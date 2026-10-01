import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.main import create_app, lifespan


def test_health_without_lifespan():
    # lifespan wiring needs Postgres, Telegram and Google; /health must not.
    app = create_app(with_lifespan=False)
    assert TestClient(app).get("/health").json() == {"ok": True}


@pytest.mark.asyncio
async def test_lifespan_closes_pool_on_graph_build_failure(monkeypatch):
    """Test that pool is closed even if build_graph raises."""
    from types import SimpleNamespace

    fake_pool = MagicMock()

    settings = SimpleNamespace(
        database_url="postgresql://test",
        timezone="UTC",
        fernet_key="test",
        telegram_owner_chat_id=123,
        telegram_bot_token="test",
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
    monkeypatch.setattr("jarvis.main.Registry", lambda: MagicMock())

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
async def test_lifespan_closes_pool_and_calls_shutdown_on_tg_init_failure(
    monkeypatch,
):
    """Test that pool is closed and tg.shutdown() is called even if initialize fails."""
    from types import SimpleNamespace

    fake_pool = MagicMock()

    # Mock telegram app with initialize that raises
    fake_tg_app = AsyncMock()
    fake_tg_app.initialize = AsyncMock(side_effect=ValueError("telegram init failed"))
    fake_tg_app.shutdown = AsyncMock()

    fake_graph = MagicMock()

    settings = SimpleNamespace(
        database_url="postgresql://test",
        timezone="UTC",
        fernet_key="test",
        telegram_owner_chat_id=123,
        telegram_bot_token="test",
    )

    # Patch all dependencies
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
    monkeypatch.setattr("jarvis.main.Registry", lambda: MagicMock())
    monkeypatch.setattr("jarvis.main.build_graph", lambda *args, **kwargs: fake_graph)

    # Mock TelegramChannel to return fake_tg_app
    mock_tg_channel = MagicMock()
    mock_tg_channel.build = MagicMock(return_value=fake_tg_app)
    monkeypatch.setattr("jarvis.main.TelegramChannel", lambda *args: mock_tg_channel)

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

    with pytest.raises(ValueError, match="telegram init failed"):
        async with lifespan(app):
            pass

    # Verify pool.close() was called exactly once
    fake_pool.close.assert_called_once()

    # Verify shutdown was called
    fake_tg_app.shutdown.assert_called_once()
