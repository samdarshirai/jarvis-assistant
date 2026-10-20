import asyncio
import logging
import socket
from functools import partial
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
from cryptography.fernet import Fernet
from langgraph.checkpoint.memory import InMemorySaver

import jarvis.llm_check as llm_check  # module import: pytest would otherwise collect test_models
from jarvis.appapi import AppService
from jarvis.google.auth import PROVIDER, PgTokenStore
from jarvis.keys import Keys
from jarvis.main import create_app
from jarvis.tenant import Tenants
from jarvis.users import SecretStore, UserStore
from jarvis.voice.devices import Devices

KEY = "sk-or-v1-SUPERSECRETVALUE7f3a"
DG = "dg-SECRETDEEPGRAMKEY-91b2"
TV = "tvly-SECRETTAVILYKEY-c4d5"
OP = "sk-or-v1-OPERATORENVKEY-0000"
FERNET = Fernet.generate_key().decode()


def settings(**kw):
    return NS(fernet_key=FERNET, models=lambda tier: [f"default/{tier}"], **kw)


def public(host, port, **kw):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def failing(status):
    """Transport: `good/*` models answer, every other model gets `status`."""
    def handler(req):
        if b'"good/' in req.content:
            return httpx.Response(200, json={"choices": [{"message": {"content": "o"}}]})
        return httpx.Response(status, text="nope")
    return httpx.MockTransport(handler)


class Checker:
    """Stands in for llm_check.test_models; records what it was asked to test and what was stored at that moment."""
    def __init__(self, pool, uid, exc=None):
        self.pool, self.uid, self.exc, self.calls, self.stored_during = pool, uid, exc, [], []

    async def __call__(self, creds):
        self.calls.append(creds)
        self.stored_during.append(SecretStore(self.pool, FERNET).all(self.uid))
        if self.exc:
            raise self.exc


async def setup(pool, uid, check=None, s=None, llm_factory=lambda *a: None):
    devices = Devices(pool)
    kw = {} if llm_factory is None else {"llm_provider_factory": llm_factory}
    t = Tenants(pool, s or settings(), InMemorySaver(), google_service_factory=lambda *a: object(), **kw)
    app = create_app(with_lifespan=False)
    app.state.app_api = AppService(devices, t)
    app.state.keys = Keys(devices, t, check=check or Checker(pool, uid))
    token = devices.create(uid)[1]
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                          headers={"Authorization": f"Bearer {token}"})
    return c, t


def audit_text(pool) -> str:
    with pool.connection() as c:
        return str(c.execute("SELECT * FROM audit_log").fetchall())


def secrets(pool, uid):
    return SecretStore(pool, FERNET).all(uid)


async def test_me_never_returns_secret_values(pool, uids, caplog):
    caplog.set_level(logging.DEBUG)
    uid = uids[0]
    c, _ = await setup(pool, uid)
    r = await c.get("/me")
    assert r.status_code == 200 and r.json() == {
        "email": "a@example.com", "tz": "Europe/Berlin",
        "setup": {"google": False, "llm": False, "deepgram": False, "tavily": False},
        "keys": {"llm_base_url": None, "llm_key_last4": None, "model_fast": None, "model_strong": None,
                 "deepgram_last4": None, "tavily_last4": None}}
    store = SecretStore(pool, FERNET)
    for name, value in (("llm_key", KEY), ("deepgram_key", DG), ("tavily_key", TV), ("model_fast", "a/b, c/d")):
        store.put(uid, name, value)
    PgTokenStore(pool, uid).put(PROVIDER, b"blob")
    r = await c.get("/me")
    assert r.json()["setup"] == {"google": True, "llm": True, "deepgram": True, "tavily": True}
    assert r.json()["keys"] == {"llm_base_url": "https://openrouter.ai/api/v1", "llm_key_last4": "7f3a",
                                "model_fast": "a/b,c/d", "model_strong": "default/strong",
                                "deepgram_last4": "91b2", "tavily_last4": "c4d5"}
    for v in (KEY, DG, TV, KEY[:-4], DG[:-4], TV[:-4]):
        assert v not in r.text and v not in caplog.text
    assert (await (await setup(pool, uids[1]))[0].get("/me")).json()["setup"]["llm"] is False  # B sees nothing of A


