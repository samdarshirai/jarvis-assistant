import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.main import create_app, lifespan


def test_health_without_lifespan():
    # lifespan wiring needs Postgres, Telegram and Google; /health must not.
    app = create_app(with_lifespan=False)
    assert TestClient(app).get("/health").json() == {"ok": True}


@pytest.fixture
def mock_lifespan_deps(monkeypatch):
    """Shared monkeypatch setup for all lifespan tests."""
    from types import SimpleNamespace

    fake_pool = MagicMock()
    call_order = []  # Track call order for assertions

    settings = SimpleNamespace(
        database_url="postgresql://test",
        timezone="UTC",
        fernet_key="test",
        tavily_api_key="",
        telegram_owner_chat_id=123,
        telegram_bot_token="test",
        deepgram_api_key="", cartesia_api_key="", cartesia_voice_id="", fcm_credentials_path="",
    )

    # Patch all dependencies to minimal no-ops
    monkeypatch.setattr("jarvis.main.get_settings", lambda: settings)
    monkeypatch.setattr("jarvis.main.make_pool", lambda url: fake_pool)
    monkeypatch.setattr("jarvis.main.init_schema", lambda pool: None)

    mock_audit = MagicMock()
    mock_audit.purge = MagicMock()
    monkeypatch.setattr("jarvis.main.Audit", lambda pool: mock_audit)

    monkeypatch.setattr("jarvis.main.PgTokenStore", lambda pool: MagicMock())
    monkeypatch.setattr(
        "jarvis.main.build_service", lambda name, ver, store, key: MagicMock()
    )
    monkeypatch.setattr("jarvis.main.CalendarClient", lambda svc, tz: MagicMock())
    monkeypatch.setattr("jarvis.main.TasksClient", lambda svc: MagicMock())
    monkeypatch.setattr("jarvis.main.LLMProvider", lambda s, audit: MagicMock())
    monkeypatch.setattr("jarvis.main.register_calendar_tools", lambda *args: None)
    monkeypatch.setattr("jarvis.main.register_task_tools", lambda *args: None)
    monkeypatch.setattr("jarvis.main.register_gmail_tools", lambda *args: None)
    monkeypatch.setattr("jarvis.main.Registry", lambda: MagicMock())
    monkeypatch.setattr("jarvis.main.ProactiveStore", lambda pool: MagicMock())
    monkeypatch.setattr("jarvis.main.start_proactive", lambda *a, **k: MagicMock())

    # Mock AsyncPostgresSaver
    mock_saver = AsyncMock()
    mock_saver.setup = AsyncMock()
    mock_saver_ctx = AsyncMock()
    mock_saver_ctx.__aenter__ = AsyncMock(return_value=mock_saver)
    mock_saver_ctx.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "jarvis.main.AsyncPostgresSaver.from_conn_string",
        lambda url: mock_saver_ctx,
    )

    return {
        "fake_pool": fake_pool,
        "call_order": call_order,
        "monkeypatch": monkeypatch,
    }


