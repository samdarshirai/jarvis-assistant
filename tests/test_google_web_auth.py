import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from jarvis.google import web_auth
from jarvis.main import create_app

REDIRECT = "https://jarvis.example.com/google/callback"


@pytest.fixture
def key():
    return Fernet.generate_key().decode()


@pytest.fixture
def secrets(tmp_path):
    p = tmp_path / "web.json"
    p.write_text(json.dumps({"web": {
        "client_id": "cid", "client_secret": "sec", "redirect_uris": [REDIRECT],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}))
    return str(p)


def settings(key, secrets):
    return SimpleNamespace(fernet_key=key, google_web_client_secrets=secrets, public_base_url="https://jarvis.example.com/")


class MemStore:
    def __init__(self):
        self.blobs = {}

    def put(self, provider, blob):
        self.blobs[provider] = blob

    def get(self, provider):
        return self.blobs.get(provider)


class Svc:
    def __init__(self, ok=True):
        self.ok = ok

    async def authorized(self, header):
        return self.ok


def client(monkeypatch, key, secrets, ok=True, store=None):
    monkeypatch.setattr("jarvis.main.get_settings", lambda: settings(key, secrets))
    app = create_app(with_lifespan=False)
    app.state.app_api = Svc(ok)
    app.state.google_store = store
    return TestClient(app)


def test_state_roundtrip_and_tamper(key):
    s = web_auth.make_state(key)
    assert web_auth.state_ok(key, s)
    assert not web_auth.state_ok(key, "garbage")
    assert not web_auth.state_ok(Fernet.generate_key().decode(), s)


def test_start_needs_a_paired_device(monkeypatch, key, secrets):
    assert client(monkeypatch, key, secrets, ok=False).post("/google/auth/start").status_code == 401


def test_start_returns_offline_consent_url(monkeypatch, key, secrets):
    r = client(monkeypatch, key, secrets).post("/google/auth/start", headers={"Authorization": "Bearer x"})
    url = r.json()["url"]
    assert r.status_code == 200 and url.startswith("https://accounts.google.com/")
    assert "access_type=offline" in url and "prompt=consent" in url and "code_challenge" not in url
    assert "redirect_uri=https%3A%2F%2Fjarvis.example.com%2Fgoogle%2Fcallback" in url


def test_start_503_when_web_client_not_configured(monkeypatch, key):
    assert client(monkeypatch, key, "").post("/google/auth/start", headers={"Authorization": "Bearer x"}).status_code == 503


def test_callback_rejects_bad_state_and_stores_nothing(monkeypatch, key, secrets):
    store = MemStore()
    r = client(monkeypatch, key, secrets, store=store).get("/google/callback", params={"code": "c", "state": "forged"})
    assert r.status_code == 400 and not store.blobs


def test_callback_stores_encrypted_credentials_and_bumps_epoch(monkeypatch, key, secrets):
    from jarvis.google import auth

    class Creds:
        def to_json(self):
            return json.dumps({"refresh_token": "rt"})

    monkeypatch.setattr(web_auth, "exchange", lambda path, redirect, code: Creds() if (code, redirect) == ("good", REDIRECT) else 1 / 0)
    store, before = MemStore(), auth.current_epoch()
    r = client(monkeypatch, key, secrets, store=store).get(
        "/google/callback", params={"code": "good", "state": web_auth.make_state(key)})
    assert r.status_code == 200 and "connected" in r.text
    assert json.loads(Fernet(key).decrypt(store.blobs["google"])) == {"refresh_token": "rt"}
    assert auth.current_epoch() == before + 1


def test_callback_exchange_failure_is_502(monkeypatch, key, secrets):
    def boom(*a):
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr(web_auth, "exchange", boom)
    r = client(monkeypatch, key, secrets, store=MemStore()).get(
        "/google/callback", params={"code": "c", "state": web_auth.make_state(key)})
    assert r.status_code == 502


def test_cached_service_rebuilds_after_new_consent():
    from jarvis.google import auth
    from jarvis.main import cached_service

    built = []
    get = cached_service(lambda: built.append(1) or len(built))
    assert get() == get() == 1
    auth.bump_epoch()
    assert get() == 2


def test_start_url_accepts_the_client_json_inline(secrets):
    url = web_auth.start_url(open(secrets).read(), REDIRECT, "s")
    assert url.startswith("https://accounts.google.com/") and "client_id=cid" in url
