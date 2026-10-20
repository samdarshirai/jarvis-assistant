import asyncio
import logging
from datetime import date
from types import SimpleNamespace as NS

from cryptography.fernet import Fernet
import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from langgraph.checkpoint.memory import InMemorySaver

from jarvis.appapi import AppService
from jarvis.agent.graph import build_graph
from jarvis.google.auth import ReauthRequired
from jarvis.main import create_app
from jarvis.llm import LLMCreds, LLMProvider
from jarvis.tenant import Tenants, thread_config
from jarvis.tools.registry import Registry
from jarvis.users import UserStore
from jarvis.voice.devices import Devices

TZ = "Europe/Berlin"
CARD = NS(id="i1", value={"actions": [{"tool": "create_event", "args": {}, "summary": "Create X"}]})
TAP = NS(id="i2", value={"actions": [{"tool": "send_draft", "args": {}}], "after_untrusted": True})


class Src:
    """One fake for every Google/notes source; exc makes every call raise."""
    def __init__(self, exc=None):
        self.exc, self.calls = exc, []

    def _go(self, name, *a, ret=None):
        self.calls.append((name, *a))
        if self.exc:
            raise self.exc
        return ret

    def list_events(self, s, e, query=None, limit=50):
        return self._go("events", s, e, ret=[
            {"id": "e1", "summary": "A", "start": "2026-10-05T09:00:00+02:00", "end": "2026-10-05T10:00:00+02:00", "location": None},
            {"id": "e2", "summary": None, "start": "2026-10-05", "end": "2026-10-06", "location": "Home"}])

    def list_tasks(self, *a):
        return self._go("list", ret=[{"id": "t2", "title": "B", "due": None}, {"id": "t1", "title": "A", "due": "2000-01-01"}])

    def create_task(self, title, due=None):
        return self._go("create", title, due, ret={"id": "t9", "title": title, "due": due and due.isoformat(), "status": "needsAction"})

    def complete_task(self, tid):
        return self._go("complete", tid, ret={"id": tid, "status": "completed"})

    def search_emails(self, q, limit=10):
        self._go("search", q, limit)
        return [{"id": f"m{i}", "from": "a@x", "subject": "S", "date": "d", "snippet": "sn"} for i in range(limit)]

    def read_email(self, mid):
        return self._go("read", mid, ret={"id": mid, "from": "a", "subject": "S", "date": "d", "body": "hi", "truncated": True})

    def recent(self, limit=20):
        return self._go("recent", ret=[{"id": 1, "title": "T", "snippet": "s", "updated_at": "u"}])

    def get(self, nid):
        return self._go("get", nid, ret={"id": nid, "title": "T", "body": "b", "updated_at": "u"} if nid == 1 else None)


class Audit:
    def __init__(self):
        self.rows = []

    def record(self, kind, name, **kw):
        self.rows.append((kind, name, kw))


class Graph:
    def __init__(self, interrupts=(), result=None, exc=None):
        self.interrupts, self.result, self.exc, self.inputs = list(interrupts), result, exc, []

    async def aget_state(self, cfg):
        return NS(interrupts=self.interrupts)

    async def ainvoke(self, inp, cfg):
        self.inputs.append((inp, cfg))
        if self.exc:
            raise self.exc
        return self.result


def rt(src=None, graph=None, audit=None, uid=1):
    """A stand-in UserRuntime: the service reads every client, the graph, the lock and the audit from it."""
    src = src or Src()
    return NS(user=NS(id=uid), calendar=src, tasks=src, gmail=src, notes=src, graph=graph or Graph(),
              lock=asyncio.Lock(), audit=audit or Audit(), tz=TZ, llm_ready=graph is not False)


def svc(**kw):
    return AppService(None, None, **kw)


async def test_calendar_shapes_clamps_window_and_flags_all_day():
    src = Src()
    out = await svc().calendar_events(rt(src), date(2026, 10, 5), 99)
    assert out["events"][0] == {"id": "e1", "summary": "A", "start": "2026-10-05T09:00:00+02:00",
                                "end": "2026-10-05T10:00:00+02:00", "location": None, "all_day": False}
    assert out["events"][1]["all_day"] and out["events"][1]["summary"] == "(no title)" and out["reauth"] is False
    _, a, b = src.calls[0]
    assert (b - a).days == 14 and a.hour == 0 and a.tzinfo is not None


