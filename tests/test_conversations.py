import asyncio
import os
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from jarvis.conversations import IDLE, ConversationStore, messages_json

from .fakes import MemoryConversations


class Clock:
    now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture(params=["pg", "memory"])
def store(request, clock):
    if request.param == "memory":
        return MemoryConversations(1, clock)
    return ConversationStore(request.getfixturevalue("pool"), request.getfixturevalue("uids")[0], clock)




def test_current_creates_then_reuses(store):
    a = store.current("chat")
    assert store.current("chat").id == a.id
    assert a.thread_id.startswith(f"user:{store.user_id}:c:")


def test_idle_boundary(store, clock):
    a = store.current("chat")
    clock.now += IDLE
    assert store.current("chat").id == a.id
    clock.now += timedelta(seconds=1)
    b = store.current("chat")
    assert b.id != a.id
    assert store.get(a.id).closed_at is not None


def test_surfaces_are_independent(store):
    c, t, v = store.current("chat"), store.current("telegram"), store.start("voice")
    assert len({c.id, t.id, v.id}) == 3
    v2 = store.start("voice")
    assert v2.id != v.id and store.get(v.id).closed_at is not None


def test_race_single_open(pool, uids, clock):
    store = ConversationStore(pool, uids[0], clock)
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


def test_close_idempotent_and_resume(store, clock):
    old = store.current("chat")
    store.close(old.id)
    first = store.get(old.id).closed_at
    clock.now += timedelta(minutes=1)
    store.close(old.id)
    assert store.get(old.id).closed_at == first
    cur = store.current("chat")
    assert cur.id != old.id
    r = store.resume(old.id, "chat")
    assert r.closed_at is None and r.surface == "chat" and r.id == old.id
    assert store.get(cur.id).closed_at is not None
    v = store.start("voice")
    assert store.resume(v.id, "chat") is None
    assert store.resume(99999, "chat") is None


def test_resume_stale_open_voice(store, clock):
    v = store.start("voice")
    chat = store.current("chat")
    clock.now += IDLE
    assert store.resume(v.id, "chat") is None  # within IDLE: a live voice session
    clock.now += timedelta(seconds=1)
    r = store.resume(v.id, "chat")  # past IDLE: left open by a redeploy, resumable
    assert r.id == v.id and r.surface == "chat" and r.closed_at is None and r.last_at == clock.now
    assert store.get(chat.id).closed_at is not None
    assert store.open_for("voice") is None and store.open_for("chat").id == v.id


def test_other_user_cannot_get_or_resume(pool, uids, clock):
    a, b = ConversationStore(pool, uids[0], clock), ConversationStore(pool, uids[1], clock)  # pg only: the twin is single-user
    conv = a.current("chat")
    assert b.get(conv.id) is None
    assert b.resume(conv.id, "chat") is None
    assert conv.id not in [c.id for c in b.list()]


@pytest.fixture
def scratch(pool):
    """A pool whose search_path is a private schema only, so there is no checkpoints table until a test makes one; the shared tables are never touched."""
    from psycopg_pool import ConnectionPool

    with pool.connection() as c:
        c.execute("DROP SCHEMA IF EXISTS scratch CASCADE")
        c.execute("CREATE SCHEMA scratch")
        c.execute("CREATE TABLE scratch.conversation (LIKE public.conversation INCLUDING ALL)")  # all list() touches besides checkpoints
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], kwargs={"options": "-c search_path=scratch"}, open=True)
    yield p
    p.close()
    with pool.connection() as c:
        c.execute("DROP SCHEMA scratch CASCADE")


def test_legacy_thread_imported_once(scratch, uids, clock):
    uid = uids[0]
    s = ConversationStore(scratch, uid, clock)
    assert s.list() == []  # no checkpoint rows for this user: nothing imported, no error
    with scratch.connection() as c:
        c.execute("CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL)")
        c.execute("INSERT INTO checkpoints VALUES (%s, 'c1')", (f"user:{uid}",))
    s.list()
    got = s.list()
    assert len(got) == 1
    assert got[0].thread_id == f"user:{uid}" and got[0].title == "Earlier conversation"
    assert got[0].closed_at is not None
    assert ConversationStore(scratch, uids[1], clock).list() == []


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


def test_messages_json_keeps_last_200():
    class G:
        async def aget_state(self, cfg):
            return SimpleNamespace(values={"messages": [HumanMessage(f"m{i}") for i in range(250)]})

    out = asyncio.run(messages_json(G(), "t"))
    assert len(out) == 200 and out[0]["text"] == "m50" and out[-1]["text"] == "m249"


def test_legacy_import_is_marked_extracted(scratch, uids, clock):
    uid = uids[0]
    with scratch.connection() as c:
        c.execute("CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL)")
        c.execute("INSERT INTO checkpoints VALUES (%s, 'c1')", (f"user:{uid}",))
    ConversationStore(scratch, uid, clock).list()
    with scratch.connection() as c:  # the untrimmed pre-conversations thread is never bulk-extracted
        assert c.execute("SELECT extracted_at FROM conversation WHERE user_id = %s", (uid,)).fetchone()[0] is not None


def test_resume_clears_extraction_state(pool, uids, clock):
    s = ConversationStore(pool, uids[0], clock)
    old = s.current("chat")
    s.close(old.id)
    with pool.connection() as c:
        c.execute("UPDATE conversation SET extracted_at = now(), extract_attempts = 2 WHERE id = %s", (old.id,))
    s.start("chat")
    assert s.resume(old.id, "chat").id == old.id
    with pool.connection() as c:
        assert c.execute("SELECT extracted_at, extract_attempts FROM conversation WHERE id = %s", (old.id,)).fetchone() == (None, 0)