async def test_put_llm_saves_only_after_passing_test(pool, uids, caplog):
    caplog.set_level(logging.DEBUG)
    uid = uids[0]
    check = Checker(pool, uid)
    c, t = await setup(pool, uid, check)
    before = await t.get(uid)
    r = await c.put("/settings/llm", json={"base_url": "https://API.example.com./v1/", "api_key": KEY,
                                          "model_fast": "good/fast", "model_strong": " good/a , good/b "})
    assert r.status_code == 200, r.text
    assert check.stored_during == [{}]  # tested before anything was written
    creds = check.calls[0]
    assert (creds.base_url, creds.api_key, creds.fast, creds.strong) == (
        "https://api.example.com/v1", KEY, ["good/fast"], ["good/a", "good/b"])
    assert secrets(pool, uid) == {"llm_base_url": "https://api.example.com/v1", "llm_key": KEY,
                                  "model_fast": "good/fast", "model_strong": "good/a , good/b"}
    assert r.json()["setup"]["llm"] is True and r.json()["keys"]["llm_key_last4"] == "7f3a"
    after = await t.get(uid)
    assert after is not before and after.secrets["llm_key"] == KEY  # invalidated: the next request has the new key
    assert KEY not in r.text and KEY not in caplog.text and KEY not in audit_text(pool)
    assert "set_llm" in audit_text(pool)
    # Omitted model ids fall back to the server defaults (stored ones are cleared).
    await c.put("/settings/llm", json={"base_url": "https://api.example.com/v1", "api_key": KEY})
    assert (check.calls[-1].fast, check.calls[-1].strong) == (["default/fast"], ["default/strong"])
    assert "model_fast" not in secrets(pool, uid)


async def test_put_llm_failure_saves_nothing_and_names_model(pool, uids, caplog):
    caplog.set_level(logging.DEBUG)
    uid = uids[0]
    body = {"base_url": "https://api.example.com/v1", "api_key": KEY, "model_fast": "good/fast", "model_strong": "bad/strong"}
    for status, code in ((401, "rejected_key"), (402, "out_of_credit"), (404, "model_not_found")):
        check = partial(llm_check.test_models, resolve=public, transport=failing(status))
        c, _ = await setup(pool, uid, check)
        r = await c.put("/settings/llm", json=body)
        assert r.status_code == 422 and r.json() == {"error": code, "model": "bad/strong"}
        assert secrets(pool, uid) == {}
    c, _ = await setup(pool, uid, Checker(pool, uid, httpx.InvalidURL("bad host")))
    r = await c.put("/settings/llm", json=body)
    assert r.status_code == 422 and r.json() == {"error": "bad_url", "model": None}
    assert secrets(pool, uid) == {} and KEY not in caplog.text and KEY not in audit_text(pool)
    c, _ = await setup(pool, uid, s=NS(fernet_key=FERNET, models=lambda tier: []))  # no models anywhere to test
    r = await c.put("/settings/llm", json={"base_url": "https://api.example.com/v1", "api_key": KEY})
    assert r.status_code == 422 and r.json()["error"] == "no_models" and secrets(pool, uid) == {}


async def test_key_is_stripped_of_whitespace_and_newline(pool, uids):
    uid = uids[0]
    c, _ = await setup(pool, uid)
    assert (await c.put("/settings/llm", json={"base_url": "https://api.example.com/v1", "api_key": f"  {KEY}\n"})).status_code == 200
    assert (await c.put("/settings/deepgram", json={"api_key": f"\t{DG} \r\n"})).status_code == 200
    assert (await c.put("/settings/tavily", json={"api_key": f"{TV}\n"})).json()["keys"]["tavily_last4"] == "c4d5"
    s = secrets(pool, uid)
    assert (s["llm_key"], s["deepgram_key"], s["tavily_key"]) == (KEY, DG, TV)
    for path, body in (("/settings/llm", {"base_url": "https://api.example.com/v1", "api_key": " \n "}),
                       ("/settings/deepgram", {"api_key": "\n"}), ("/settings/tavily", {"api_key": ""})):
        r = await c.put(path, json=body)
        assert r.status_code == 422 and r.json() == {"error": "bad_request"}
    assert secrets(pool, uid) == s


async def test_http_url_and_localhost_rejected_422(pool, uids, caplog):
    caplog.set_level(logging.DEBUG)
    uid = uids[0]
    check = Checker(pool, uid)
    c, _ = await setup(pool, uid, check)
    for url in ("http://api.example.com/v1", "https://localhost/v1", "https://LOCALHOST./v1", "https://x.localhost/v1",
                "https://127.0.0.1/v1", "https://10.1.2.3:8443/v1", "https://192.168.0.1/v1", "https://[::1]/v1",
                "https://169.254.169.254/v1", "https://user:PWSECRET@api.example.com/v1",
                "https://api.example.com/v1?key=QSECRET", "api.example.com/v1", "https://"):
        r = await c.put("/settings/llm", json={"base_url": url, "api_key": KEY})
        assert r.status_code == 422 and r.json() == {"error": "bad_url", "model": None}, url
        assert "SECRET" not in r.text
    assert check.calls == [] and secrets(pool, uid) == {}
    assert "PWSECRET" not in caplog.text and "QSECRET" not in caplog.text and "SECRET" not in audit_text(pool)