@pytest.mark.asyncio
async def test_lifespan_closes_pool_on_graph_build_failure(monkeypatch):
    """Test that pool is closed even if build_graph raises."""
    from types import SimpleNamespace

    fake_pool = MagicMock()

    settings = SimpleNamespace(
        database_url="postgresql://test",
        timezone="UTC",
        fernet_key="test",
        tavily_api_key="",
        telegram_owner_chat_id=123,
        telegram_bot_token="test",
        deepgram_api_key="", cartesia_api_key="", cartesia_voice_id="", fcm_credentials_path="",
    )

    # Patch all dependencies to minimal no-ops
    monkeypatch.setattr("jarvis.main.get_settings", lambda: settings)
    monkeypatch.setattr("jarvis.main.make_pool", lambda url: fake_pool)
    monkeypatch.setattr("jarvis.main.init_schema", lambda pool: None)

    mock_audit = MagicMock()
    mock_audit.purge = MagicMock()
    monkeypatch.setattr("jarvis.main.Audit", lambda pool: mock_audit)

    monkeypatch.setattr("jarvis.main.PgTokenStore", lambda pool: MagicMock())
    monkeypatch.setattr(
        "jarvis.main.build_service", lambda name, ver, store, key: MagicMock()
    )
    monkeypatch.setattr("jarvis.main.CalendarClient", lambda svc, tz: MagicMock())
    monkeypatch.setattr("jarvis.main.TasksClient", lambda svc: MagicMock())
    monkeypatch.setattr("jarvis.main.LLMProvider", lambda s, audit: MagicMock())
    monkeypatch.setattr("jarvis.main.register_calendar_tools", lambda *args: None)
    monkeypatch.setattr("jarvis.main.register_task_tools", lambda *args: None)
    monkeypatch.setattr("jarvis.main.register_gmail_tools", lambda *args: None)
    monkeypatch.setattr("jarvis.main.Registry", lambda: MagicMock())
    monkeypatch.setattr("jarvis.main.ProactiveStore", lambda pool: MagicMock())
    monkeypatch.setattr("jarvis.main.start_proactive", lambda *a, **k: MagicMock())

    # Make build_graph raise
    def mock_build_graph(*args, **kwargs):
        raise ValueError("graph build failed")

    monkeypatch.setattr("jarvis.main.build_graph", mock_build_graph)

    # Mock AsyncPostgresSaver
    mock_saver = AsyncMock()
    mock_saver.setup = AsyncMock()
    mock_saver_ctx = AsyncMock()
    mock_saver_ctx.__aenter__ = AsyncMock(return_value=mock_saver)
    mock_saver_ctx.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "jarvis.main.AsyncPostgresSaver.from_conn_string",
        lambda url: mock_saver_ctx,
    )

    app = FastAPI()

    with pytest.raises(ValueError, match="graph build failed"):
        async with lifespan(app):
            pass

    # Verify pool.close() was called exactly once
    fake_pool.close.assert_called_once()


@pytest.mark.asyncio
async def test_lifespan_initialize_failure_calls_shutdown_and_closes_pool(
    mock_lifespan_deps,
):
    """Test that pool is closed and tg.shutdown() is called once if initialize fails."""
    fake_pool = mock_lifespan_deps["fake_pool"]
    monkeypatch = mock_lifespan_deps["monkeypatch"]

    # Mock telegram app with initialize that raises
    fake_tg_app = AsyncMock()
    fake_tg_app.initialize = AsyncMock(side_effect=ValueError("telegram init failed"))
    fake_tg_app.shutdown = AsyncMock()
    fake_tg_app.updater = AsyncMock()
    fake_graph = MagicMock()

    monkeypatch.setattr("jarvis.main.build_graph", lambda *args, **kwargs: fake_graph)

    # Mock TelegramChannel to return fake_tg_app
    mock_tg_channel = MagicMock()
    mock_tg_channel.build = MagicMock(return_value=fake_tg_app)
    monkeypatch.setattr("jarvis.main.TelegramChannel", lambda *args, **kw: mock_tg_channel)

    app = FastAPI()

    with pytest.raises(ValueError, match="telegram init failed"):
        async with lifespan(app):
            pass

    # Verify pool.close() was called exactly once
    fake_pool.close.assert_called_once()

    # Verify shutdown was called exactly once
    fake_tg_app.shutdown.assert_called_once()

    # Verify no stop calls
    fake_tg_app.updater.stop.assert_not_called()
    fake_tg_app.stop.assert_not_called()


