import asyncio
import logging
from contextlib import asynccontextmanager
from functools import partial
from types import SimpleNamespace

from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, StringConstraints
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from jarvis.appapi import AppService
from jarvis.audit import purge_all as purge_audit
from jarvis.channels.telegram import TelegramChannel
from jarvis.config import get_settings
from jarvis.dashboard import DashboardService
from jarvis.db import init_schema, make_pool
from jarvis.google import web_auth
from jarvis.google.auth import PgTokenStore, save_credentials
from jarvis.proactive.mailwatch import undo_event
from jarvis.proactive.scheduler import start_proactive
from jarvis.proactive.store import purge_all as purge_proactive
from jarvis.tenant import Tenants
from jarvis.timeutil import now_local
from jarvis.users import ensure_owner
from jarvis.voice.devices import Devices
from jarvis.voice.stt import DeepgramSTT
from jarvis.voice.tts import DeepgramTTS
from jarvis.voice.ws import VoiceService

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    pool = make_pool(s.database_url)
    try:
        init_schema(pool)
        # Owner-only wiring until per-user channels land: everything below runs as the owner's runtime.
        owner_id = ensure_owner(pool, s)
        if owner_id is None:
            raise RuntimeError("No owner yet: set JARVIS_OWNER_EMAIL to the owner's Google email.")
        purge_audit(pool, 90)
        purge_proactive(pool, 90)
        devices = Devices(pool)

        async with AsyncPostgresSaver.from_conn_string(s.database_url) as saver:
            await saver.setup()
            tenants = Tenants(pool, s, saver)
            try:
                # No LLM key yet is fine: owner.graph is None and chat fails until the owner sets one.
                owner = await tenants.get(owner_id)

                async def undo(event_id: str) -> bool:
                    return await undo_event(owner.calendar, owner.proactive, owner.audit, event_id)

                # One per-user lock for every channel on the owner's thread: "read pending -> decide -> ainvoke" is one step.
                voice = VoiceService(
                    owner.graph, devices,
                    DeepgramSTT(s.deepgram_api_key) if s.deepgram_api_key else None,
                    DeepgramTTS(s.deepgram_api_key) if s.deepgram_api_key else None,
                    lock=owner.lock, user_id=owner_id)
                app.state.voice = voice
                app.state.google_store = PgTokenStore(pool, owner_id)
                app.state.tenants, app.state.owner_id = tenants, owner_id
                app.state.dashboard = DashboardService(devices, owner.calendar, owner.tasks, owner.gmail, owner.notes,
                                                       owner.tz, weather_city=s.weather_city, store=owner.proactive)
                app.state.app_api = app_api = AppService(devices, owner.calendar, owner.tasks, owner.gmail, owner.notes,
                                                         owner.graph, owner.lock, owner.audit, owner.tz, user_id=owner_id)
                tg = TelegramChannel(owner.graph, s.telegram_owner_chat_id, deliver_actions=voice.deliver,
                                     lock=owner.lock, undo=undo, user_id=owner_id).build(s.telegram_bot_token)
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
                    owner_devices = SimpleNamespace(fcm_tokens=partial(devices.fcm_tokens, owner_id))  # until per-user notifiers
                    sched = start_proactive(s, owner.proactive, owner.calendar, owner.tasks, owner.gmail, owner.llm,
                                            owner.audit, tg.bot, owner_devices)
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
                    app.state.google_store = None
                    app.state.tenants = None
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
                await tenants.close()  # after the steps drained: closes each runtime's LLM http client
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


def _consent_page(message: str, status: int) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<body style="font-family:sans-serif;padding:2rem;text-align:center"><h2>Jarvis</h2><p>{message}</p>',
        status_code=status)


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

    def google_redirect(s) -> str | None:
        return s.public_base_url.rstrip("/") + "/google/callback" if s.google_web_client_secrets and s.public_base_url else None

    @app.post("/google/auth/start")
    async def google_auth_start(request: Request):
        svc, err = await guarded(request)
        if err:
            return err
        s = get_settings()
        redirect = google_redirect(s)
        if redirect is None:
            return JSONResponse({"error": "not_configured"}, status_code=503)
        url = await asyncio.to_thread(web_auth.start_url, s.google_web_client_secrets, redirect, web_auth.make_state(s.fernet_key))
        return {"url": url}

    @app.get("/google/callback", response_class=HTMLResponse)
    async def google_callback(code: str = "", state: str = "", error: str = ""):
        s = get_settings()
        store = getattr(app.state, "google_store", None)
        redirect = google_redirect(s)
        if store is None or redirect is None:
            return _consent_page("Jarvis is not ready to connect Google yet.", 503)
        if error or not code or not web_auth.state_ok(s.fernet_key, state):
            return _consent_page("That Google link is invalid or expired. Open Jarvis and tap Reconnect Google again.", 400)
        try:
            creds = await asyncio.to_thread(web_auth.exchange, s.google_web_client_secrets, redirect, code)
            await asyncio.to_thread(save_credentials, store, s.fernet_key, creds)
        except Exception:
            log.exception("google consent exchange failed")
            return _consent_page("Google did not accept the sign-in. Open Jarvis and try again.", 502)
        tenants = getattr(app.state, "tenants", None)
        if tenants is not None:
            tenants.invalidate(app.state.owner_id)  # rebuild the owner's Google clients on the new consent
        return _consent_page("Google is connected. You can return to Jarvis.", 200)

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
