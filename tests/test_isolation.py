"""Cross-user isolation, end to end: the real create_app + lifespan (Tenants, Devices, sign-in, Keys, AppService,
voice, scheduler) over Postgres with two signed-in friends A and B and an owner. Only the outside world is fake:
Google (a service whose every answer carries the tag of the Google account it was built from), the LLM (a scripted
chat model that records which key served it), Deepgram, Telegram and the network under the LLM key check."""
import base64
import hashlib
import json
import logging
import os
import re
import socket
from functools import partial
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import jarvis.llm_check as llm_check  # module import: pytest would otherwise collect test_models
import jarvis.tenant
from jarvis.agent.graph import ROUTER_PROMPT
from jarvis.config import Settings
from jarvis.google.auth import PROVIDER, ReauthRequired
from jarvis.keys import Keys
from jarvis.main import create_app
from jarvis.proactive import scheduler
from jarvis.signin import SignIn
from jarvis.tenant import Tenants, thread_config
from jarvis.users import SecretStore, UserStore
from jarvis.voice.ws import VoiceService
from tests.fakes import FakeSTT, FakeTTS
from tests.voice_helpers import FINAL, ping, read, until_state

FERNET = Fernet.generate_key().decode()
CLIENT = json.dumps({"web": {
    "client_id": "cid", "client_secret": "sec", "redirect_uris": ["https://jarvis.example.com/auth/callback"],
    "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}})
PUBLIC_IP = "93.184.216.34"
# Distinctive planted values: any of them (or all but their last 4 characters) showing up anywhere is a leak.
KEYS = {
    "a": {"llm": "sk-AAAA-LLMKEY-planted-1a2b", "deepgram": "dg-AAAA-DEEPGRAM-planted-3c4d",
          "tavily": "tvly-AAAA-TAVILY-planted-5e6f", "google": "GTOK_AAAA_planted_google"},
    "b": {"llm": "sk-BBBB-LLMKEY-planted-7a8b", "deepgram": "dg-BBBB-DEEPGRAM-planted-9c0d",
          "tavily": "tvly-BBBB-TAVILY-planted-1e2f", "google": "GTOK_BBBB_planted_google"},
}
OP = {"llm": "sk-OPERATOR-ENV-LLM-0000", "deepgram": "dg-OPERATOR-ENV-DG-0000", "tavily": "tvly-OPERATOR-ENV-TV-0000"}
CALLS: list[tuple[str, str]] = []  # (llm key, every message's text joined) per LLM call
GOOGLE: list[tuple[str, str, dict]] = []  # (google tag, method path, kwargs) per Google API call


def planted() -> list[str]:
    vals = [v for u in KEYS.values() for k, v in u.items() if k != "google"] + list(OP.values())
    return vals + [v[:-4] for v in vals]


# --- fake Google: a discovery-style service bound to the account whose stored token built it ---
class FakeGoogleError(Exception):
    pass


def _message(tag: str, mid: str, fmt: str) -> dict:
    body = base64.urlsafe_b64encode(f"Your booking for {tag} is confirmed.".encode()).decode()
    return {"id": mid, "threadId": f"t-{mid}", "snippet": f"booking {tag}", "labelIds": ["INBOX"],
            "payload": {"mimeType": "text/plain", "body": {"data": body} if fmt == "full" else {},
                        "headers": [{"name": "From", "value": f"{tag}@sender.example.com"},
                                    {"name": "Subject", "value": f"Booking {tag}"},
                                    {"name": "Date", "value": "Tue, 20 Oct 2026 09:00:00 +0200"}]}}


def _answer(tag: str, path: str, kw: dict):
    mine = lambda i: i.endswith(tag) or i == "msg-shared"  # noqa: E731  (Gmail ids are per mailbox: both have one)
    if path == "events.list":
        return {"items": [{"id": f"ev-{tag}", "summary": f"Meeting {tag}", "status": "confirmed",
                           "start": {"dateTime": "2026-10-21T10:00:00+02:00"},
                           "end": {"dateTime": "2026-10-21T11:00:00+02:00"}}]}
    if path == "tasks.list":
        return {"items": [{"id": f"task-{tag}", "title": f"Task {tag}", "status": "needsAction"}]}
    if path == "tasks.insert":
        return {"id": f"new-{tag}", "title": kw["body"]["title"], "status": "needsAction"}
    if path == "tasks.patch":
        if not mine(kw["task"]):
            raise FakeGoogleError("404 not this account's task")
        return {"id": kw["task"], "status": "completed"}
    if path == "users.messages.list":
        return {"messages": [{"id": f"msg-{tag}"}, {"id": "msg-shared"}]}
    if path == "users.messages.get":
        if not mine(kw["id"]):
            raise FakeGoogleError("404 not this account's message")
        return _message(tag, kw["id"], kw.get("format"))
    return {}


class FakeGoogle:
    def __init__(self, tag: str, path: tuple = (), kw: dict | None = None):
        self.tag, self.path, self.kw = tag, path, kw or {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return FakeGoogle(self.tag, self.path + (name,))

    def __call__(self, **kw):
        return FakeGoogle(self.tag, self.path, kw)

    def execute(self):
        path = ".".join(self.path)
        GOOGLE.append((self.tag, path, self.kw))
        return _answer(self.tag, path, self.kw)


def google_factory(name, version, store, key):
    """Like build_service: the stored, encrypted credentials of the store's user, or ReauthRequired."""
    blob = store.get(PROVIDER)
    if blob is None:
        raise ReauthRequired("No Google authorization stored.")
    return FakeGoogle(json.loads(Fernet(key).decrypt(blob))["tag"])


# --- fake LLM: answers by the conversation, records which key served each call ---
class Brain(BaseChatModel):
    key: str

    @property
    def _llm_type(self) -> str:
        return "brain"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        CALLS.append((self.key, "\n".join(str(m.content) for m in messages)))
        return ChatResult(generations=[ChatGeneration(message=self._decide(messages))])

    @staticmethod
    def _decide(messages) -> AIMessage:
        system = messages[0].content if isinstance(messages[0], SystemMessage) else ""
        last = messages[-1]
        if system.startswith(ROUTER_PROMPT):  # memory and situation blocks are appended to it
            # follow-up turns skip keyword routing, so the fake router has to pick the domain itself
            asked = str(last.content) if isinstance(last, HumanMessage) else ""
            if asked.startswith(("remember:", "forget about")):
                return AIMessage("memory")
            if asked.startswith("note:"):
                return AIMessage("notes")
            return AIMessage("tasks" if asked == "list my tasks" else "chat")
        if system.startswith("You extract one calendar event"):
            return AIMessage('{"found": false}')
        if isinstance(last, ToolMessage) or not isinstance(last, HumanMessage):
            return AIMessage("Done.")
        text = str(last.content)

        def call(name, args):
            return AIMessage("", tool_calls=[{"name": name, "args": args, "id": "c1", "type": "tool_call"}])

        if text.startswith("remember:"):
            return call("remember", {"text": text[9:].strip()})
        if text.startswith("note:"):
            return call("create_note", {"title": text[5:].strip(), "body": text[5:].strip()})
        if m := re.fullmatch(r"forget about #(\d+)", text):
            return call("forget", {"memory_id": int(m[1])})
        if text == "list my tasks":
            return call("list_tasks", {})
        return AIMessage("Hi there.")


class FakeLLM:
    made: list[str] = []

    def __init__(self, creds, audit):
        self.key = creds.api_key
        FakeLLM.made.append(creds.api_key)

    def get(self, tier, tools=None):
        return Brain(key=self.key)

    async def aclose(self):
        pass


# --- the outside of the LLM key check: a resolver and a transport that record every lookup and connection ---
class Net:
    def __init__(self):
        self.dns: dict[str, list[str]] = {}  # host -> addresses for successive lookups (the last one repeats)
        self.lookups: list[str] = []
        self.connections: list[tuple[str, str, str]] = []  # (ip connected to, Host header, Authorization)
        self.redirect: dict[str, str] = {}  # Host header -> Location
        self.status: dict[str, int] = {}

    def resolve(self, host, port, **kw):
        self.lookups.append(host)
        seq = self.dns.get(host, [PUBLIC_IP])
        ip = seq.pop(0) if len(seq) > 1 else seq[0]
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(fam, socket.SOCK_STREAM, 6, "", (ip, port))]

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        self.connections.append((request.url.host, host, request.headers.get("authorization", "")))
        if host in self.redirect:
            return httpx.Response(302, headers={"location": self.redirect[host]})
        status = self.status.get(host, 200)
        if status == 0:  # unreachable, with the key in the exception text
            raise httpx.ConnectError(f"connect failed for {request.headers.get('authorization')}", request=request)
        if status != 200:  # a provider that echoes the key it refused
            return httpx.Response(status, text=f"invalid key {request.headers.get('authorization')}")
        return httpx.Response(200, json={"choices": [{"message": {"content": "o"}}]})


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def sign_in(c: TestClient, email: str) -> str | None:
    """The real sign-in: /auth/start -> Google (faked) -> /auth/callback -> /auth/claim. The device token, or None."""
    verifier = "v" * 43
    nonce = c.post("/auth/start", json={"challenge": _challenge(verifier)}).json()["nonce"]
    r = c.get("/auth/callback", params={"code": email, "state": nonce})
    m = re.search(r"[;&]code=([\w-]+)", r.text)
    if r.status_code != 200 or not m:
        return None
    r = c.post("/auth/claim", json={"nonce": nonce, "verifier": verifier, "code": m[1]})
    return r.json()["token"] if r.status_code == 200 else None


class _Creds:
    def __init__(self, tag):
        self.tag = tag

    def to_json(self):
        return json.dumps({"tag": self.tag, "refresh_token": f"rt-{self.tag}"})


def exchange(code: str):
    """Google's answer for a sign-in code: here the code is the email that approved it."""
    who = code.split("@")[0]
    tag = KEYS[who]["google"] if who in KEYS else f"GTOK_{who}"
    return _Creds(tag), {"email": code, "sub": f"sub-{who}", "email_verified": True}


@pytest.fixture
def env(pool, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    CALLS.clear()
    GOOGLE.clear()
    FakeLLM.made = []
    # mail watch is owner-only for now; run it for the friends here so its per-user isolation stays covered
    monkeypatch.setattr("jarvis.proactive.scheduler.FRIEND_MAIL_WATCH", True)
    drop_checkpoint_tables(pool)  # the lifespan's saver creates them fresh; other suites expect them absent
    users = UserStore(pool)
    a, b = users.add("a@example.com", "Europe/Berlin").id, users.add("b@example.com", "Asia/Kolkata").id
    for name, value in (("JARVIS_OPENROUTER_API_KEY", OP["llm"]), ("JARVIS_DEEPGRAM_API_KEY", OP["deepgram"]),
                        ("JARVIS_TAVILY_API_KEY", OP["tavily"]), ("OPENROUTER_API_KEY", OP["llm"]),
                        ("OPENAI_API_KEY", OP["llm"]), ("DEEPGRAM_API_KEY", OP["deepgram"]),
                        ("TAVILY_API_KEY", OP["tavily"])):
        monkeypatch.setenv(name, value)
    s = Settings(_env_file=None, database_url=os.environ["TEST_DATABASE_URL"], telegram_bot_token="1:test",
                 telegram_owner_chat_id=42, fernet_key=FERNET, owner_email="owner@example.com",
                 openrouter_api_key=OP["llm"], deepgram_api_key=OP["deepgram"], tavily_api_key=OP["tavily"],
                 models_fast="op/fast", models_strong="op/strong", google_web_client_secrets=CLIENT,
                 public_base_url="https://jarvis.example.com")
    net = Net()
    voice_keys: list[tuple[str, str]] = []
    stt_scripts: list[list] = []
    web_search_keys: list[str] = []
    ns = NS(a=a, b=b, s=s, net=net, voice_keys=voice_keys, stt_scripts=stt_scripts, web_search_keys=web_search_keys,
            pool=pool, caplog=caplog, bodies=[])

    def stt_factory(key):
        voice_keys.append(("stt", key))
        return FakeSTT(stt_scripts.pop(0) if stt_scripts else [])

    def tts_factory(key):
        voice_keys.append(("tts", key))
        return FakeTTS()

    def capture_scheduler(*args, **kw):
        ns.sched = scheduler.start_proactive(*args, **kw)
        return ns.sched

    class FakeTelegram:
        def __init__(self, *args, **kw):
            pass

        def build(self, token):
            tg = AsyncMock()
            tg.updater = AsyncMock()
            tg.updater.running = tg.running = False
            return tg

    real_web_search = jarvis.tenant.WebSearch
    monkeypatch.setattr("jarvis.tenant.WebSearch", lambda key: web_search_keys.append(key) or real_web_search(key))
    monkeypatch.setattr("jarvis.main.get_settings", lambda: s)
    monkeypatch.setattr("jarvis.main.Tenants", partial(Tenants, google_service_factory=google_factory,
                                                       llm_provider_factory=FakeLLM))
    monkeypatch.setattr("jarvis.main.VoiceService", partial(VoiceService, stt_factory=stt_factory, tts_factory=tts_factory))
    monkeypatch.setattr("jarvis.main.Keys", partial(Keys, check=partial(
        llm_check.test_models, resolve=net.resolve, transport=httpx.MockTransport(net.handle))))
    monkeypatch.setattr("jarvis.main.SignIn", partial(SignIn, exchange=exchange))
    monkeypatch.setattr("jarvis.main.TelegramChannel", FakeTelegram)
    monkeypatch.setattr("jarvis.main.start_proactive", capture_scheduler)

    def keep(r: httpx.Response):
        r.read()
        ns.bodies.append(r.text)

    with TestClient(create_app()) as c:
        c.event_hooks["response"] = [keep]
        ns.c, ns.app = c, c.app
        ns.owner = c.app.state.owner_id
        ns.tok = {"a": sign_in(c, "a@example.com"), "b": sign_in(c, "b@example.com")}
        assert all(ns.tok.values())
        for who in ("a", "b"):
            h = {"Authorization": f"Bearer {ns.tok[who]}"}
            r = c.put("/settings/llm", headers=h, json={"base_url": f"https://llm-{who}.example.com/v1",
                                                        "api_key": KEYS[who]["llm"], "model_fast": f"{who}/fast",
                                                        "model_strong": f"{who}/strong"})
            assert r.status_code == 200, r.text
        ns.h = lambda who: {"Authorization": f"Bearer {ns.tok[who]}"}
        ns.portal = c.portal
        ns.tenants = c.app.state.tenants
        yield ns
    drop_checkpoint_tables(pool)


def drop_checkpoint_tables(pool) -> None:
    with pool.connection() as conn:
        conn.execute("DROP TABLE IF EXISTS checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations")


def uid(env, who):
    return {"a": env.a, "b": env.b}[who]


def chat(env, who, text):
    r = env.c.post("/chat", headers=env.h(who), json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def confirm(env, who, interrupt_id, decision="yes"):
    r = env.c.post("/chat/confirm", headers=env.h(who), json={"interrupt_id": interrupt_id, "decision": decision})
    assert r.status_code == 200, r.text
    return r.json()


def run_job(env, name):
    env.portal.call(env.sched.get_job(name).func)


def rows(env, sql, *args):
    with env.pool.connection() as c:
        return c.execute(sql, args).fetchall()


def other(who):
    return "b" if who == "a" else "a"


# ---------------------------------------------------------------------------------------------------------------------
def test_token_a_never_reaches_user_b_clients(env):
    c = env.c
    for who in ("a", "b"):
        mine, theirs = KEYS[who]["google"], KEYS[other(who)]["google"]
        GOOGLE.clear()
        CALLS.clear()
        h = env.h(who)
        bodies = [c.get("/calendar", headers=h), c.get("/tasks", headers=h), c.get("/mail", headers=h),
                  c.get("/mail/msg-shared", headers=h), c.get(f"/mail/msg-{mine}", headers=h),
                  c.get("/dashboard", headers=h), c.post("/tasks", headers=h, json={"title": "buy milk"}),
                  c.post(f"/tasks/task-{mine}/complete", headers=h)]
        assert all(r.status_code == 200 for r in bodies), [(r.request.url, r.status_code) for r in bodies]
        assert mine in bodies[0].text and mine in bodies[1].text and mine in bodies[2].text
        # the other account's ids are not reachable with this user's Google token
        assert c.get(f"/mail/msg-{theirs}", headers=h).status_code == 404
        assert c.post(f"/tasks/task-{theirs}/complete", headers=h).status_code == 502
        # through the agent too: a read tool runs on the caller's Google client
        assert chat(env, who, "list my tasks")["reply"] == "Done."
        assert GOOGLE and {tag for tag, _, _ in GOOGLE} == {mine}, GOOGLE
        assert {k for k, _ in CALLS} == {KEYS[who]["llm"]}
        assert not any(theirs in text for _, text in CALLS)
        assert not any(theirs in r.text for r in bodies)


def test_memories_notes_audit_mail_state_isolated_end_to_end(env):
    c = env.c
    a, b = env.a, env.b
    for who, fact, note in (("a", "A likes window seats", "A trip to Lisbon"), ("b", "B likes aisle seats", "B trip to Pune")):
        card = chat(env, who, f"remember: {fact}")["card"]
        assert card and fact in card["summary"]
        assert confirm(env, who, card["interrupt_id"])["reply"] == "Got it, I'll remember that."
        card = chat(env, who, f"note: {note}")["card"]
        assert confirm(env, who, card["interrupt_id"])["reply"] == "Saved the note."
    assert sorted(rows(env, "SELECT user_id, text FROM memories")) == [(a, "A likes window seats"), (b, "B likes aisle seats")]
    notes = dict(rows(env, "SELECT title, id FROM notes"))
    # notes: each sees only their own list; the other's id is not found
    assert [n["title"] for n in c.get("/notes", headers=env.h("b")).json()["notes"]] == ["B trip to Pune"]
    assert c.get(f"/notes/{notes['A trip to Lisbon']}", headers=env.h("b")).status_code == 404
    assert c.get(f"/notes/{notes['A trip to Lisbon']}", headers=env.h("a")).json()["title"] == "A trip to Lisbon"
    # memories reach only their owner's prompts
    CALLS.clear()
    chat(env, "a", "hello")
    chat(env, "b", "hello")
    for key, text in CALLS:
        assert ("B likes" if key == KEYS["a"]["llm"] else "A likes") not in text
    assert any("A likes window seats" in t for k, t in CALLS if k == KEYS["a"]["llm"])
    assert any("B likes aisle seats" in t for k, t in CALLS if k == KEYS["b"]["llm"])
    # B confirming "forget #<A's memory>" removes nothing of A's
    a_mem = rows(env, "SELECT id FROM memories WHERE user_id = %s", a)[0][0]
    card = chat(env, "b", f"forget about #{a_mem}")["card"]
    confirm(env, "b", card["interrupt_id"])
    assert rows(env, "SELECT text FROM memories WHERE id = %s", a_mem) == [("A likes window seats",)]
    # mail watch: each user's own mailbox and state; the same Gmail id in both mailboxes is handled for both
    run_job(env, "mail")  # first run only sets each cursor
    run_job(env, "mail")
    seen = rows(env, "SELECT user_id, message_id FROM mail_seen ORDER BY 1, 2")
    assert seen == sorted([(a, f"msg-{KEYS['a']['google']}"), (a, "msg-shared"),
                           (b, f"msg-{KEYS['b']['google']}"), (b, "msg-shared")])
    assert {r[0] for r in rows(env, "SELECT user_id FROM proactive_state WHERE key = 'mail_cursor'")} == {a, b}
    # audit: every row is its user's, and never mentions the other's data
    audit = rows(env, "SELECT user_id, name, args::text, result::text FROM audit_log")
    assert {r[0] for r in audit} >= {a, b}
    for user_id, name, args, result in audit:
        text = f"{args} {result}"
        if user_id == a:
            assert "B likes" not in text and "B trip" not in text and KEYS["b"]["google"] not in text, (name, text)
        elif user_id == b:
            assert "A likes" not in text and "A trip" not in text and KEYS["a"]["google"] not in text, (name, text)
    assert any("A likes window seats" in r[2] for r in audit if r[0] == a)
    assert any("B likes aisle seats" in r[2] for r in audit if r[0] == b)


def test_checkpoint_threads_distinct(env):
    chat(env, "a", "hello from alpha")
    chat(env, "b", "hello from bravo")
    threads = {r[0] for r in rows(env, "SELECT DISTINCT thread_id FROM checkpoints")}
    assert threads == {f"user:{env.a}", f"user:{env.b}"}
    for who, mine, theirs in (("a", "alpha", "bravo"), ("b", "bravo", "alpha")):
        rt = env.portal.call(env.tenants.get, uid(env, who))
        state = env.portal.call(rt.graph.aget_state, thread_config(uid(env, who)))
        texts = " ".join(str(m.content) for m in state.values["messages"])
        assert f"hello from {mine}" in texts and theirs not in texts
    assert thread_config(env.a)["configurable"]["thread_id"] != thread_config(env.b)["configurable"]["thread_id"]


def test_interrupt_ids_do_not_cross_users(env):
    a_card = chat(env, "a", "remember: A owns a red bike")["card"]
    # B has nothing pending: A's id does nothing for B, and A's card stays pending
    assert confirm(env, "b", a_card["interrupt_id"])["card"] is None
    b_card = chat(env, "b", "remember: B owns a blue car")["card"]
    assert b_card["interrupt_id"] != a_card["interrupt_id"]
    assert confirm(env, "b", a_card["interrupt_id"])["reply"] != "Got it, I'll remember that."
    assert rows(env, "SELECT count(*) FROM memories")[0][0] == 0
    # B's voice session tapping A's id resumes nothing either
    env.stt_scripts.append([])
    with env.c.websocket_connect("/voice", headers={"authorization": f"Bearer {env.tok['b']}"}) as ws:
        read(ws)
        ws.send_text(json.dumps({"type": "confirm", "interrupt_id": a_card["interrupt_id"], "decision": "yes"}))
        until_state(ws, "listening")
    assert rows(env, "SELECT count(*) FROM memories")[0][0] == 0
    # both cards are still pending for their own users, and each owner can confirm theirs
    assert chat(env, "a", "hi")["card"]["interrupt_id"] == a_card["interrupt_id"]
    assert confirm(env, "a", a_card["interrupt_id"])["reply"] == "Got it, I'll remember that."
    assert confirm(env, "b", b_card["interrupt_id"])["reply"] == "Got it, I'll remember that."
    assert sorted(rows(env, "SELECT user_id, text FROM memories")) == [(env.a, "A owns a red bike"), (env.b, "B owns a blue car")]


def test_voice_uses_the_operators_deepgram_key_and_the_callers_thread(env):
    for who in ("b", "a"):
        env.voice_keys.clear()
        CALLS.clear()
        env.stt_scripts.append([FINAL(f"hello from {who} voice")])
        with env.c.websocket_connect("/voice", headers={"authorization": f"Bearer {env.tok[who]}"}) as ws:
            read(ws)
            ping(ws)
            until_state(ws, "speaking")
            until_state(ws, "listening")
        assert env.voice_keys == [("stt", OP["deepgram"]), ("tts", OP["deepgram"])]
        assert CALLS and {k for k, _ in CALLS} == {KEYS[who]["llm"]}
        rt = env.portal.call(env.tenants.get, uid(env, who))
        state = env.portal.call(rt.graph.aget_state, thread_config(uid(env, who)))
        assert f"hello from {who} voice" in str(state.values["messages"])
        assert f"hello from {other(who)} voice" not in str(state.values["messages"])


def test_scheduler_runs_each_user_on_their_own_llm_and_google(env):
    run_job(env, "mail")
    GOOGLE.clear()
    CALLS.clear()
    run_job(env, "mail")
    run_job(env, "sweep")
    assert {tag for tag, _, _ in GOOGLE} == {KEYS["a"]["google"], KEYS["b"]["google"]}
    assert {k for k, _ in CALLS} == {KEYS["a"]["llm"], KEYS["b"]["llm"]}  # one extraction per mail, per user
    for key, text in CALLS:
        who = "a" if key == KEYS["a"]["llm"] else "b"
        assert KEYS[who]["google"] in text and KEYS[other(who)]["google"] not in text  # A's key reads only A's mail
    # a disabled user is skipped entirely
    UserStore(env.pool).set_disabled(env.b, True)
    GOOGLE.clear()
    CALLS.clear()
    run_job(env, "mail")
    run_job(env, "sweep")
    assert {tag for tag, _, _ in GOOGLE} == {KEYS["a"]["google"]} and {k for k, _ in CALLS} <= {KEYS["a"]["llm"]}


def test_me_shows_only_the_callers_own_data(env):
    for who in ("a", "b"):
        me = env.c.get("/me", headers=env.h(who)).json()
        assert me["email"] == f"{who}@example.com"
        assert me["setup"] == {"google": True, "llm": True}
        assert me["keys"] == {"llm_base_url": f"https://llm-{who}.example.com/v1", "llm_key_last4": KEYS[who]["llm"][-4:],
                              "model_fast": f"{who}/fast", "model_strong": f"{who}/strong"}
    assert env.c.get("/me", headers=env.h("b")).json()["tz"] == "Asia/Kolkata"


def test_secrets_absent_from_all_responses_logs_and_audit(env):
    c = env.c
    for who in ("a", "b"):
        h = env.h(who)
        for path in ("/me", "/calendar", "/tasks", "/mail", "/notes", "/dashboard", f"/mail/msg-{KEYS[who]['google']}"):
            c.get(path, headers=h)
        card = chat(env, who, f"remember: {who} prefers tea")["card"]
        confirm(env, who, card["interrupt_id"])
        chat(env, who, "list my tasks")
        env.stt_scripts.append([FINAL("hello")])
        with c.websocket_connect("/voice", headers={"authorization": f"Bearer {env.tok[who]}"}) as ws:
            read(ws)
            ping(ws)
            for kind, frame in until_state(ws, "speaking") + until_state(ws, "listening"):
                env.bodies.append(json.dumps(frame) if kind == "text" else frame.decode())
        # failure paths: a provider that refuses (and echoes) the key, and an unreachable endpoint
        env.net.status[f"bad-{who}.example.com"] = 401
        r = c.put("/settings/llm", headers=h, json={"base_url": f"https://bad-{who}.example.com/v1",
                                                    "api_key": KEYS[who]["llm"] + "X", "model_fast": "m/f"})
        assert r.status_code == 422 and r.json()["error"] == "rejected_key"
        env.net.status[f"down-{who}.example.com"] = 0
        r = c.put("/settings/llm", headers=h, json={"base_url": f"https://down-{who}.example.com/v1",
                                                    "api_key": KEYS[who]["llm"], "model_fast": "m/f"})
        assert r.status_code == 422 and r.json()["error"] == "unreachable"
        run_job(env, "mail")
        run_job(env, "mail")
    env.c.put("/settings/llm", headers=env.h("a"), json={"base_url": "https://llm-a.example.com/v1",
                                                         "api_key": KEYS["a"]["llm"], "model_fast": "a/fast"})
    audit = str(rows(env, "SELECT * FROM audit_log"))
    checkpoints = " ".join(str(r) for t in ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
                           for r in rows(env, f"SELECT * FROM {t}"))
    state = str(rows(env, "SELECT * FROM proactive_state")) + str(rows(env, "SELECT * FROM auth_attempts"))
    logs = env.caplog.text + " ".join(repr(r.args) for r in env.caplog.records)
    assert len(env.bodies) > 40
    for value in planted():
        for where, text in (("response", "\n".join(env.bodies)), ("log", logs), ("audit", audit),
                            ("checkpoint", checkpoints), ("state", state)):
            assert value not in text, (value, where)


ROUTES = [("GET", "/calendar", None), ("GET", "/tasks", None), ("POST", "/tasks", {"title": "x"}),
          ("POST", "/tasks/t1/complete", None), ("GET", "/mail", None), ("GET", "/mail/m1", None),
          ("GET", "/notes", None), ("GET", "/notes/1", None), ("POST", "/chat", {"text": "hi"}),
          ("POST", "/chat/confirm", {"interrupt_id": "i", "decision": "yes"}), ("GET", "/me", None),
          ("PUT", "/me", {"tz": "UTC"}), ("PUT", "/settings/llm", {"base_url": "https://x.example.com/v1", "api_key": "k"}),
          ("GET", "/dashboard", None), ("POST", "/location", {"lat": 1.0, "lon": 2.0}),
          ("PUT", "/device/fcm", {"token": "fcm-x"}),
          ("DELETE", "/calendar/events/e1", None), ("POST", "/calendar/events", {
              "summary": "x", "start": "2026-10-28T10:00:00+01:00", "end": "2026-10-28T11:00:00+01:00"}),
          ("GET", "/memory", None), ("DELETE", "/memory/1", None), ("GET", "/shopping", None),
          ("POST", "/shopping", {"title": "milk"}), ("POST", "/shopping/i1/complete", None),
          ("GET", "/graph/entities", None), ("GET", "/graph/entities/1", None), ("GET", "/graph/entities/1/forget-preview", None),
          ("DELETE", "/graph/entities/1", None), ("PATCH", "/graph/facts/1", {"value": "x"}), ("DELETE", "/graph/facts/1", None),
          ("GET", "/graph/suppressions", None), ("DELETE", "/graph/suppressions", {"key_type": "email", "key_hmac": "x"})]
PUBLIC = {"/", "/privacy", "/health", "/auth/start", "/auth/callback", "/auth/claim", "/voice", "/openapi.json", "/docs",
          "/docs/oauth2-redirect", "/redoc"}


def refused_everywhere(env, header: dict) -> None:
    snapshot = rows(env, "SELECT user_id, name FROM user_secrets ORDER BY 1, 2"), rows(env, "SELECT count(*) FROM audit_log")
    GOOGLE.clear()
    CALLS.clear()
    for method, path, body in ROUTES:
        r = env.c.request(method, path, headers=header, **({"json": body} if body else {}))
        assert r.status_code == 401 and r.json() == {"error": "unauthorized"}, (header, method, path, r.status_code)
    with pytest.raises(WebSocketDisconnect) as e:
        with env.c.websocket_connect("/voice", headers={k.lower(): v for k, v in header.items()}) as ws:
            read(ws)
    assert e.value.code == 4401, header
    assert GOOGLE == [] and CALLS == []
    assert (rows(env, "SELECT user_id, name FROM user_secrets ORDER BY 1, 2"), rows(env, "SELECT count(*) FROM audit_log")) == snapshot


def test_unlisted_and_disabled_users_refused_on_every_route(env):
    # every authenticated route of the app is in the table above
    for path in {r.path for r in env.app.routes} - PUBLIC:
        assert any(re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", path), p) for _, p, _ in ROUTES), path
    # an email not on the list never gets a device token or stored Google credentials
    before = rows(env, "SELECT count(*) FROM devices")[0][0], rows(env, "SELECT count(*) FROM oauth_tokens")[0][0]
    assert sign_in(env.c, "stranger@example.com") is None
    assert (rows(env, "SELECT count(*) FROM devices")[0][0], rows(env, "SELECT count(*) FROM oauth_tokens")[0][0]) == before
    # disabled: B's still-stored token is refused everywhere, voice included; A is unaffected
    UserStore(env.pool).set_disabled(env.b, True)
    refused_everywhere(env, env.h("b"))
    assert sign_in(env.c, "b@example.com") is None
    assert env.c.get("/me", headers=env.h("a")).status_code == 200
    # deleted: A's token stops working with the user gone
    UserStore(env.pool).delete(env.a)
    refused_everywhere(env, env.h("a"))


def test_authorization_header_edge_cases_are_401(env):
    tok = env.tok["a"]
    flipped = tok.swapcase() if tok.swapcase() != tok else tok[:-1] + ("A" if tok[-1] != "A" else "B")
    for header in ({}, {"Authorization": ""}, {"Authorization": "Bearer"}, {"Authorization": "Bearer "},
                   {"Authorization": "Bearer    "}, {"Authorization": tok}, {"Authorization": f"Basic {tok}"},
                   {"Authorization": f"Token {tok}"}, {"Authorization": f"Bearer{tok}"},
                   {"Authorization": f"Bearer {flipped}"}, {"Authorization": f"Bearer {tok}x"},
                   {"Authorization": f"Bearer {tok[:-1]}"}, {"Authorization": f"Bearer {env.tok['b']}{tok}"}):
        refused_everywhere(env, header)
    # the scheme is case-insensitive (RFC 7235); the token itself is not
    assert env.c.get("/me", headers={"Authorization": f"bEaReR {tok}"}).json()["email"] == "a@example.com"


def test_operator_env_keys_unused_by_friend_requests(env):
    c = env.c
    UserStore(env.pool).add("c@example.com", "UTC")
    tok_c = sign_in(c, "c@example.com")
    h = {"Authorization": f"Bearer {tok_c}"}
    uid_c = UserStore(env.pool).by_email("c@example.com").id
    env.net.connections.clear()
    FakeLLM.made = []
    # a friend with no keys of their own: needs setup everywhere, never the operator's keys
    assert c.post("/chat", headers=h, json={"text": "hi"}).json() == {"error": "needs_setup", "missing": "llm"}
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/voice", headers={"authorization": f"Bearer {tok_c}"}) as ws:
            for _ in range(3):
                read(ws)
    assert e.value.code == 4403
    me = c.get("/me", headers=h).json()
    assert me["setup"] == {"google": True, "llm": False}
    rt = env.portal.call(env.tenants.get, uid_c)
    assert rt.llm is None and rt.graph is None and rt.secrets == {}
    run_job(env, "mail")
    run_job(env, "mail")
    assert uid_c not in {r[0] for r in rows(env, "SELECT user_id FROM mail_seen")}  # no LLM: the watch waits for a key
    # friends with keys: every LLM, Deepgram, Tavily and key-check use is their own
    env.web_search_keys.clear()
    for u in (env.a, env.b, uid_c):
        env.tenants.invalidate(u)
    CALLS.clear()
    env.voice_keys.clear()
    for who in ("a", "b"):
        chat(env, who, "list my tasks")
        env.stt_scripts.append([FINAL("hello")])
        with c.websocket_connect("/voice", headers={"authorization": f"Bearer {env.tok[who]}"}) as ws:
            read(ws)
            ping(ws)
            until_state(ws, "speaking")
            until_state(ws, "listening")
        c.put("/settings/llm", headers=env.h(who), json={"base_url": f"https://llm-{who}.example.com/v1",
                                                         "api_key": KEYS[who]["llm"], "model_fast": f"{who}/fast",
                                                         "model_strong": f"{who}/strong"})
    env.portal.call(env.tenants.get, uid_c)
    ops = {OP["llm"]}
    assert set(FakeLLM.made) == {KEYS["a"]["llm"], KEYS["b"]["llm"]}
    assert {k for k, _ in CALLS} == {KEYS["a"]["llm"], KEYS["b"]["llm"]}
    assert {k for _, k in env.voice_keys} == {OP["deepgram"]}  # Deepgram and Tavily are the operator's, shared
    assert set(env.web_search_keys) == {OP["tavily"]}
    assert {auth for _, _, auth in env.net.connections} == {f"Bearer {KEYS['a']['llm']}", f"Bearer {KEYS['b']['llm']}"}
    assert not ops & set(FakeLLM.made)  # the operator's LLM key is never a user's
    # the owner's copy of the env keys stays the owner's
    assert SecretStore(env.pool, FERNET).all(env.owner)["llm_key"] == OP["llm"]


PRIVATE = ["10.0.0.5", "127.0.0.1", "169.254.169.254", "169.254.10.1", "192.168.1.1", "172.16.0.1", "100.64.0.1",
           "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:169.254.169.254", "::ffff:10.0.0.1"]


def test_ssrf_suite_through_put_llm(env):
    c, net, h = env.c, env.net, env.h("a")
    before = SecretStore(env.pool, FERNET).all(env.a)
    cases = []
    # literal addresses (refused before any lookup or connection)
    for ip in PRIVATE:
        cases.append(f"https://[{ip}]/v1" if ":" in ip else f"https://{ip}/v1")
    cases += ["https://localhost/v1", "https://[::ffff:a9fe:a9fe]/v1", "https://[::ffff:7f00:1]:8443/v1"]
    # names that resolve to private, loopback, link-local and metadata addresses
    for i, ip in enumerate(PRIVATE):
        net.dns[f"inner{i}.example.com"] = [ip]
        cases.append(f"https://inner{i}.example.com/v1")
    # a public host that redirects inward
    net.redirect["redirect.example.com"] = "https://169.254.169.254/latest/meta-data/"
    cases.append("https://redirect.example.com/v1")
    # DNS rebinding: public at the first lookup, private from the second on
    net.dns["rebind.example.com"] = [PUBLIC_IP, "127.0.0.1"]
    cases.append("https://rebind.example.com/v1")
    for url in cases:
        net.connections.clear()
        r = c.put("/settings/llm", headers=h, json={"base_url": url, "api_key": KEYS["a"]["llm"],
                                                    "model_fast": "a/fast", "model_strong": "a/strong"})
        assert r.status_code == 422 and r.json()["error"] == "bad_url", (url, r.status_code, r.text)
        assert {ip for ip, _, _ in net.connections} <= {PUBLIC_IP}, (url, net.connections)
    assert net.lookups.count("rebind.example.com") == 2  # every request re-resolved and re-checked
    assert [h_ for _, h_, _ in net.connections] == ["rebind.example.com"]  # only the public first answer was used
    assert SecretStore(env.pool, FERNET).all(env.a) == before


EMBEDDED = [f"{prefix}{v4}" for prefix in ("64:ff9b::", "::", "::ffff:0:") for v4 in ("169.254.169.254", "127.0.0.1")]


def test_ssrf_ipv6_forms_embedding_a_private_ipv4_are_refused(env):
    """NAT64, IPv4-compatible and SIIT addresses reach the embedded IPv4 through a translator: it must be global."""
    c, net, h = env.c, env.net, env.h("a")
    before = SecretStore(env.pool, FERNET).all(env.a)
    for i, ip in enumerate(EMBEDDED):
        net.dns[f"embed{i}.example.com"] = [ip]
        for url in (f"https://embed{i}.example.com/v1", f"https://[{ip}]/v1"):
            net.connections.clear()
            r = c.put("/settings/llm", headers=h, json={"base_url": url, "api_key": KEYS["a"]["llm"],
                                                        "model_fast": "a/fast", "model_strong": "a/strong"})
            assert r.status_code == 422 and r.json()["error"] == "bad_url", (url, r.status_code, r.text)
            assert net.connections == [], url
    assert SecretStore(env.pool, FERNET).all(env.a) == before
    # the same forms around a public IPv4 are still fine
    net.dns["nat64-public.example.com"] = [f"64:ff9b::{PUBLIC_IP}"]
    r = c.put("/settings/llm", headers=h, json={"base_url": "https://nat64-public.example.com/v1",
                                                "api_key": KEYS["a"]["llm"], "model_fast": "a/fast"})
    assert r.status_code == 200, r.text