async def test_chat_without_llm_key_is_needs_setup_even_if_operator_key_in_env(pool, uids, monkeypatch):
    for name, value in (("JARVIS_OPENROUTER_API_KEY", OP), ("JARVIS_DEEPGRAM_API_KEY", OP), ("JARVIS_TAVILY_API_KEY", OP),
                        ("OPENROUTER_API_KEY", OP), ("OPENAI_API_KEY", OP)):
        monkeypatch.setenv(name, value)
    uid = uids[1]  # not the owner: nothing was copied into their secrets
    s = settings(openrouter_api_key=OP, deepgram_api_key=OP, tavily_api_key=OP, models_fast="m/f", models_strong="m/s")
    c, t = await setup(pool, uid, s=s, llm_factory=None)  # the real LLMProvider factory
    r = await c.post("/chat", json={"text": "hi"})
    assert r.status_code == 409 and r.json() == {"error": "needs_setup", "missing": "llm"}
    r = await c.post("/chat/confirm", json={"interrupt_id": "i", "decision": "yes"})
    assert r.status_code == 409 and r.json() == {"error": "needs_setup", "missing": "llm"}
    rt = await t.get(uid)
    assert rt.llm is None and rt.graph is None and rt.secrets == {}
    me = (await c.get("/me")).json()
    assert me["setup"] == {"google": False, "llm": False, "deepgram": False, "tavily": False}
    assert OP[-4:] not in str(me["keys"])


async def test_delete_optional_key(pool, uids):
    uid = uids[0]
    c, t = await setup(pool, uid)
    await c.put("/settings/deepgram", json={"api_key": DG})
    await c.put("/settings/tavily", json={"api_key": TV})
    before = await t.get(uid)
    assert before.secrets["deepgram_key"] == DG
    r = await c.delete("/settings/deepgram")
    assert r.status_code == 200 and r.json()["setup"]["deepgram"] is False and r.json()["setup"]["tavily"] is True
    assert r.json()["keys"]["deepgram_last4"] is None
    assert secrets(pool, uid) == {"tavily_key": TV}
    after = await t.get(uid)
    assert after is not before and "deepgram_key" not in after.secrets
    assert (await c.delete("/settings/deepgram")).status_code == 200  # already gone: still fine
    assert (await c.delete("/settings/tavily")).json()["setup"]["tavily"] is False
    assert (await c.delete("/settings/llm")).status_code in (404, 405, 422)  # the LLM key is replaced, not deleted


async def test_bad_timezone_rejected(pool, uids):
    uid = uids[0]
    c, t = await setup(pool, uid)
    before = await t.get(uid)
    for tz in ("Mars/Olympus", "America", "../../etc/passwd", "", "/etc/localtime"):
        r = await c.put("/me", json={"tz": tz})
        assert r.status_code == 422 and r.json()["error"] in ("bad_timezone", "bad_request"), tz
    assert UserStore(pool).get(uid).tz == "Europe/Berlin"
    r = await c.put("/me", json={"tz": " Asia/Kolkata "})
    assert r.status_code == 200 and r.json()["tz"] == "Asia/Kolkata"
    after = await t.get(uid)
    assert after is not before and after.tz == "Asia/Kolkata"


async def test_unauthenticated_401(pool, uids):
    uid = uids[0]
    c, _ = await setup(pool, uid)
    routes = [("get", "/me", None), ("put", "/me", {"tz": "UTC"}),
              ("put", "/settings/llm", {"base_url": "https://api.example.com/v1", "api_key": KEY}),
              ("put", "/settings/deepgram", {"api_key": DG}), ("put", "/settings/tavily", {"api_key": TV}),
              ("delete", "/settings/deepgram", None), ("delete", "/settings/tavily", None)]
    for hdr in ({"Authorization": ""}, {"Authorization": "Bearer nope"}):
        for m, p, body in routes:
            r = await c.request(m.upper(), p, headers=hdr, **({"json": body} if body else {}))
            assert r.status_code == 401 and r.json() == {"error": "unauthorized"}, (m, p)
    UserStore(pool).set_disabled(uid, True)
    assert (await c.get("/me")).status_code == 401
    assert secrets(pool, uid) == {}
    app = create_app(with_lifespan=False)  # before the lifespan built the service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c2:
        assert (await c2.get("/me")).status_code == 503


def test_caddy_proxies_account_and_settings_routes():
    line = next(l for l in (Path(__file__).parents[1] / "Caddyfile").read_text().splitlines() if "@public path" in l)
    assert {"/me", "/settings/*"} <= set(line.split())
