import asyncio
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from langgraph.checkpoint.memory import InMemorySaver

from jarvis.tenant import Tenants, UnknownUser, auth_runtime, cached_service, thread_config
from jarvis.users import SecretStore, UserStore
from jarvis.voice.devices import DeviceAuth

KEY = Fernet.generate_key().decode()


class FakeLLM:
    def __init__(self, creds, audit):
        self.creds, self.audit, self.closed = creds, audit, False

    def get(self, tier, tools=None):
        raise AssertionError("not called in these tests")

    async def aclose(self):
        self.closed = True


def settings(tavily=""):
    return SimpleNamespace(fernet_key=KEY, tavily_api_key=tavily, models=lambda tier: [f"{tier}-model"])


def tenants(pool, **kw):
    tavily = kw.pop("tavily", "")
    return Tenants(pool, settings(tavily), InMemorySaver(), google_service_factory=lambda name, version, store, key: object(),
                   llm_provider_factory=FakeLLM, **kw)


def test_thread_config_shape():
    assert thread_config(7) == {"configurable": {"thread_id": "user:7"}, "recursion_limit": 40}
    assert thread_config(7, voice=True) == {"configurable": {"thread_id": "user:7", "voice": True}, "recursion_limit": 40}


async def test_get_builds_scoped_runtime(pool, uids):
    a, b = uids
    t = tenants(pool)
    ra, rb = await t.get(a), await t.get(b)
    assert (ra.user.id, rb.user.id) == (a, b)
    assert (ra.audit.user_id, ra.kg.user_id, ra.notes.user_id, ra.proactive.user_id) == (a, a, a, a)
    assert (rb.audit.user_id, rb.kg.user_id, rb.notes.user_id, rb.proactive.user_id) == (b, b, b, b)
    for name in ("audit", "kg", "notes", "proactive", "calendar", "tasks", "gmail", "registry", "lock", "secrets"):
        assert getattr(ra, name) is not getattr(rb, name), name
    assert ra.lock is t.lock(a) and rb.lock is t.lock(b)
    assert await t.get(a) is ra  # cached
    with pytest.raises(UnknownUser):
        await t.get(999_999)


async def test_runtime_without_llm_key_has_no_graph(pool, uids):
    a, b = uids
    SecretStore(pool, KEY).put(b, "llm_key", "sk-secret-b")
    t = tenants(pool)
    ra, rb = await t.get(a), await t.get(b)
    assert ra.llm is None and ra.graph is None and not ra.llm_ready
    assert rb.llm_ready and rb.graph is not None and rb.llm.creds.api_key == "sk-secret-b"
    assert "sk-secret-b" not in repr(rb)


async def test_invalidate_rebuilds(pool, uids):
    a, _ = uids
    t = tenants(pool)
    r1 = await t.get(a)
    SecretStore(pool, KEY).put(a, "llm_key", "sk-a")
    assert await t.get(a) is r1 and not r1.llm_ready  # still the cached runtime
    t.invalidate(a)
    r2 = await t.get(a)
    assert r2 is not r1 and r2.llm_ready
    assert r2.lock is r1.lock  # a running step keeps its lock across the rebuild


async def test_lru_evicts_but_lock_survives(pool, uids):
    a, b = uids
    SecretStore(pool, KEY).put(a, "llm_key", "sk-a")
    t = tenants(pool, max_size=1)
    ra = await t.get(a)
    lock = t.lock(a)
    async with lock:  # a step of a's is running: b's request must not wait on it
        await asyncio.wait_for(t.get(b), 5)  # evicts a
    assert not ra.llm.closed  # holders without the lock (scheduler, owner channels) keep a working client
    ra2 = await t.get(a)
    assert ra2 is not ra and ra2.lock is lock and t.lock(a) is lock
    await t.close()
    assert ra.llm.closed and ra2.llm.closed  # everything is closed at shutdown


async def test_tavily_key_selects_tavily_search(pool, uids, monkeypatch):
    a, b = uids
    made = []
    monkeypatch.setattr("jarvis.tenant.WebSearch", lambda key: made.append(key) or SimpleNamespace())
    monkeypatch.setattr("jarvis.tenant.DdgSearch", lambda: made.append("ddg") or SimpleNamespace())
    t = tenants(pool, tavily="tv-op")
    await t.get(a)
    await t.get(b)
    assert made == ["tv-op", "tv-op"]  # one operator key for everyone
    made.clear()
    await tenants(pool).get(a)
    assert made == ["ddg"]


