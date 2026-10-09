import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager

from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from jarvis.agent.graph import build_graph
from jarvis.appapi import AppService
from jarvis.audit import Audit
from jarvis.channels.telegram import TelegramChannel
from jarvis.config import get_settings
from jarvis.dashboard import DashboardService
from jarvis.db import init_schema, make_pool
from jarvis.google.auth import PgTokenStore, build_service
from jarvis.google.calendar import CalendarClient
from jarvis.google.gmail import GmailClient
from jarvis.google.tasks import TasksClient
from jarvis.llm import LLMProvider
from jarvis.memory import MemoryStore
from jarvis.notes import NoteStore
from jarvis.proactive.mailwatch import undo_event
from jarvis.proactive.scheduler import start_proactive
from jarvis.proactive.store import ProactiveStore
from jarvis.timeutil import now_local
from jarvis.tools.calendar_tools import register_calendar_tools
from jarvis.tools.gmail_tools import register_gmail_tools
from jarvis.tools.memory_tools import register_memory_tools
from jarvis.tools.note_tools import register_note_tools
from jarvis.tools.phone_tools import register_phone_tools
from jarvis.tools.registry import Registry
from jarvis.tools.research_tools import register_research_tools
from jarvis.tools.task_tools import register_task_tools
from jarvis.web import WebSearch, DdgSearch
from jarvis.voice.devices import Devices
from jarvis.voice.stt import DeepgramSTT
from jarvis.voice.tts import CartesiaTTS
from jarvis.voice.ws import VoiceService

log = logging.getLogger(__name__)


def cached_service(build, ttl: float = 1800.0, clock=time.monotonic):
    """Reuse the built Google client per thread (httplib2 is not thread-safe); rebuild after ttl so token
    refresh and ReauthRequired still surface. Tools run in to_thread, hence the thread-local."""
    local = threading.local()

    def get():
        hit = getattr(local, "hit", None)
        if hit is None or clock() - hit[0] > ttl:
            hit = local.hit = (clock(), build())
        return hit[1]

    return get


def build_registry(svc, tz: str, pool=None, tavily_key: str = "") -> Registry:
    registry = Registry()
    gmail = GmailClient(svc("gmail", "v1"))
    register_calendar_tools(registry, CalendarClient(svc("calendar", "v3"), tz), tz, gmail.sent_to)
    register_task_tools(registry, TasksClient(svc("tasks", "v1")))
    register_gmail_tools(registry, gmail)
    register_phone_tools(registry)
    if pool is not None:
        register_memory_tools(registry, MemoryStore(pool))
        register_note_tools(registry, NoteStore(pool))
    register_research_tools(registry, WebSearch(tavily_key) if tavily_key else DdgSearch())
    return registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    pool = make_pool(s.database_url)
    try:
        init_schema(pool)
        audit = Audit(pool)
        audit.purge(90)
        proactive_store = ProactiveStore(pool)
        proactive_store.purge(90)
        store = PgTokenStore(pool)

        def svc(name: str, version: str):
            return cached_service(lambda: build_service(name, version, store, s.fernet_key))

        registry = build_registry(svc, s.timezone, pool, s.tavily_api_key)
        calendar = CalendarClient(svc("calendar", "v3"), s.timezone)
        tasks_client = TasksClient(svc("tasks", "v1"))
        gmail = GmailClient(svc("gmail", "v1"))
        llm = LLMProvider(s, audit)
        devices = Devices(pool)

        async def undo(event_id: str) -> bool:
            return await undo_event(calendar, proactive_store, audit, event_id)

        async with AsyncPostgresSaver.from_conn_string(s.database_url) as saver:
            await saver.setup()
            graph = build_graph(llm, registry, audit, saver, s.timezone,
                                memories=MemoryStore(pool).all)
            # One lock for both channels on the shared thread "owner": "read pending -> decide -> ainvoke" is one step.
            # ponytail: one global lock, fine for a single owner; per-thread locks if more threads ever appear.
            lock = asyncio.Lock()
            voice = VoiceService(
                graph, devices,
                DeepgramSTT(s.deepgram_api_key) if s.deepgram_api_key else None,
                CartesiaTTS(s.cartesia_api_key, s.cartesia_voice_id) if s.cartesia_api_key and s.cartesia_voice_id else None,
                lock=lock)
            app.state.voice = voice
            app.state.dashboard = DashboardService(devices, calendar, tasks_client, gmail, NoteStore(pool), s.timezone, weather_city=s.weather_city, store=proactive_store)
            app.state.app_api = app_api = AppService(devices, calendar, tasks_client, gmail, NoteStore(pool), graph,
                                                     lock, audit, s.timezone)
            tg = TelegramChannel(graph, s.telegram_owner_chat_id, deliver_actions=voice.deliver,
                                 lock=lock, undo=undo).build(s.telegram_bot_token)
            try:
                await tg.initialize()
            except Exception:
                # If initialize failed, still try to shut down
                try:
                    await tg.shutdown()
                except Exception:
                    log.exception("failed to shutdown telegram app after init failure")
                raise
            sched = None
            try:
                await tg.start()
                await tg.updater.start_polling()
                sched = start_proactive(s, proactive_store, calendar, tasks_client, gmail, llm, audit, tg.bot, devices)
                try:
                    yield
                finally:
                    pass  # Teardown happens in outer finally
            finally:
                # Ordered teardown for all paths through start/start_polling/yield
                if sched is not None:
                    sched.shutdown(wait=False)  # stop new jobs before the channels and pool go away
                app.state.voice = None  # a connection arriving during teardown is refused (1013) instead of reaching a closing service
                app.state.dashboard = None
                app.state.app_api = None
                try:
                    if tg.updater.running:
                        await tg.updater.stop()
                except Exception:
                    log.exception("failed to stop updater")
                try:
                    if tg.running:
                        await tg.stop()
                except Exception:
                    log.exception("failed to stop telegram app")
                try:
                    await tg.shutdown()
                except Exception:
                    log.exception("failed to shutdown telegram app")
                # A graph step an abandoned voice turn left running must finish (audit row + checkpoint) before the
                # saver and pool close. asyncio.wait does not cancel on timeout, unlike wait_for(gather(...)).
                try:
                    pending = set(voice.steps) | set(app_api.steps)
                    if pending:
                        await asyncio.wait(pending, timeout=30)
                except Exception:
                    log.exception("voice steps did not drain")
    finally:
        pool.close()


