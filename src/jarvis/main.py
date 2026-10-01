import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from jarvis.agent.graph import build_graph
from jarvis.audit import Audit
from jarvis.channels.telegram import TelegramChannel
from jarvis.config import get_settings
from jarvis.db import init_schema, make_pool
from jarvis.google.auth import PgTokenStore, build_service
from jarvis.google.calendar import CalendarClient
from jarvis.google.tasks import TasksClient
from jarvis.llm import LLMProvider
from jarvis.tools.calendar_tools import register_calendar_tools
from jarvis.tools.registry import Registry
from jarvis.tools.task_tools import register_task_tools

log = logging.getLogger(__name__)


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
            return lambda: build_service(name, version, store, s.fernet_key)

        registry = Registry()
        register_calendar_tools(registry, CalendarClient(svc("calendar", "v3"), s.timezone), s.timezone)
        register_task_tools(registry, TasksClient(svc("tasks", "v1")))

        async with AsyncPostgresSaver.from_conn_string(s.database_url) as saver:
            await saver.setup()
            graph = build_graph(LLMProvider(s, audit), registry, audit, saver, s.timezone)
            tg = TelegramChannel(graph, s.telegram_owner_chat_id).build(s.telegram_bot_token)
            # ponytail: PTB updates run sequentially; the pending-confirmation check in TelegramChannel assumes that, do not enable concurrent_updates
            try:
                await tg.initialize()
                try:
                    await tg.start()
                    await tg.updater.start_polling()
                    try:
                        yield
                    finally:
                        # Normal shutdown after serving
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
                except Exception:
                    # If start or start_polling failed, still try to shut down
                    try:
                        await tg.shutdown()
                    except Exception:
                        log.exception("failed to shutdown telegram app after startup failure")
                    raise
            except Exception:
                # If initialize or subsequent startup failed, still try to shut down
                try:
                    await tg.shutdown()
                except Exception:
                    log.exception("failed to shutdown telegram app after init failure")
                raise
    finally:
        pool.close()


def create_app(with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(lifespan=lifespan if with_lifespan else None)

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


app = create_app()
