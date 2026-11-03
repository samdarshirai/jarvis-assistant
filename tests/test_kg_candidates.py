from datetime import datetime, timedelta, timezone

import pytest

from jarvis.conversations import IDLE
from jarvis.kg.candidates import MAX_AGE, MAX_ATTEMPTS, CandidateStore

NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


def conv(pool, uid, *, last_at=NOW, closed=False, thread=None, surface="chat", extracted=False):
    thread = thread or f"t{uid}-{last_at.isoformat()}-{closed}-{surface}-{extracted}"
    with pool.connection() as c:
        return c.execute(
            "INSERT INTO conversation (user_id, channel, surface, thread_id, created_at, last_at, closed_at, extracted_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (uid, surface, surface, thread, last_at, last_at, last_at if closed else None, NOW if extracted else None)).fetchone()[0]


def row(**over):
    return {"subject_name": "Anna", "subject_kind": "person", "relation": "sister", "predicate": "city", "value": "Pune",
            "quote": "my sister Anna lives in Pune", "subject_norm": "anna", "value_norm": "pune",
            "replaces_fact_id": None, **over}


@pytest.fixture
def cs(pool, uids):
    return CandidateStore(pool, uids[0])


def q(pool, sql, *args):
    with pool.connection() as c:
        return c.execute(sql, args).fetchall()


def test_due_returns_closed_and_idle_open_conversations_oldest_first(pool, uids, cs):
    closed = conv(pool, uids[0], last_at=NOW - timedelta(hours=2), closed=True)
    idle = conv(pool, uids[0], last_at=NOW - IDLE - timedelta(seconds=1), surface="telegram")
    conv(pool, uids[0], last_at=NOW - timedelta(minutes=5), surface="voice")  # open and fresh: still going
    conv(pool, uids[0], last_at=NOW - timedelta(hours=1), closed=True, extracted=True)
    got = cs.due(NOW, 10)
    assert [d.id for d in got] == [closed, idle]
    assert got[0].thread_id and got[0].last_at == NOW - timedelta(hours=2)


def test_idle_boundary_is_the_conversations_rule(pool, uids, cs):
    exactly = conv(pool, uids[0], last_at=NOW - IDLE)
    assert cs.due(NOW, 10) == []  # at exactly 30 minutes ConversationStore.current still reuses it
    assert [d.id for d in cs.due(NOW + timedelta(seconds=1), 10)] == [exactly]


def test_due_ignores_old_conversations_and_respects_the_limit(pool, uids, cs):
    conv(pool, uids[0], last_at=NOW - MAX_AGE - timedelta(seconds=1), closed=True)
    ids = [conv(pool, uids[0], last_at=NOW - timedelta(hours=h), closed=True) for h in (5, 4, 3)]
    assert [d.id for d in cs.due(NOW, 2)] == ids[:2]


def test_due_is_per_user(pool, uids, cs):
    conv(pool, uids[1], last_at=NOW - timedelta(hours=1), closed=True)
    assert cs.due(NOW, 10) == []


def test_save_stores_rows_and_marks_extracted_once(pool, uids, cs):
    c = conv(pool, uids[0], last_at=NOW - timedelta(hours=1), closed=True)
    last = NOW - timedelta(hours=1)
    assert cs.save(c, last, [row(), row(predicate="employer", value="Infosys", value_norm="infosys")], NOW) is True
    assert q(pool, "SELECT count(*), min(status) FROM fact_candidate WHERE conversation_id = %s", c) == [(2, "new")]
    assert q(pool, "SELECT extracted_at FROM conversation WHERE id = %s", c) == [(NOW,)]
    assert cs.save(c, last, [row(value="Delhi", value_norm="delhi")], NOW) is False  # the loser of a race
    assert q(pool, "SELECT count(*) FROM fact_candidate")[0][0] == 2
    assert cs.due(NOW, 10) == []


def test_save_with_no_rows_only_marks(pool, uids, cs):
    c = conv(pool, uids[0], last_at=NOW - timedelta(hours=1), closed=True)
    assert cs.save(c, NOW - timedelta(hours=1), [], NOW) is True
    assert q(pool, "SELECT count(*) FROM fact_candidate")[0][0] == 0
    assert cs.due(NOW, 10) == []


def test_save_is_refused_when_the_conversation_moved_on(pool, uids, cs):
    c = conv(pool, uids[0], last_at=NOW - timedelta(hours=1), closed=True)
    seen = NOW - timedelta(hours=1)
    with pool.connection() as conn:  # resumed and extended while the model was thinking
        conn.execute("UPDATE conversation SET last_at = %s WHERE id = %s", (NOW, c))
    assert cs.save(c, seen, [row()], NOW) is False
    assert q(pool, "SELECT count(*) FROM fact_candidate")[0][0] == 0
    assert q(pool, "SELECT extracted_at FROM conversation WHERE id = %s", c) == [(None,)]


def test_save_stores_replaces_fact_id_and_relation(pool, uids, cs):
    c = conv(pool, uids[0], last_at=NOW - timedelta(hours=1), closed=True)
    with pool.connection() as conn:
        e = conn.execute("INSERT INTO entity (user_id, kind, display_name) VALUES (%s, 'person', 'Anna') RETURNING id", (uids[0],)).fetchone()[0]
        f = conn.execute("INSERT INTO fact (user_id, subject_id, predicate, object_value) VALUES (%s, %s, 'city', 'Delhi') RETURNING id",
                         (uids[0], e)).fetchone()[0]
    cs.save(c, NOW - timedelta(hours=1), [row(replaces_fact_id=f)], NOW)
    assert q(pool, "SELECT replaces_fact_id, relation FROM fact_candidate") == [(f, "sister")]


def test_fail_gives_up_on_the_last_attempt(pool, uids, cs):
    c = conv(pool, uids[0], last_at=NOW - timedelta(hours=1), closed=True)
    for _ in range(MAX_ATTEMPTS - 1):
        assert cs.fail(c, NOW) is False
        assert [d.id for d in cs.due(NOW, 10)] == [c]
    assert cs.fail(c, NOW) is True
    assert q(pool, "SELECT extract_attempts, extracted_at FROM conversation WHERE id = %s", c) == [(MAX_ATTEMPTS, NOW)]
    assert cs.due(NOW, 10) == []
    assert q(pool, "SELECT count(*) FROM fact_candidate")[0][0] == 0


def test_known_counts_new_approved_and_dismissed_but_not_expired(pool, uids, cs):
    c = conv(pool, uids[0], last_at=NOW - timedelta(hours=1), closed=True)
    cs.save(c, NOW - timedelta(hours=1), [row(value_norm=v, value=v) for v in ("a", "b", "c", "d")], NOW)
    with pool.connection() as conn:
        for v, st in (("b", "approved"), ("c", "dismissed"), ("d", "expired")):
            conn.execute("UPDATE fact_candidate SET status = %s WHERE value_norm = %s", (st, v))
    keys = [("anna", "city", v) for v in ("a", "b", "c", "d", "e")]
    assert cs.known(keys) == {("anna", "city", v) for v in "abc"}
    assert cs.known([]) == set()


def test_known_is_per_user(pool, uids, cs):
    other = CandidateStore(pool, uids[1])
    c = conv(pool, uids[1], last_at=NOW - timedelta(hours=1), closed=True)
    other.save(c, NOW - timedelta(hours=1), [row()], NOW)
    assert cs.known([("anna", "city", "pune")]) == set()
    assert other.known([("anna", "city", "pune")]) == {("anna", "city", "pune")}


def test_another_user_cannot_save_or_fail_my_conversation(pool, uids, cs):
    c = conv(pool, uids[0], last_at=NOW - timedelta(hours=1), closed=True)
    other = CandidateStore(pool, uids[1])
    assert other.save(c, NOW - timedelta(hours=1), [row()], NOW) is False
    assert other.fail(c, NOW) is False
    assert q(pool, "SELECT extract_attempts, extracted_at FROM conversation WHERE id = %s", c) == [(0, None)]
