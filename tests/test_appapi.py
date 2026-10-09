import asyncio
from datetime import date
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from jarvis.appapi import AppService
from jarvis.google.auth import ReauthRequired
from jarvis.main import create_app

TZ = "Europe/Berlin"
CARD = NS(id="i1", value={"actions": [{"tool": "create_event", "args": {}, "summary": "Create X"}]})
TAP = NS(id="i2", value={"actions": [{"tool": "send_draft", "args": {}}], "after_untrusted": True})


class Devices:
    def verify(self, token):
        return 1 if token == "good" else None


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


def svc(src=None, graph=None, audit=None, **kw):
    src = src or Src()
    return AppService(Devices(), src, src, src, src, graph or Graph(), asyncio.Lock(), audit or Audit(), TZ, **kw)


async def test_authorized_matches_dashboard():
    s = svc()
    assert await s.authorized("Bearer good") and not await s.authorized("Bearer bad") and not await s.authorized("")


async def test_calendar_shapes_clamps_window_and_flags_all_day():
    src = Src()
    out = await svc(src).calendar_events(date(2026, 10, 5), 99)
    assert out["events"][0] == {"id": "e1", "summary": "A", "start": "2026-10-05T09:00:00+02:00",
                                "end": "2026-10-05T10:00:00+02:00", "location": None, "all_day": False}
    assert out["events"][1]["all_day"] and out["events"][1]["summary"] == "(no title)" and out["reauth"] is False
    _, a, b = src.calls[0]
    assert (b - a).days == 14 and a.hour == 0 and a.tzinfo is not None


async def test_tasks_sorted_with_overdue_and_failure_is_null():
    out = await svc().task_list()
    assert [(t["id"], t["overdue"]) for t in out["tasks"]] == [("t1", True), ("t2", False)]
    out = await svc(Src(RuntimeError())).task_list()
    assert out == {"tasks": None, "reauth": False}


async def test_reauth_sets_flag():
    assert await svc(Src(ReauthRequired("x"))).task_list() == {"tasks": None, "reauth": True}
    assert (await svc(Src(ReauthRequired("x"))).mail_list(5))["reauth"] is True


async def test_task_writes_are_audited_as_app():
    audit = Audit()
    s = svc(audit=audit)
    t = await s.create_task("Milk", date(2026, 10, 9))
    assert t["title"] == "Milk" and t["due"] == "2026-10-09"
    assert await s.complete_task("t9")
    assert [(r[0], r[1]) for r in audit.rows] == [("app", "create_task"), ("app", "complete_task")]
    assert audit.rows[0][2]["args"]["title"] == "Milk"


async def test_failed_task_write_returns_none_and_no_audit():
    audit = Audit()
    s = svc(Src(RuntimeError()), audit=audit)
    assert await s.create_task("x", None) is None and not await s.complete_task("t")
    assert audit.rows == []


async def test_audit_failure_does_not_hide_the_write():
    class Bad(Audit):
        def record(self, *a, **k):
            raise RuntimeError

    assert await svc(audit=Bad()).complete_task("t1")


async def test_mail_list_query_clamp_and_more():
    src = Src()
    out = await svc(src).mail_list(3)
    assert src.calls[0] == ("search", "is:unread in:inbox newer_than:7d", 4)
    assert out["count"] == 3 and out["more"] is True and out["items"][0] == {
        "id": "m0", "from": "a@x", "subject": "S", "date": "d", "snippet": "sn"}
    assert (await svc(Src()).mail_list(500))["count"] == 20
    assert (await svc(Src(RuntimeError())).mail_list(5))["items"] is None


async def test_mail_item_and_not_found():
    assert (await svc().mail_item("m1"))["truncated"] is True
    assert await svc(Src(RuntimeError())).mail_item("m1") is None


async def test_notes():
    assert (await svc().note_list())["notes"][0]["snippet"] == "s"
    assert (await svc().note_item(1))["body"] == "b" and await svc().note_item(2) is None


async def test_chat_replies_on_plain_thread():
    g = Graph(result={"messages": [HumanMessage("hi"), AIMessage("Hello")], "client_actions": [{"a": 1}]})
    out = await svc(graph=g).chat("hi")
    assert out == {"reply": "Hello", "card": None, "client_actions": [{"a": 1}]}
    cfg = g.inputs[0][1]
    assert cfg["configurable"] == {"thread_id": "owner"}


async def test_chat_empty_fallback_and_new_interrupt_card():
    g = Graph(result={"messages": [HumanMessage("hi")]})
    assert "no summary" in (await svc(graph=g).chat("hi"))["reply"]
    g = Graph(result={"messages": [], "__interrupt__": [TAP]})
    out = await svc(graph=g).chat("send it")
    assert out["reply"] is None and out["card"] == {
        "interrupt_id": "i2", "summary": "send_draft: {}", "tap_only": True, "after_untrusted": True}


