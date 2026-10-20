import json

import pytest

from jarvis.google import web_auth

REDIRECT = "https://jarvis.example.com/auth/callback"


@pytest.fixture
def secrets(tmp_path):
    p = tmp_path / "web.json"
    p.write_text(json.dumps({"web": {
        "client_id": "cid", "client_secret": "sec", "redirect_uris": [REDIRECT],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}))
    return str(p)


def test_start_url_asks_offline_consent_with_identity_scopes(secrets):
    url = web_auth.start_url(secrets, REDIRECT, "nonce123")
    assert url.startswith("https://accounts.google.com/") and "state=nonce123" in url
    assert "access_type=offline" in url and "prompt=consent" in url and "code_challenge" not in url
    assert "openid" in url and "userinfo.email" in url and "calendar" in url
    assert "redirect_uri=https%3A%2F%2Fjarvis.example.com%2Fauth%2Fcallback" in url


def test_start_url_accepts_the_client_json_inline(secrets):
    url = web_auth.start_url(open(secrets).read(), REDIRECT, "s")
    assert url.startswith("https://accounts.google.com/") and "client_id=cid" in url


def test_exchange_verifies_the_id_token_against_the_web_client(monkeypatch, secrets):
    seen = {}

    def fetch_token(self, code):
        seen["code"] = code
        self.oauth2session.token = {"access_token": "at", "id_token": "idt", "token_type": "Bearer", "expires_at": 2000000000}

    def verify(token, request, audience):
        seen["verify"] = (token, audience)
        return {"email": "a@example.com", "sub": "s", "email_verified": True}

    monkeypatch.setattr(web_auth.Flow, "fetch_token", fetch_token)
    monkeypatch.setattr(web_auth.id_token, "verify_oauth2_token", verify)
    creds, claims = web_auth.exchange(secrets, REDIRECT, "the-code")
    assert seen == {"code": "the-code", "verify": ("idt", "cid")}
    assert claims["sub"] == "s" and creds.token == "at"