async def test_tasks_sorted_with_overdue_and_failure_is_null():
    out = await svc().task_list(rt())
    assert [(t["id"], t["overdue"]) for t in out["tasks"]] == [("t1", True), ("t2", False)]
    out = await svc().task_list(rt(Src(RuntimeError())))
    assert out == {"tasks": None, "reauth": False}


async def test_reauth_sets_flag():
    assert await svc().task_list(rt(Src(ReauthRequired("x")))) == {"tasks": None, "reauth": True}
    assert (await svc().mail_list(rt(Src(ReauthRequired("x"))), 5))["reauth"] is True


async def test_task_writes_are_audited_as_app():
    audit = Audit()
    s, r = svc(), rt(audit=audit)
    t = await s.create_task(r, "Milk", date(2026, 10, 9))
    assert t["title"] == "Milk" and t["due"] == "2026-10-09"
    assert await s.complete_task(r, "t9")
    assert [(r[0], r[1]) for r in audit.rows] == [("app", "create_task"), ("app", "complete_task")]
    assert audit.rows[0][2]["args"]["title"] == "Milk"


async def test_failed_task_write_returns_none_and_no_audit():
    audit = Audit()
    s, r = svc(), rt(Src(RuntimeError()), audit=audit)
    assert await s.create_task(r, "x", None) is None and not await s.complete_task(r, "t")
    assert audit.rows == []


async def test_audit_failure_does_not_hide_the_write():
    class Bad(Audit):
        def record(self, *a, **k):
            raise RuntimeError

    assert await svc().complete_task(rt(audit=Bad()), "t1")


async def test_mail_list_query_clamp_and_more():
    src = Src()
    out = await svc().mail_list(rt(src), 3)
    assert src.calls[0] == ("search", "is:unread in:inbox newer_than:7d", 4)
    assert out["count"] == 3 and out["more"] is True and out["items"][0] == {
        "id": "m0", "from": "a@x", "subject": "S", "date": "d", "snippet": "sn"}
    assert (await svc().mail_list(rt(Src()), 500))["count"] == 20
    assert (await svc().mail_list(rt(Src(RuntimeError())), 5))["items"] is None


async def test_mail_item_and_not_found():
    assert (await svc().mail_item(rt(), "m1"))["truncated"] is True
    assert await svc().mail_item(rt(Src(RuntimeError())), "m1") is None


async def test_notes():
    assert (await svc().note_list(rt()))["notes"][0]["snippet"] == "s"
    assert (await svc().note_item(rt(), 1))["body"] == "b" and await svc().note_item(rt(), 2) is None


async def test_chat_replies_on_plain_thread():
    g = Graph(result={"messages": [HumanMessage("hi"), AIMessage("Hello")], "client_actions": [{"a": 1}]})
    out = await svc().chat(rt(graph=g), "hi")
    assert out == {"reply": "Hello", "card": None, "client_actions": [{"a": 1}]}
    cfg = g.inputs[0][1]
    assert cfg["configurable"] == {"thread_id": "user:1"}


async def test_chat_empty_fallback_and_new_interrupt_card():
    g = Graph(result={"messages": [HumanMessage("hi")]})
    assert "no summary" in (await svc().chat(rt(graph=g), "hi"))["reply"]
    g = Graph(result={"messages": [], "__interrupt__": [TAP]})
    out = await svc().chat(rt(graph=g), "send it")
    assert out["reply"] is None and out["card"] == {
        "interrupt_id": "i2", "summary": "send_draft: {}", "tap_only": True, "after_untrusted": True}


async def test_chat_with_pending_interrupt_reoffers_without_invoking():
    g = Graph(interrupts=[CARD])
    out = await svc().chat(rt(graph=g), "hello")
    assert out["card"]["interrupt_id"] == "i1" and out["card"]["summary"] == "Create X" and g.inputs == []


async def test_chat_failure_reoffers_pending_card():
    out = await svc().chat(rt(graph=Graph(exc=RuntimeError("boom"))), "hi")
    assert out["reply"].startswith("Something went wrong") and out["card"] is None
    g = Graph(exc=RuntimeError())
    orig = g.aget_state
    calls = []

    async def state(cfg):  # nothing pending before the run, a card after the failure
        calls.append(1)
        return NS(interrupts=[] if len(calls) == 1 else [CARD])

    g.aget_state = state
    out = await svc().chat(rt(graph=g), "hi")
    assert out["reply"].startswith("Something went wrong") and out["card"]["interrupt_id"] == "i1"


