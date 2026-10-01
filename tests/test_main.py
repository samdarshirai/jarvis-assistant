from fastapi.testclient import TestClient

from jarvis.main import create_app


def test_health_without_lifespan():
    # lifespan wiring needs Postgres, Telegram and Google; /health must not.
    app = create_app(with_lifespan=False)
    assert TestClient(app).get("/health").json() == {"ok": True}
