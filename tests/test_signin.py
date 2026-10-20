import base64
import re
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from jarvis.google.auth import PgTokenStore
from jarvis.main import create_app
from jarvis.signin import BadState, Refused, SignIn
from jarvis.users import SecretStore, UserStore
from jarvis.voice.devices import Devices

KEY = Fernet.generate_key().decode()
CLIENT = json.dumps({"web": {
    "client_id": "cid", "client_secret": "sec", "redirect_uris": ["https://jarvis.example.com/auth/callback"],
    "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}})
VERIFIER = "v" * 43


def challenge(verifier=VERIFIER):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class Creds:
    def __init__(self, rt="rt"):
        self.rt = rt

    def to_json(self):
        return json.dumps({"refresh_token": self.rt, "client_id": "cid", "client_secret": "sec",
                           "token_uri": "https://oauth2.googleapis.com/token"})


def claims(email="a@example.com", sub="sub-a", verified=True):
    return {"email": email, "sub": sub, "email_verified": verified}


def make(pool, result, invalidated=None):
    """A SignIn whose Google exchange returns `result` (credentials, claims) for any code."""
    return SignIn(pool, UserStore(pool), Devices(pool), KEY, client_secrets=CLIENT,
                  redirect_uri="https://jarvis.example.com/auth/callback", exchange=lambda code: result,
                  invalidate=(invalidated.append if invalidated is not None else lambda uid: None))


def counts(pool):
    with pool.connection() as c:
        return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("devices", "oauth_tokens", "auth_attempts")}


def signed_in(pool, result, invalidated=None):
    s = make(pool, result, invalidated)
    nonce, _ = s.start(challenge())
    link = s.finish(nonce, "code")
    q = parse_qs(urlsplit(link).query)
    assert link.startswith("jarvis://signin?") and q["nonce"] == [nonce] and len(q["code"][0]) >= 43
    return s, nonce, q["code"][0]


def link_code(html_text):
    """The finish code from the callback page's deep link."""
    return re.search(r"jarvis://signin\?nonce=[\w-]+&amp;code=([\w-]+)", html_text).group(1)


def test_start_returns_consent_url_bound_to_the_nonce(pool):
    nonce, url = make(pool, None).start(challenge())
    assert url.startswith("https://accounts.google.com/") and f"state={nonce}" in url
    assert "access_type=offline" in url and "prompt=consent" in url and "openid" in url


def test_unlisted_email_refused_no_rows_created(pool):
    s = make(pool, (Creds(), claims("stranger@example.com")))
    nonce, _ = s.start(challenge())
    with pytest.raises(Refused):
        s.finish(nonce, "code")
    assert counts(pool) == {"devices": 0, "oauth_tokens": 0, "auth_attempts": 0}
    assert UserStore(pool).list() == []


def test_email_case_insensitive_match(pool):
    u = UserStore(pool).add("a@example.com", "UTC")
    s, nonce, code = signed_in(pool, (Creds(), claims("A@Example.COM")))
    token = s.claim(nonce, VERIFIER, code)
    assert Devices(pool).verify(token).user_id == u.id


def test_unverified_email_refused(pool):
    UserStore(pool).add("a@example.com", "UTC")
    s = make(pool, (Creds(), claims(verified=False)))
    nonce, _ = s.start(challenge())
    with pytest.raises(Refused):
        s.finish(nonce, "code")
    assert counts(pool) == {"devices": 0, "oauth_tokens": 0, "auth_attempts": 0}


def test_sub_mismatch_refused(pool):
    users = UserStore(pool)
    u = users.add("a@example.com", "UTC")
    users.set_sub(u.id, "sub-a")
    s = make(pool, (Creds(), claims(sub="sub-other")))
    nonce, _ = s.start(challenge())
    with pytest.raises(Refused):
        s.finish(nonce, "code")
    assert counts(pool) == {"devices": 0, "oauth_tokens": 0, "auth_attempts": 0}
    assert users.get(u.id).google_sub == "sub-a"


def test_disabled_user_refused(pool):
    users = UserStore(pool)
    users.set_disabled(users.add("a@example.com", "UTC").id, True)
    s = make(pool, (Creds(), claims()))
    nonce, _ = s.start(challenge())
    with pytest.raises(Refused):
        s.finish(nonce, "code")
    assert counts(pool) == {"devices": 0, "oauth_tokens": 0, "auth_attempts": 0}


def test_owner_first_signin_sets_sub_and_keeps_existing_devices(pool):
    users, devices = UserStore(pool), Devices(pool)
    owner = users.add("a@example.com", "UTC", is_owner=True)
    _, old = devices.create(owner.id)
    invalidated = []
    s, nonce, code = signed_in(pool, (Creds(), claims()), invalidated)
    new = s.claim(nonce, VERIFIER, code)
    assert users.get(owner.id).google_sub == "sub-a"
    assert devices.verify(old).user_id == owner.id and devices.verify(new).user_id == owner.id
    assert invalidated == [owner.id]


def test_resignin_replaces_credentials_and_adds_a_device(pool):
    u = UserStore(pool).add("a@example.com", "UTC")
    signed_in(pool, (Creds("rt1"), claims()))
    s, nonce, code = signed_in(pool, (Creds("rt2"), claims()))
    assert s.claim(nonce, VERIFIER, code)
    assert json.loads(Fernet(KEY).decrypt(PgTokenStore(pool, u.id).get("google")))["refresh_token"] == "rt2"
    assert counts(pool)["devices"] == 2


def test_state_is_single_use(pool):
    UserStore(pool).add("a@example.com", "UTC")
    s, nonce, code = signed_in(pool, (Creds(), claims()))
    with pytest.raises(BadState):
        s.finish(nonce, "code")


def test_claim_wrong_verifier_none(pool):
    UserStore(pool).add("a@example.com", "UTC")
    s, nonce, code = signed_in(pool, (Creds(), claims()))
    assert s.claim(nonce, "w" * 43, code) is None
    assert s.claim(nonce, VERIFIER, code)  # a wrong guess does not burn the attempt for the real app


def test_claim_replay_none(pool):
    UserStore(pool).add("a@example.com", "UTC")
    s, nonce, code = signed_in(pool, (Creds(), claims()))
    assert s.claim(nonce, VERIFIER, code)
    assert s.claim(nonce, VERIFIER, code) is None
    assert counts(pool)["auth_attempts"] == 0


def test_claim_expired_none(pool):
    UserStore(pool).add("a@example.com", "UTC")
    s, nonce, code = signed_in(pool, (Creds(), claims()))
    with pool.connection() as c:
        c.execute("UPDATE auth_attempts SET created_at = now() - interval '601 seconds'")
    assert s.claim(nonce, VERIFIER, code) is None


def test_claim_before_callback_none(pool):
    s = make(pool, None)
    nonce, _ = s.start(challenge())
    assert s.claim(nonce, VERIFIER, "") is None


def test_claim_for_other_nonce_none(pool):
    UserStore(pool).add("a@example.com", "UTC")
    s, nonce, code = signed_in(pool, (Creds(), claims()))
    other, _ = s.start(challenge("x" * 43))
    assert s.claim(other, VERIFIER, code) is None
    assert s.claim("no-such-nonce", VERIFIER, code) is None


def test_start_purges_expired_attempts(pool):
    s = make(pool, None)
    s.start(challenge())
    with pool.connection() as c:
        c.execute("UPDATE auth_attempts SET created_at = now() - interval '601 seconds'")
    s.start(challenge())
    assert counts(pool)["auth_attempts"] == 1


def test_credentials_stored_encrypted_for_that_user_only(pool):
    users = UserStore(pool)
    a, b = users.add("a@example.com", "UTC"), users.add("b@example.com", "UTC")
    signed_in(pool, (Creds("secret-rt"), claims()))
    with pool.connection() as c:
        rows = c.execute("SELECT user_id, blob FROM oauth_tokens").fetchall()
    assert [r[0] for r in rows] == [a.id] and b"secret-rt" not in bytes(rows[0][1])
    assert json.loads(Fernet(KEY).decrypt(bytes(rows[0][1])))["refresh_token"] == "secret-rt"
    assert PgTokenStore(pool, b.id).get("google") is None
    with pool.connection() as c:
        enc = c.execute("SELECT device_token_enc FROM auth_attempts").fetchone()[0]
    assert enc is not None and Fernet(KEY).decrypt(bytes(enc))  # the device token waits encrypted


# Routes


def client(pool, result):
    app = create_app(with_lifespan=False)
    app.state.signin = make(pool, result)
    return TestClient(app)


def test_routes_sign_in_and_claim(pool):
    u = UserStore(pool).add("a@example.com", "UTC")
    c = client(pool, (Creds(), claims()))
    r = c.post("/auth/start", json={"challenge": challenge()})
    nonce = r.json()["nonce"]
    assert r.status_code == 200 and r.json()["url"].startswith("https://accounts.google.com/")
    r = c.get("/auth/callback", params={"code": "code", "state": nonce})
    assert r.status_code == 200 and f"jarvis://signin?nonce={nonce}&amp;code=" in r.text
    r = c.post("/auth/claim", json={"nonce": nonce, "verifier": VERIFIER, "code": link_code(r.text)})
    assert r.status_code == 200 and r.json()["needs_setup"] is True
    assert Devices(pool).verify(r.json()["token"]).user_id == u.id
    SecretStore(pool, KEY).put(u.id, "llm_key", "k")
    nonce = c.post("/auth/start", json={"challenge": challenge()}).json()["nonce"]
    code = link_code(c.get("/auth/callback", params={"code": "code", "state": nonce}).text)
    assert c.post("/auth/claim", json={"nonce": nonce, "verifier": VERIFIER, "code": code}).json()["needs_setup"] is False


def test_callback_with_unknown_state_400(pool):
    r = client(pool, (Creds(), claims())).get("/auth/callback", params={"code": "code", "state": "<script>x</script>"})
    assert r.status_code == 400 and "<script>" not in r.text
    assert counts(pool) == {"devices": 0, "oauth_tokens": 0, "auth_attempts": 0}


def test_callback_refused_page(pool):
    c = client(pool, (Creds(), claims("stranger@example.com")))
    nonce = c.post("/auth/start", json={"challenge": challenge()}).json()["nonce"]
    r = c.get("/auth/callback", params={"code": "code", "state": nonce})
    assert r.status_code == 403 and "not on the list. Ask the owner to add your email." in r.text


def test_callback_exchange_failure_502_without_detail(pool, caplog):
    def boom(code):
        raise RuntimeError("invalid_grant secret-code-xyz")

    app = create_app(with_lifespan=False)
    app.state.signin = SignIn(pool, UserStore(pool), Devices(pool), KEY, client_secrets=CLIENT,
                              redirect_uri="https://jarvis.example.com/auth/callback", exchange=boom)
    c = TestClient(app)
    nonce = c.post("/auth/start", json={"challenge": challenge()}).json()["nonce"]
    r = c.get("/auth/callback", params={"code": "secret-code-xyz", "state": nonce})
    assert r.status_code == 502 and "secret-code-xyz" not in r.text and "secret-code-xyz" not in caplog.text


def test_claim_failures_are_identical_401(pool):
    UserStore(pool).add("a@example.com", "UTC")
    c = client(pool, (Creds(), claims()))
    nonce = c.post("/auth/start", json={"challenge": challenge()}).json()["nonce"]
    code = link_code(c.get("/auth/callback", params={"code": "code", "state": nonce}).text)
    wrong = c.post("/auth/claim", json={"nonce": nonce, "verifier": "w" * 43, "code": code})
    bad_code = c.post("/auth/claim", json={"nonce": nonce, "verifier": VERIFIER, "code": "x" * 43})
    unknown = c.post("/auth/claim", json={"nonce": "nope", "verifier": VERIFIER, "code": code})
    assert wrong.status_code == bad_code.status_code == unknown.status_code == 401
    assert wrong.json() == bad_code.json() == unknown.json()
    assert c.post("/auth/claim", json={"nonce": nonce, "verifier": VERIFIER, "code": code}).status_code == 200


def test_start_refuses_503_busy_when_too_many_live_attempts(pool):
    c = client(pool, None)
    with pool.connection() as conn:  # 1000 live attempts (a flood) plus one expired, which the purge removes
        conn.execute("INSERT INTO auth_attempts (nonce, challenge) SELECT 'n' || i, 'c' FROM generate_series(1, 1000) i")
        conn.execute("INSERT INTO auth_attempts (nonce, challenge, created_at) VALUES ('old', 'c', now() - interval '1 hour')")
    r = c.post("/auth/start", json={"challenge": challenge()})
    assert r.status_code == 503 and r.json() == {"error": "busy"}
    assert counts(pool)["auth_attempts"] == 1000  # nothing written, the expired one purged
    with pool.connection() as conn:
        conn.execute("DELETE FROM auth_attempts WHERE nonce = 'n1'")
        idx = conn.execute("SELECT indexdef FROM pg_indexes WHERE tablename = 'auth_attempts'").fetchall()
    assert c.post("/auth/start", json={"challenge": challenge()}).status_code == 200
    assert any("(created_at)" in d for (d,) in idx)


def test_start_rejects_a_malformed_challenge(pool):
    assert client(pool, None).post("/auth/start", json={"challenge": "short"}).status_code == 422


def test_routes_503_when_sign_in_not_configured():
    c = TestClient(create_app(with_lifespan=False))
    assert c.post("/auth/start", json={"challenge": challenge()}).status_code == 503
    assert c.get("/auth/callback", params={"code": "c", "state": "s"}).status_code == 503
    assert c.post("/auth/claim", json={"nonce": "n", "verifier": VERIFIER, "code": "c"}).status_code == 503


def test_old_google_routes_are_gone():
    c = TestClient(create_app(with_lifespan=False))
    assert c.post("/google/auth/start").status_code == 404
    assert c.get("/google/callback").status_code == 404


def test_claim_needs_the_finish_code(pool):
    UserStore(pool).add("a@example.com", "UTC")
    s, nonce, code = signed_in(pool, (Creds(), claims()))
    assert s.claim(nonce, VERIFIER, "") is None
    assert s.claim(nonce, VERIFIER, "x" * 43) is None
    assert counts(pool)["auth_attempts"] == 1  # a wrong code does not burn the victim's attempt
    assert s.claim(nonce, VERIFIER, code)


def test_login_fixation_attacker_cannot_claim_victims_sign_in(pool):
    # The attacker starts with their own verifier and gets the victim to approve the Google URL. The finish code
    # only reaches the victim's browser, so the attacker's claim fails.
    UserStore(pool).add("a@example.com", "UTC")
    s = make(pool, (Creds(), claims()))
    nonce, _ = s.start(challenge())  # attacker's challenge
    s.finish(nonce, "victim-code")
    assert s.claim(nonce, VERIFIER, "") is None and s.claim(nonce, VERIFIER, "guess") is None


def test_finish_code_is_stored_hashed(pool):
    UserStore(pool).add("a@example.com", "UTC")
    _, _, code = signed_in(pool, (Creds(), claims()))
    with pool.connection() as c:
        stored = c.execute("SELECT finish_code_hash FROM auth_attempts").fetchone()[0]
    assert stored and code not in stored


def test_claim_rejects_a_malformed_verifier(pool):
    c = client(pool, None)
    for bad in ("short", "v" * 129, "v" * 42 + "!"):
        assert c.post("/auth/claim", json={"nonce": "n", "verifier": bad, "code": "c"}).status_code == 422


def test_sub_set_concurrently_to_another_account_refused(pool):
    users = UserStore(pool)
    u = users.add("a@example.com", "UTC")
    s = make(pool, (Creds(), claims(sub="sub-late")))
    read = users.by_email

    def stale(email):  # the user is read before a concurrent sign-in sets a different sub
        found = read(email)
        assert users.set_sub(u.id, "sub-first")
        return found

    s.users.by_email = stale
    nonce, _ = s.start(challenge())
    with pytest.raises(Refused):
        s.finish(nonce, "code")
    assert users.get(u.id).google_sub == "sub-first"
    assert counts(pool) == {"devices": 0, "oauth_tokens": 0, "auth_attempts": 0}


def test_set_sub_only_when_unset_or_equal(pool):
    users = UserStore(pool)
    u = users.add("a@example.com", "UTC")
    assert users.set_sub(u.id, "s1") and users.set_sub(u.id, "s1")
    assert not users.set_sub(u.id, "s2")
    assert users.get(u.id).google_sub == "s1"