@pytest.mark.parametrize("failure", ["body", "request_chain"])
async def test_provider_error_body_never_reaches_logs_audit_reply_or_checkpoint(caplog, failure):
    key = "sk-synthetic-provider-body-secret"
    audit = Audit()
    provider = LLMProvider(LLMCreds("https://provider.example/v1", key, ["m"], ["m"]), audit)

    def reject(request):
        assert request.headers["Authorization"] == f"Bearer {key}"
        if failure == "request_chain":
            try:
                raise RuntimeError(f"chained secret: {key}")
            except RuntimeError as error:
                raise httpx.ReadError(f"request secret: {key}", request=request) from error
        return httpx.Response(401, json={"error": {"message": f"Rejected API key: {key}", "type": "invalid_api_key"}})

    await provider.http.aclose()
    provider.http = httpx.AsyncClient(transport=httpx.MockTransport(reject))
    saver = InMemorySaver()
    graph = build_graph(provider, Registry(), audit, saver, TZ)
    try:
        with caplog.at_level(logging.DEBUG):
            out = await svc().chat(rt(graph=graph, audit=audit, uid=2), "hello")
        checkpoints = [item async for item in saver.alist(thread_config(2))]
    finally:
        await provider.aclose()
    errors = [value for item in checkpoints for _, channel, value in item.pending_writes if channel == "__error__"]
    assert errors, "The failing graph step must have a checkpoint error write to inspect"
    assert audit.rows
    assert out["reply"].startswith("Something went wrong")
    for surface in (caplog.text, str(audit.rows), str(out), repr(checkpoints)):
        assert key not in surface


async def test_chat_failure_logs_neither_raw_errors_nor_their_chains(caplog):
    key = "sk-synthetic-chained-error-secret"

    def fail():
        try:
            raise RuntimeError(f"request Authorization: Bearer {key}")
        except RuntimeError as error:
            raise ValueError("provider failed") from error

    class Broken(Graph):
        async def ainvoke(self, inp, cfg):
            fail()

        async def aget_state(self, cfg):
            if self.inputs:
                fail()
            self.inputs.append(cfg)
            return NS(interrupts=[])

    with caplog.at_level(logging.ERROR):
        out = await svc().chat(rt(graph=Broken()), "hello")
    assert "graph run failed" in caplog.text
    assert "could not re-offer pending confirmation" in caplog.text
    assert key not in caplog.text
    assert key not in str(out)


async def test_confirm_stale_or_mismatched_does_not_resume():
    for g, iid, dec in [(Graph(), "i1", "yes"), (Graph(interrupts=[CARD]), "other", "yes"), (Graph(interrupts=[CARD]), "i1", "maybe")]:
        out = await svc().confirm(rt(graph=g), iid, dec)
        assert out["reply"] == "Already handled." and out["card"] is None and g.inputs == []


async def test_confirm_resumes_with_bool():
    for dec, want in (("yes", True), ("no", False)):
        g = Graph(interrupts=[CARD], result={"messages": [AIMessage("Done")]})
        out = await svc().confirm(rt(graph=g), "i1", dec)
        assert out["reply"] == "Done" and isinstance(g.inputs[0][0], Command) and g.inputs[0][0].resume is want


async def test_step_survives_cancelled_request_and_is_drainable():
    gate = asyncio.Event()

    class Slow(Graph):
        async def ainvoke(self, inp, cfg):
            await gate.wait()
            return {"messages": [AIMessage("ok")]}

    s, r = svc(), rt(graph=Slow())
    t = asyncio.create_task(s.chat(r, "hi"))
    await asyncio.sleep(0.05)
    t.cancel()
    await asyncio.sleep(0)
    assert len(s.steps) == 1 and r.lock.locked()
    gate.set()
    await asyncio.wait(set(s.steps), timeout=2)
    assert not s.steps and not r.lock.locked()


# --- HTTP layer ---
H = {"Authorization": "Bearer good"}


