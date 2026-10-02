import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from jarvis.agent.graph import build_graph
from jarvis.audit import Audit
from jarvis.channels.telegram import TelegramChannel
from jarvis.config import get_settings
from jarvis.db import init_schema, make_pool
from jarvis.google.auth import PgTokenStore, build_service
from jarvis.google.calendar import CalendarClient
from jarvis.google.gmail import GmailClient
from jarvis.google.tasks import TasksClient
from jarvis.llm import LLMProvider
from jarvis.memory import MemoryStore
from jarvis.notes import NoteStore
from jarvis.tools.calendar_tools import register_calendar_tools
from jarvis.tools.gmail_tools import register_gmail_tools
from jarvis.tools.memory_tools import register_memory_tools
from jarvis.tools.note_tools import register_note_tools
from jarvis.tools.phone_tools import register_phone_tools
from jarvis.tools.registry import Registry
from jarvis.tools.research_tools import register_research_tools
from jarvis.tools.task_tools import register_task_tools
from jarvis.web import WebSearch
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
    register_calendar_tools(registry, CalendarClient(svc("calendar", "v3"), tz), tz)
    register_task_tools(registry, TasksClient(svc("tasks", "v1")))
    register_gmail_tools(registry, GmailClient(svc("gmail", "v1")))
    register_phone_tools(registry)
    if pool is not None:
        register_memory_tools(registry, MemoryStore(pool))
        register_note_tools(registry, NoteStore(pool))
    register_research_tools(registry, WebSearch(tavily_key) if tavily_key else None)
    return registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    pool = make_pool(s.database_url)
    try:
        init_schema(pool)
        audit = Audit(pool)
        audit.purge(90)
        store = PgTokenStore(pool)

        def svc(name: str, version: str):
            return cached_service(lambda: build_service(name, version, store, s.fernet_key))

        registry = build_registry(svc, s.timezone)

        async with AsyncPostgresSaver.from_conn_string(s.database_url) as saver:
            await saver.setup()
            graph = build_graph(LLMProvider(s, audit), registry, audit, saver, s.timezone)
            # One lock for both channels on the shared thread "owner": "read pending -> decide -> ainvoke" is one step.
            # ponytail: one global lock, fine for a single owner; per-thread locks if more threads ever appear.
            lock = asyncio.Lock()
            voice = VoiceService(
                graph, Devices(pool),
                DeepgramSTT(s.deepgram_api_key) if s.deepgram_api_key else None,
                CartesiaTTS(s.cartesia_api_key, s.cartesia_voice_id) if s.cartesia_api_key and s.cartesia_voice_id else None,
                lock=lock)
            app.state.voice = voice
            tg = TelegramChannel(graph, s.telegram_owner_chat_id, deliver_actions=voice.deliver,
                                 lock=lock).build(s.telegram_bot_token)
            try:
                await tg.initialize()
            except Exception:
                # If initialize failed, still try to shut down
                try:
                    await tg.shutdown()
                except Exception:
                    log.exception("failed to shutdown telegram app after init failure")
                raise
            try:
                await tg.start()
                await tg.updater.start_polling()
                try:
                    yield
                finally:
                    pass  # Teardown happens in outer finally
            finally:
                # Ordered teardown for all paths through start/start_polling/yield
                app.state.voice = None  # a connection arriving during teardown is refused (1013) instead of reaching a closing service
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
                    pending = set(voice.steps)
                    if pending:
                        await asyncio.wait(pending, timeout=30)
                except Exception:
                    log.exception("voice steps did not drain")
    finally:
        pool.close()


def create_app(with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(lifespan=lifespan if with_lifespan else None)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.websocket("/voice")
    async def voice(ws: WebSocket):
        svc = getattr(app.state, "voice", None)
        if svc is None:  # lifespan has not built it (or voice is disabled)
            await ws.close(code=1013)
            return
        await svc.handle(ws)

    return app


app = create_app()
