import asyncio
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from jarvis.conversations import IDLE, ConversationStore, messages_json


class Clock:
    now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture(params=["pg"])
def clock():
    return Clock()


@pytest.fixture
def store(request, pool, uids, clock):
    return ConversationStore(pool, uids[0], clock)


def closed_at(pool, cid):
    with pool.connection() as c:
        return c.execute("SELECT closed_at FROM conversation WHERE id = %s", (cid,)).fetchone()[0]


def test_current_creates_then_reuses(store, uids):
    a = store.current("chat")
    assert store.current("chat").id == a.id
    assert a.thread_id.startswith(f"user:{uids[0]}:c:")


def test_idle_boundary(store, clock, pool):
    a = store.current("chat")
    clock.now += IDLE
    assert store.current("chat").id == a.id
    clock.now += timedelta(seconds=1)
    b = store.current("chat")
    assert b.id != a.id
    assert closed_at(pool, a.id) is not None


def test_surfaces_are_independent(store):
    c, t, v = store.current("chat"), store.current("telegram"), store.start("voice")
    assert len({c.id, t.id, v.id}) == 3
    v2 = store.start("voice")
    assert v2.id != v.id and store.get(v.id).closed_at is not None


def test_race_single_open(store, pool, uids):
    barrier, out = threading.Barrier(2), []

    def go():
        barrier.wait()
        out.append(store.current("chat"))

    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(out) == 2
    with pool.connection() as c:
        n = c.execute("SELECT count(*) FROM conversation WHERE user_id = %s AND surface = 'chat' AND closed_at IS NULL",
                      (uids[0],)).fetchone()[0]
    assert n == 1


def test_touch_sets_title_once(store):
    a = store.current("chat")
    store.touch(a.id, "  hello  ")
    assert store.get(a.id).title == "hello"
    store.touch(a.id, "other")
    assert store.get(a.id).title == "hello"
    b = store.start("chat")
    store.touch(b.id, "   ")
    assert store.get(b.id).title is None
    store.touch(b.id, "x" * 80)
    assert store.get(b.id).title == "x" * 60


def test_close_idempotent_and_resume(store, clock, pool):
    old = store.current("chat")
    store.close(old.id)
    first = closed_at(pool, old.id)
    clock.now += timedelta(minutes=1)
    store.close(old.id)
    assert closed_at(pool, old.id) == first
    cur = store.current("chat")
    assert cur.id != old.id
    r = store.resume(old.id, "chat")
    assert r.closed_at is None and r.surface == "chat" and r.id == old.id
    assert store.get(cur.id).closed_at is not None
    v = store.start("voice")
    assert store.resume(v.id, "chat") is None
    assert store.resume(99999, "chat") is None


def test_other_user_cannot_get_or_resume(pool, uids, clock):
    a, b = ConversationStore(pool, uids[0], clock), ConversationStore(pool, uids[1], clock)
    conv = a.current("chat")
    assert b.get(conv.id) is None
    assert b.resume(conv.id, "chat") is None
    assert conv.id not in [c.id for c in b.list()]


def test_legacy_thread_imported_once(pool, uids, clock):
    uid = uids[0]
    with pool.connection() as c:
        c.execute("DROP TABLE IF EXISTS checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations")
    s = ConversationStore(pool, uid, clock)
    assert s.list() == []  # no checkpoint tables: no error
    try:
        with pool.connection() as c:
            c.execute("CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL)")
            c.execute("INSERT INTO checkpoints VALUES (%s, 'c1')", (f"user:{uid}",))
        s.list()
        got = s.list()
        assert len(got) == 1
        assert got[0].thread_id == f"user:{uid}" and got[0].title == "Earlier conversation"
        assert got[0].closed_at is not None
        assert ConversationStore(pool, uids[1], clock).list() == []
    finally:
        with pool.connection() as c:
            c.execute("DROP TABLE IF EXISTS checkpoints")


def test_messages_json():
    msgs = [HumanMessage("hi"), AIMessage("", tool_calls=[{"name": "t", "args": {}, "id": "1"}]),
            ToolMessage("r", tool_call_id="1"), AIMessage("done")]

    class G:
        def __init__(self, values):
            self.values = values

        async def aget_state(self, cfg):
            return SimpleNamespace(values=self.values)

    assert asyncio.run(messages_json(G({"messages": msgs}), "t")) == [
        {"role": "user", "text": "hi"}, {"role": "assistant", "text": "done"}]
    assert asyncio.run(messages_json(G({}), "t")) == []