def client(r=None, built=True):
    """The routes with a service whose only paired device ("good") belongs to runtime r."""
    app = create_app(with_lifespan=False)
    if built:
        s = svc()

        async def authenticate(header):
            return r if header == "Bearer good" else None

        s.authenticate = authenticate
        app.state.app_api = s
    return TestClient(app)


def test_every_route_503_before_service_and_401_with_bad_token():
    routes = [("get", "/calendar"), ("get", "/tasks"), ("post", "/tasks"), ("post", "/tasks/x/complete"), ("get", "/mail"),
              ("get", "/mail/x"), ("get", "/notes"), ("get", "/notes/1"), ("post", "/chat"), ("post", "/chat/confirm")]
    bodies = {"/tasks": {"title": "a"}, "/chat": {"text": "a"}, "/chat/confirm": {"interrupt_id": "i", "decision": "yes"}}
    for c, code, hdr in ((client(built=False), 503, H), (client(rt()), 401, {}), (client(rt()), 401, {"Authorization": "Bearer no"})):
        for m, p in routes:
            assert getattr(c, m)(p, headers=hdr, **({"json": bodies[p]} if m == "post" and p in bodies else {})).status_code == code, p


def test_http_calendar_task_mail_notes_contract():
    c = client(rt())
    assert c.get("/calendar?from=nope", headers=H).json() == {"error": "bad_request"}
    assert c.get("/calendar?from=nope", headers=H).status_code == 422
    assert c.get("/calendar?from=2026-10-05&days=3", headers=H).json()["events"][0]["id"] == "e1"
    assert c.get("/tasks", headers=H).json()["tasks"][0]["id"] == "t1"
    r = c.post("/tasks", headers=H, json={"title": "  Milk  ", "due": "2026-10-09"})
    assert r.json()["task"]["title"] == "Milk"
    assert c.post("/tasks", headers=H, json={"title": "   "}).json() == {"error": "bad_request"}
    assert c.post("/tasks", headers=H, json={"title": "x" * 201}).status_code == 422
    assert c.post("/tasks/t1/complete", headers=H).json() == {"ok": True}
    assert c.get("/mail?limit=2", headers=H).json()["count"] == 2
    assert c.get("/mail/m1", headers=H).json()["body"] == "hi"
    assert c.get("/notes", headers=H).json()["notes"][0]["id"] == 1
    assert c.get("/notes/1", headers=H).json()["body"] == "b"
    assert c.get("/notes/2", headers=H).status_code == 404


def test_http_404_for_unreadable_mail_and_502_for_failed_write():
    c = client(rt(Src(RuntimeError())))
    assert c.get("/mail/x", headers=H).json() == {"error": "not_found"}
    assert c.post("/tasks/t/complete", headers=H).status_code == 502


def test_http_chat_validation_and_confirm():
    g = Graph(interrupts=[CARD], result={"messages": [AIMessage("Done")]})
    c = client(rt(graph=g))
    assert c.post("/chat", headers=H, json={"text": ""}).status_code == 422
    assert c.post("/chat", headers=H, json={"text": "x" * 2001}).status_code == 422
    assert c.post("/chat/confirm", headers=H, json={"interrupt_id": "i1", "decision": "maybe"}).status_code == 422
    assert c.post("/chat", headers=H, json={"text": "hi"}).json()["card"]["interrupt_id"] == "i1"
    assert c.post("/chat/confirm", headers=H, json={"interrupt_id": "i1", "decision": "yes"}).json()["reply"] == "Done"


def test_http_chat_without_llm_key_is_needs_setup():
    r = client(rt(graph=False)).post("/chat", headers=H, json={"text": "hi"})
    assert r.status_code == 409 and r.json() == {"error": "needs_setup", "missing": "llm"}
    r = client(rt(graph=False)).post("/chat/confirm", headers=H, json={"interrupt_id": "i", "decision": "yes"})
    assert r.status_code == 409


# --- per user, against real Tenants and Devices ---
class Tagged(Src):
    """Src whose rows name the user they belong to."""
    def __init__(self, tag):
        super().__init__()
        self.tag = tag

    def list_events(self, s, e, query=None, limit=50):
        return [{**ev, "id": self.tag} for ev in super().list_events(s, e)]

    def list_tasks(self, *a):
        return [{**t, "id": self.tag} for t in super().list_tasks()]

    def search_emails(self, q, limit=10):
        return [{**m, "id": self.tag} for m in super().search_emails(q, limit)]