@pytest.mark.asyncio
async def test_lifespan_start_polling_failure_calls_teardown_in_order(
    mock_lifespan_deps,
):
    """Test that start_polling failure triggers ordered teardown: stop_updater, stop, shutdown."""
    fake_pool = mock_lifespan_deps["fake_pool"]
    call_order = mock_lifespan_deps["call_order"]
    monkeypatch = mock_lifespan_deps["monkeypatch"]

    # Mock telegram app
    fake_tg_app = AsyncMock()
    fake_graph = MagicMock()
    fake_updater = AsyncMock()
    fake_updater.running = True
    fake_tg_app.updater = fake_updater
    fake_tg_app.running = True

    # Track calls in order
    async def track_stop_updater():
        call_order.append("stop_updater")

    async def track_stop():
        call_order.append("stop")

    async def track_shutdown():
        call_order.append("shutdown")

    fake_updater.stop = AsyncMock(side_effect=track_stop_updater)
    fake_tg_app.stop = AsyncMock(side_effect=track_stop)
    fake_tg_app.shutdown = AsyncMock(side_effect=track_shutdown)

    # Make start_polling raise
    async def mock_start_polling():
        raise ValueError("start_polling failed")

    fake_updater.start_polling = mock_start_polling

    monkeypatch.setattr("jarvis.main.build_graph", lambda *args, **kwargs: fake_graph)

    # Mock TelegramChannel to return fake_tg_app
    mock_tg_channel = MagicMock()
    mock_tg_channel.build = MagicMock(return_value=fake_tg_app)
    monkeypatch.setattr("jarvis.main.TelegramChannel", lambda *args, **kw: mock_tg_channel)

    app = FastAPI()

    with pytest.raises(ValueError, match="start_polling failed"):
        async with lifespan(app):
            pass

    # Verify call order: stop_updater -> stop -> shutdown
    assert call_order == [
        "stop_updater",
        "stop",
        "shutdown",
    ], f"Got: {call_order}"

    # Verify pool.close() was called exactly once
    fake_pool.close.assert_called_once()

    # Verify shutdown was called exactly once
    fake_tg_app.shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_lifespan_normal_path_calls_teardown_in_order(mock_lifespan_deps):
    """Test normal serving path calls teardown in order."""
    fake_pool = mock_lifespan_deps["fake_pool"]
    call_order = mock_lifespan_deps["call_order"]
    monkeypatch = mock_lifespan_deps["monkeypatch"]

    # Mock telegram app
    fake_tg_app = AsyncMock()
    fake_graph = MagicMock()
    fake_updater = AsyncMock()
    fake_updater.running = True
    fake_tg_app.updater = fake_updater
    fake_tg_app.running = True

    # Track calls in order
    async def track_stop_updater():
        call_order.append("stop_updater")

    async def track_stop():
        call_order.append("stop")

    async def track_shutdown():
        call_order.append("shutdown")

    fake_updater.stop = AsyncMock(side_effect=track_stop_updater)
    fake_tg_app.stop = AsyncMock(side_effect=track_stop)
    fake_tg_app.shutdown = AsyncMock(side_effect=track_shutdown)

    monkeypatch.setattr("jarvis.main.build_graph", lambda *args, **kwargs: fake_graph)

    # Mock TelegramChannel to return fake_tg_app
    mock_tg_channel = MagicMock()
    mock_tg_channel.build = MagicMock(return_value=fake_tg_app)
    monkeypatch.setattr("jarvis.main.TelegramChannel", lambda *args, **kw: mock_tg_channel)

    app = FastAPI()

    # Normal serving
    async with lifespan(app):
        pass

    # Verify call order: stop_updater -> stop -> shutdown -> pool.close
    assert call_order == ["stop_updater", "stop", "shutdown"], f"Got: {call_order}"

    # Verify pool.close() was called exactly once
    fake_pool.close.assert_called_once()

    # Verify shutdown was called exactly once
    fake_tg_app.shutdown.assert_called_once()


def test_voice_route_is_closed_until_the_lifespan_has_built_the_service():
    from starlette.websockets import WebSocketDisconnect
    app = create_app(with_lifespan=False)
    with pytest.raises(WebSocketDisconnect) as e:
        with TestClient(app).websocket_connect("/voice"):
            pass
    assert e.value.code == 1013