async def test_auth_runtime(pool, uids):
    a, b = uids
    UserStore(pool).set_disabled(b, True)
    t = tenants(pool)
    devices = SimpleNamespace(verify=lambda tok: {"ta": DeviceAuth(1, a), "tb": DeviceAuth(2, b),
                                                  "tx": DeviceAuth(3, 999_999)}.get(tok))
    assert (await auth_runtime(devices, t, "Bearer ta")).user.id == a
    assert (await auth_runtime(devices, t, "bearer  ta ")).user.id == a
    for header in ("", "ta", "Bearer ", "Bearer nope", "Bearer tb", "Bearer tx", "Basic ta"):
        assert await auth_runtime(devices, t, header) is None, header


def test_cached_service_rebuilds_when_the_epoch_moves():
    built, epoch = [], [0]
    get = cached_service(lambda: built.append(1) or len(built), epoch=lambda: epoch[0])
    assert get() == get() == 1
    epoch[0] += 1
    assert get() == 2


async def test_invalidate_makes_the_old_runtime_rebuild_google_clients(pool, uids):
    """Long-lived holders of a runtime (owner channels until per-request lookup) pick up new Google consent."""
    a, _ = uids
    built = []
    t = Tenants(pool, settings(), InMemorySaver(), llm_provider_factory=FakeLLM,
                google_service_factory=lambda name, version, store, key: built.append(name) or object())
    r = await t.get(a)
    first = r.tasks._svc()
    assert r.tasks._svc() is first
    t.invalidate(a)
    assert r.tasks._svc() is not first


async def test_scheduler_scans_of_every_user_retire_no_runtime(pool, uids):
    from jarvis.proactive.scheduler import run_for_users
    from jarvis.users import MAX_USERS

    users = UserStore(pool)
    for i in range(40 - len(uids)):  # more users than the old 32-runtime cache held
        users.add(f"u{i}@example.com", "Europe/Berlin")
    t = tenants(pool)
    assert t.max_size >= MAX_USERS

    async def job(rt, notifier, llm, tz, now):
        pass

    for _ in range(3):  # every tick of every job walks all users
        await run_for_users("sweep", job, users, t, lambda uid: True, lambda u: None)
    assert len(t._cache) == 40 and t._retired == []
    await t.close()


async def test_runtime_always_has_a_graph(pool, uids):
    from jarvis.kg.store import GraphStore
    rt = await tenants(pool).get(uids[0])
    assert isinstance(rt.kg, GraphStore)
    assert all(rt.registry.get(n) is None for n in ("remember", "forget", "recall"))


SITUATION = "Right now"
GRAPH_TOOLS = {"remember_fact", "graph_lookup", "forget_entity"}


async def test_registers_graph_tools_in_memory_domain(pool, uids):
    rt = await tenants(pool).get(uids[0])
    for n in GRAPH_TOOLS:
        assert rt.registry.get(n).domain == "memory"


class RecordingProvider:
    def __init__(self, creds, audit):
        self.seen = []

    def get(self, tier, tools=None, tool_choice=None):
        from langchain_core.runnables import RunnableLambda
        return RunnableLambda(lambda msgs: self.seen.append(msgs[0].content) or "ok")

    async def aclose(self):
        pass


async def _prompt(pool, uid, monkeypatch):
    from langchain_core.messages import HumanMessage, SystemMessage
    import jarvis.tenant as tn
    captured = []
    monkeypatch.setattr(tn, "build_graph", lambda provider, *a, **k: captured.append(provider) or object())
    SecretStore(pool, KEY).put(uid, "llm_key", "sk-x")
    t = tenants(pool)
    t.llm_provider_factory = RecordingProvider
    rt = await t.get(uid)
    rt.kg.add_fact(rt.kg.self_entity().id, "note", "I like tea", source_kind="user_stated")
    rt.registry.get("remember_fact").fn(subject="Meera", predicate="city", value="Pune", relation="sister")
    await captured[0].get("fast").ainvoke([SystemMessage("BASE"), HumanMessage("call Meera")], config={})
    return _seen(captured[0])


def _seen(p):
    while not hasattr(p, "seen"):
        p = p.provider
    return p.seen[-1]


async def test_prompt_order_is_base_graph_situation(pool, uids, monkeypatch):
    text = await _prompt(pool, uids[0], monkeypatch)
    marks = [text.index(m) for m in ("BASE", "<graph>", "I like tea", "Pune")]
    assert marks == sorted(marks)
    assert text.rindex("</graph>") < text.index(SITUATION)