def tenants(pool):
    return Tenants(pool, NS(fernet_key=Fernet.generate_key().decode(), models=lambda tier: [tier]), InMemorySaver(),
                   google_service_factory=lambda *a: object(), llm_provider_factory=lambda *a: None)


async def users(pool, uids):
    """(service, header_a, header_b, runtime_a, runtime_b) with each user's Google clients replaced by a Tagged fake."""
    devices, t = Devices(pool), tenants(pool)
    ha, hb = (f"Bearer {devices.create(u)[1]}" for u in uids)
    s = AppService(devices, t)
    ra, rb = await s.authenticate(ha), await s.authenticate(hb)
    for r, tag in ((ra, "A"), (rb, "B")):
        r.calendar = r.tasks = r.gmail = Tagged(tag)
    return s, ha, hb, ra, rb


async def test_app_endpoints_use_callers_google_clients(pool, uids):
    s, ha, hb, ra, rb = await users(pool, uids)
    await asyncio.to_thread(ra.notes.create, "A note")
    await asyncio.to_thread(rb.notes.create, "B note")
    for h, tag in ((ha, "A"), (hb, "B")):
        r = await s.authenticate(h)
        assert {e["id"] for e in (await s.calendar_events(r, date(2026, 10, 5), 1))["events"]} == {tag}
        assert {t["id"] for t in (await s.task_list(r))["tasks"]} == {tag}
        assert {m["id"] for m in (await s.mail_list(r, 3))["items"]} == {tag}
        assert [n["title"] for n in (await s.note_list(r))["notes"]] == [f"{tag} note"]


async def test_note_of_other_user_404(pool, uids):
    s, ha, hb, ra, rb = await users(pool, uids)
    note = await asyncio.to_thread(ra.notes.create, "secret")
    assert (await s.note_item(ra, note["id"]))["title"] == "secret"
    assert await s.note_item(rb, note["id"]) is None  # the route turns None into 404


async def test_chat_uses_user_thread_and_lock(pool, uids):
    s, ha, hb, ra, rb = await users(pool, uids)
    ga, gb = (Graph(result={"messages": [AIMessage("ok")]}) for _ in "ab")
    ra.graph, rb.graph = ga, gb
    await s.chat(ra, "hi")
    await s.chat(rb, "hi")
    assert ga.inputs[0][1]["configurable"]["thread_id"] == f"user:{uids[0]}"
    assert gb.inputs[0][1]["configurable"]["thread_id"] == f"user:{uids[1]}"
    assert ra.lock is s.tenants.lock(uids[0]) and ra.lock is not rb.lock


async def test_two_users_chat_concurrently_not_serialised(pool, uids):
    s, ha, hb, ra, rb = await users(pool, uids)
    gate = asyncio.Event()

    class Blocked(Graph):
        async def ainvoke(self, inp, cfg):
            await gate.wait()
            return {"messages": [AIMessage("a")]}

    ra.graph, rb.graph = Blocked(), Graph(result={"messages": [AIMessage("b")]})
    a = asyncio.create_task(s.chat(ra, "hi"))
    await asyncio.sleep(0.05)
    assert ra.lock.locked()
    assert (await asyncio.wait_for(s.chat(rb, "hi"), 2))["reply"] == "b"  # B is not queued behind A's step
    gate.set()
    assert (await a)["reply"] == "a"


async def test_disabled_user_gets_401_on_next_request(pool, uids):
    s, ha, hb, ra, rb = await users(pool, uids)
    assert await s.authenticate(ha) is ra  # cached runtime still says enabled
    UserStore(pool).set_disabled(uids[0], True)  # what the users CLI does from another process
    assert await s.authenticate(ha) is None and await s.authenticate(hb) is rb
    UserStore(pool).set_disabled(uids[0], False)
    assert await s.authenticate(ha) is ra  # re-enabling takes effect without a restart
    UserStore(pool).set_disabled(uids[1], True)
    s.tenants.invalidate(uids[1])
    assert (await s.tenants.get(uids[1])).user.disabled  # e.g. a scheduler job cached it while disabled
    UserStore(pool).set_disabled(uids[1], False)
    assert (await s.authenticate(hb)).user.id == uids[1]  # the stale cached flag does not lock them out