@pytest.mark.asyncio
async def test_lifespan_refuses_voice_during_teardown_and_drains_abandoned_steps(mock_lifespan_deps):
    import asyncio

    monkeypatch = mock_lifespan_deps["monkeypatch"]
    fake_tg_app = AsyncMock()
    fake_tg_app.updater = AsyncMock()
    fake_tg_app.updater.running = True
    fake_tg_app.running = True
    monkeypatch.setattr("jarvis.main.build_graph", lambda *a, **k: MagicMock())
    channel = MagicMock()
    channel.build = MagicMock(return_value=fake_tg_app)
    monkeypatch.setattr("jarvis.main.TelegramChannel", lambda *a, **kw: channel)

    app = FastAPI()
    done = []

    async def abandoned_step():  # a graph step a cancelled voice turn left running (tool in flight)
        await asyncio.sleep(0.2)
        done.append("audited")

    async with lifespan(app):
        assert app.state.voice is not None
        t = asyncio.create_task(abandoned_step())
        app.state.voice.steps.add(t)
    assert app.state.voice is None  # new connections get 1013 while the app shuts down
    assert done == ["audited"]  # teardown waited for the step before the saver and pool closed


def test_cached_service_reuses_then_rebuilds_after_ttl():
    from jarvis.main import cached_service
    now, n = [0.0], [0]

    def build():
        n[0] += 1
        return n[0]

    get = cached_service(build, ttl=10, clock=lambda: now[0])
    assert get() == get() == 1
    now[0] = 11
    assert get() == 2


@pytest.mark.asyncio
async def test_lifespan_starts_the_scheduler_after_polling_and_stops_it_first(mock_lifespan_deps):
    call_order = mock_lifespan_deps["call_order"]
    monkeypatch = mock_lifespan_deps["monkeypatch"]
    fake_tg_app = AsyncMock()
    fake_tg_app.updater = AsyncMock()
    fake_tg_app.updater.running = True
    fake_tg_app.running = True
    fake_tg_app.updater.stop = AsyncMock(side_effect=lambda: call_order.append("stop_updater"))
    fake_tg_app.updater.start_polling = AsyncMock(side_effect=lambda: call_order.append("start_polling"))
    sched = MagicMock()
    sched.shutdown = MagicMock(side_effect=lambda wait=True: call_order.append(f"scheduler_shutdown(wait={wait})"))

    def start(*args, **kwargs):
        call_order.append("scheduler_start")
        return sched

    monkeypatch.setattr("jarvis.main.start_proactive", start)
    monkeypatch.setattr("jarvis.main.build_graph", lambda *a, **k: MagicMock())
    channel = MagicMock()
    channel.build = MagicMock(return_value=fake_tg_app)
    monkeypatch.setattr("jarvis.main.TelegramChannel", lambda *a, **kw: channel)

    async with lifespan(FastAPI()):
        pass

    assert call_order == ["start_polling", "scheduler_start", "scheduler_shutdown(wait=False)", "stop_updater"]


class _Dash:
    def __init__(self, ok=True):
        self.ok, self.seen = ok, None

    async def authorized(self, header):
        self.seen = header
        return self.ok

    async def payload(self):
        return {"events": [], "reauth": False}


def test_dashboard_is_503_until_the_service_is_built():
    assert TestClient(create_app(with_lifespan=False)).get("/dashboard").status_code == 503


def test_dashboard_rejects_a_bad_token():
    app = create_app(with_lifespan=False)
    app.state.dashboard = _Dash(ok=False)
    assert TestClient(app).get("/dashboard", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert TestClient(app).get("/dashboard").status_code == 401


def test_dashboard_returns_the_payload_for_a_paired_device():
    app = create_app(with_lifespan=False)
    dash = app.state.dashboard = _Dash()
    r = TestClient(app).get("/dashboard", headers={"Authorization": "Bearer good"})
    assert r.status_code == 200 and r.json() == {"events": [], "reauth": False}
    assert dash.seen == "Bearer good"