async def test_chat_with_pending_interrupt_reoffers_without_invoking():
    g = Graph(interrupts=[CARD])
    out = await svc(graph=g).chat("hello")
    assert out["card"]["interrupt_id"] == "i1" and out["card"]["summary"] == "Create X" and g.inputs == []


async def test_chat_failure_reoffers_pending_card():
    s = svc(graph=Graph(exc=RuntimeError("boom")))
    out = await s.chat("hi")
    assert out["reply"].startswith("Something went wrong") and out["card"] is None
    g = Graph(exc=RuntimeError())
    orig = g.aget_state
    calls = []

    async def state(cfg):  # nothing pending before the run, a card after the failure
        calls.append(1)
        return NS(interrupts=[] if len(calls) == 1 else [CARD])

    g.aget_state = state
    out = await svc(graph=g).chat("hi")
    assert out["reply"].startswith("Something went wrong") and out["card"]["interrupt_id"] == "i1"


async def test_confirm_stale_or_mismatched_does_not_resume():
    for g, iid, dec in [(Graph(), "i1", "yes"), (Graph(interrupts=[CARD]), "other", "yes"), (Graph(interrupts=[CARD]), "i1", "maybe")]:
        out = await svc(graph=g).confirm(iid, dec)
        assert out["reply"] == "Already handled." and out["card"] is None and g.inputs == []


async def test_confirm_resumes_with_bool():
    for dec, want in (("yes", True), ("no", False)):
        g = Graph(interrupts=[CARD], result={"messages": [AIMessage("Done")]})
        out = await svc(graph=g).confirm("i1", dec)
        assert out["reply"] == "Done" and isinstance(g.inputs[0][0], Command) and g.inputs[0][0].resume is want


async def test_step_survives_cancelled_request_and_is_drainable():
    gate = asyncio.Event()

    class Slow(Graph):
        async def ainvoke(self, inp, cfg):
            await gate.wait()
            return {"messages": [AIMessage("ok")]}

    s = svc(graph=Slow())
    t = asyncio.create_task(s.chat("hi"))
    await asyncio.sleep(0.05)
    t.cancel()
    await asyncio.sleep(0)
    assert len(s.steps) == 1 and s.lock.locked()
    gate.set()
    await asyncio.wait(set(s.steps), timeout=2)
    assert not s.steps and not s.lock.locked()


# --- HTTP layer ---
H = {"Authorization": "Bearer good"}


def client(s=None):
    app = create_app(with_lifespan=False)
    if s is not None:
        app.state.app_api = s
    return TestClient(app)


def test_every_route_503_before_service_and_401_with_bad_token():
    routes = [("get", "/calendar"), ("get", "/tasks"), ("post", "/tasks"), ("post", "/tasks/x/complete"), ("get", "/mail"),
              ("get", "/mail/x"), ("get", "/notes"), ("get", "/notes/1"), ("post", "/chat"), ("post", "/chat/confirm")]
    bodies = {"/tasks": {"title": "a"}, "/chat": {"text": "a"}, "/chat/confirm": {"interrupt_id": "i", "decision": "yes"}}
    for c, code, hdr in ((client(), 503, H), (client(svc()), 401, {}), (client(svc()), 401, {"Authorization": "Bearer no"})):
        for m, p in routes:
            assert getattr(c, m)(p, headers=hdr, **({"json": bodies[p]} if m == "post" and p in bodies else {})).status_code == code, p


def test_http_calendar_task_mail_notes_contract():
    c = client(svc())
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
    c = client(svc(Src(RuntimeError())))
    assert c.get("/mail/x", headers=H).json() == {"error": "not_found"}
    assert c.post("/tasks/t/complete", headers=H).status_code == 502


def test_http_chat_validation_and_confirm():
    g = Graph(interrupts=[CARD], result={"messages": [AIMessage("Done")]})
    c = client(svc(graph=g))
    assert c.post("/chat", headers=H, json={"text": ""}).status_code == 422
    assert c.post("/chat", headers=H, json={"text": "x" * 2001}).status_code == 422
    assert c.post("/chat/confirm", headers=H, json={"interrupt_id": "i1", "decision": "maybe"}).status_code == 422
    assert c.post("/chat", headers=H, json={"text": "hi"}).json()["card"]["interrupt_id"] == "i1"
    assert c.post("/chat/confirm", headers=H, json={"interrupt_id": "i1", "decision": "yes"}).json()["reply"] == "Done"