class TaskIn(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    due: date | None = None


class ChatIn(BaseModel):
    text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class ConfirmIn(BaseModel):
    interrupt_id: str
    decision: Literal["yes", "no"]


def create_app(with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(lifespan=lifespan if with_lifespan else None)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/dashboard")
    async def dashboard(request: Request):
        svc = getattr(app.state, "dashboard", None)
        if svc is None:  # lifespan has not built it yet
            return JSONResponse({"error": "unavailable"}, status_code=503)
        if not await svc.authorized(request.headers.get("authorization", "")):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            lat, lon = float(request.query_params["lat"]), float(request.query_params["lon"])
            coords = (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None
        except (KeyError, ValueError):
            coords = None
        return await svc.payload(coords=coords)

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "bad_request"}, status_code=422)

    async def guarded(request: Request):
        """(service, error response): 503 until the lifespan built it, 401 on a bad device token."""
        svc = getattr(app.state, "app_api", None)
        if svc is None:
            return None, JSONResponse({"error": "unavailable"}, status_code=503)
        if not await svc.authorized(request.headers.get("authorization", "")):
            return None, JSONResponse({"error": "unauthorized"}, status_code=401)
        return svc, None

    def found(body):
        return JSONResponse({"error": "not_found"}, status_code=404) if body is None else body

    @app.get("/calendar")
    async def calendar(request: Request, days: int = 7):
        svc, err = await guarded(request)
        if err:
            return err
        try:
            start = date.fromisoformat(request.query_params["from"]) if "from" in request.query_params else None
        except ValueError:
            return JSONResponse({"error": "bad_request"}, status_code=422)
        return await svc.calendar_events(start or now_local(svc.tz).date(), days)

    @app.get("/tasks")
    async def tasks(request: Request):
        svc, err = await guarded(request)
        return err or await svc.task_list()

    @app.post("/tasks")
    async def add_task(request: Request, body: TaskIn):
        svc, err = await guarded(request)
        if err:
            return err
        task = await svc.create_task(body.title, body.due)
        return JSONResponse({"error": "unavailable"}, status_code=502) if task is None else {"task": task}

    @app.post("/tasks/{task_id}/complete")
    async def complete_task(request: Request, task_id: str):
        svc, err = await guarded(request)
        if err:
            return err
        if not await svc.complete_task(task_id):
            return JSONResponse({"error": "unavailable"}, status_code=502)
        return {"ok": True}

    @app.get("/mail")
    async def mail(request: Request, limit: int = 20):
        svc, err = await guarded(request)
        return err or await svc.mail_list(limit)

    @app.get("/mail/{message_id}")
    async def mail_item(request: Request, message_id: str):
        svc, err = await guarded(request)
        return err or found(await svc.mail_item(message_id))

    @app.get("/notes")
    async def notes(request: Request):
        svc, err = await guarded(request)
        return err or await svc.note_list()

    @app.get("/notes/{note_id}")
    async def note(request: Request, note_id: int):
        svc, err = await guarded(request)
        return err or found(await svc.note_item(note_id))

    @app.post("/chat")
    async def chat(request: Request, body: ChatIn):
        svc, err = await guarded(request)
        return err or await svc.chat(body.text)

    @app.post("/chat/confirm")
    async def chat_confirm(request: Request, body: ConfirmIn):
        svc, err = await guarded(request)
        return err or await svc.confirm(body.interrupt_id, body.decision)

    @app.websocket("/voice")
    async def voice(ws: WebSocket):
        svc = getattr(app.state, "voice", None)
        if svc is None:  # lifespan has not built it (or voice is disabled)
            await ws.close(code=1013)
            return
        await svc.handle(ws)

    return app


app = create_app()
