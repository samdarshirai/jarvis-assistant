import json

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError

from jarvis.google import auth


class MemStore:
    def __init__(self):
        self.d = {}

    def get(self, p):
        return self.d.get(p)

    def put(self, p, b):
        self.d[p] = b


class StubCreds:
    valid = True

    def __init__(self, info):
        self.info = info
        if info.get("expired"):
            self.valid = False

    @classmethod
    def from_authorized_user_info(cls, info, scopes):
        return cls(info)

    def refresh(self, request):
        if self.info.get("revoked"):
            raise RefreshError("revoked")
        self.info["refreshed"] = True
        self.valid = True

    def to_json(self):
        return json.dumps(self.info)


@pytest.fixture
def key():
    return Fernet.generate_key().decode()


def test_missing_token_requires_reauth(key):
    with pytest.raises(auth.ReauthRequired):
        auth.load_credentials(MemStore(), key)


def test_token_encrypted_at_rest_and_roundtrips(key, monkeypatch):
    monkeypatch.setattr(auth, "Credentials", StubCreds)
    store = MemStore()
    auth.save_credentials(store, key, StubCreds({"refresh_token": "secret-rt"}))
    assert b"secret-rt" not in store.d["google"]
    assert auth.load_credentials(store, key).info["refresh_token"] == "secret-rt"


def test_revoked_refresh_requires_reauth(key, monkeypatch):
    monkeypatch.setattr(auth, "Credentials", StubCreds)
    store = MemStore()
    auth.save_credentials(store, key, StubCreds({"expired": True, "revoked": True}))
    with pytest.raises(auth.ReauthRequired):
        auth.load_credentials(store, key)


def test_scopes_are_calendar_tasks_and_gmail_only():
    assert sorted(s.rsplit("/", 1)[1] for s in auth.SCOPES) == [
        "calendar", "gmail.compose", "gmail.readonly", "tasks"]
    assert all(s.startswith("https://www.googleapis.com/auth/") for s in auth.SCOPES)


def test_rotated_key_requires_reauth(key, monkeypatch):
    monkeypatch.setattr(auth, "Credentials", StubCreds)
    store = MemStore()
    auth.save_credentials(store, key, StubCreds({"refresh_token": "secret-rt"}))
    wrong_key = Fernet.generate_key().decode()
    with pytest.raises(auth.ReauthRequired):
        auth.load_credentials(store, wrong_key)


def test_corrupt_blob_requires_reauth(key, monkeypatch):
    monkeypatch.setattr(auth, "Credentials", StubCreds)
    store = MemStore()
    store.put("google", b"not-valid-fernet-data")
    with pytest.raises(auth.ReauthRequired):
        auth.load_credentials(store, key)


def test_refresh_success_resaves_token(key, monkeypatch):
    monkeypatch.setattr(auth, "Credentials", StubCreds)
    store = MemStore()
    auth.save_credentials(store, key, StubCreds({"expired": True, "refresh_token": "rt"}))
    creds = auth.load_credentials(store, key)
    assert creds.valid
    assert creds.info.get("refreshed") is True
    decrypted = json.loads(auth.Fernet(key).decrypt(store.d["google"]))
    assert decrypted.get("refreshed") is True


def test_get_retries_dead_socket_but_post_does_not():
    from unittest.mock import MagicMock
    from googleapiclient.http import HttpRequest
    from jarvis.google.auth import _RetryingGet

    def run(method):
        http = MagicMock()
        http.request.side_effect = [BrokenPipeError(32, "Broken pipe"),
                                    (MagicMock(status=200), b"{}")]
        req = _RetryingGet(http, lambda r, c: {}, "https://x.test/", method=method)
        try:
            req.execute(); return http.request.call_count
        except BrokenPipeError:
            return http.request.call_count

    assert run("GET") == 2
    assert run("POST") == 1
