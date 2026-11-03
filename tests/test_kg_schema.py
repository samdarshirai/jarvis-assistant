import psycopg
import pytest
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from jarvis.db import init_schema

KG_TABLES = ["entity", "entity_key", "source", "fact", "fact_source", "suppression"]


def ent(c, uid, name="Anna"):
    return c.execute("INSERT INTO entity (user_id, kind, display_name) VALUES (%s, 'person', %s) RETURNING id",
                     (uid, name)).fetchone()[0]


def src(c, uid, ref="m1"):
    return c.execute("INSERT INTO source (user_id, kind, ref, observed_at, trust) VALUES (%s, 'email', %s, now(), 30)"
                     " RETURNING id", (uid, ref)).fetchone()[0]


def fact(c, uid, subj, pred="city", **kw):
    return c.execute("INSERT INTO fact (user_id, subject_id, predicate, object_value, object_entity_id)"
                     " VALUES (%s, %s, %s, 'x', %s) RETURNING id", (uid, subj, pred, kw.get("obj"))).fetchone()[0]


def test_init_schema_is_idempotent(pool):
    init_schema(pool)
    init_schema(pool)


def test_fact_cannot_point_at_another_users_entity_as_subject(pool, uids):
    with pool.connection() as c:
        e = ent(c, uids[0])
        with pytest.raises(ForeignKeyViolation):
            fact(c, uids[1], e)


def test_fact_cannot_point_at_another_users_entity_as_object(pool, uids):
    with pool.connection() as c:
        e0, e1 = ent(c, uids[0]), ent(c, uids[1])
        with pytest.raises(ForeignKeyViolation):
            fact(c, uids[1], e1, obj=e0)


def test_entity_key_cannot_point_at_another_users_entity(pool, uids):
    with pool.connection() as c:
        e = ent(c, uids[0])
        with pytest.raises(ForeignKeyViolation):
            c.execute("INSERT INTO entity_key (user_id, entity_id, type, value_norm) VALUES (%s, %s, 'email', 'a@x')",
                      (uids[1], e))


def test_fact_source_cannot_cross_users(pool, uids):
    with pool.connection() as c:
        e0, e1 = ent(c, uids[0]), ent(c, uids[1])
        f0, f1 = fact(c, uids[0], e0), fact(c, uids[1], e1)
        s0, s1 = src(c, uids[0]), src(c, uids[1])
        with pytest.raises(ForeignKeyViolation):
            c.execute("INSERT INTO fact_source (user_id, fact_id, source_id) VALUES (%s, %s, %s)", (uids[1], f0, s1))
    with pool.connection() as c:
        e0, e1 = ent(c, uids[0]), ent(c, uids[1])
        f1, s0 = fact(c, uids[1], e1), src(c, uids[0], "m2")
        with pytest.raises(ForeignKeyViolation):
            c.execute("INSERT INTO fact_source (user_id, fact_id, source_id) VALUES (%s, %s, %s)", (uids[1], f1, s0))


def test_merged_into_cannot_cross_users(pool, uids):
    with pool.connection() as c:
        e0 = ent(c, uids[0])
        with pytest.raises(ForeignKeyViolation):
            c.execute("INSERT INTO entity (user_id, kind, display_name, merged_into) VALUES (%s, 'person', 'B', %s)",
                      (uids[1], e0))


def test_superseded_by_cannot_cross_users(pool, uids):
    with pool.connection() as c:
        e0, e1 = ent(c, uids[0]), ent(c, uids[1])
        f0 = fact(c, uids[0], e0)
        with pytest.raises(ForeignKeyViolation):
            c.execute("INSERT INTO fact (user_id, subject_id, predicate, superseded_by) VALUES (%s, %s, 'note', %s)",
                      (uids[1], e1, f0))


def test_deleting_user_cascades_graph_rows(pool, uids):
    with pool.connection() as c:
        e = ent(c, uids[0])
        c.execute("INSERT INTO entity_key (user_id, entity_id, type, value_norm) VALUES (%s, %s, 'email', 'a@x')",
                  (uids[0], e))
        f, s = fact(c, uids[0], e), src(c, uids[0])
        c.execute("INSERT INTO fact_source (user_id, fact_id, source_id) VALUES (%s, %s, %s)", (uids[0], f, s))
        c.execute("INSERT INTO suppression (user_id, key_type, key_hmac, kind) VALUES (%s, 'email', 'h', 'hard')",
                  (uids[0],))
    with pool.connection() as c:
        c.execute("DELETE FROM users WHERE id = %s", (uids[0],))
        for t in KG_TABLES:
            assert c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0, t


def test_deleting_entity_removes_its_rows_and_nulls_merge_pointer(pool, uids):
    with pool.connection() as c:
        e, e2 = ent(c, uids[0]), ent(c, uids[0], "Ann")
        c.execute("UPDATE entity SET merged_into = %s WHERE id = %s", (e, e2))
        c.execute("INSERT INTO entity_key (user_id, entity_id, type, value_norm) VALUES (%s, %s, 'email', 'a@x')",
                  (uids[0], e))
        fact(c, uids[0], e)
        c.execute("DELETE FROM entity WHERE id = %s", (e,))
        assert c.execute("SELECT count(*) FROM fact").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM entity_key").fetchone()[0] == 0
        assert c.execute("SELECT merged_into, user_id FROM entity WHERE id = %s", (e2,)).fetchone() == (None, uids[0])


def test_hard_key_unique_per_holder(pool, uids):
    q = "INSERT INTO entity_key (user_id, entity_id, type, value_norm) VALUES (%s, %s, 'email', 'a@x')"
    with pool.connection() as c:
        a, b = ent(c, uids[0]), ent(c, uids[0], "Bob")
        c.execute(q, (uids[0], a))
        c.execute(q, (uids[0], b))  # same key on a second entity is allowed
        with pytest.raises(UniqueViolation):
            c.execute(q, (uids[0], a))


def test_two_active_single_valued_facts_rejected(pool, uids):
    with pool.connection() as c:
        e = ent(c, uids[0])
        fact(c, uids[0], e, "friend_of")
        fact(c, uids[0], e, "friend_of")
        fact(c, uids[0], e, "city")
        with pytest.raises(UniqueViolation):
            fact(c, uids[0], e, "city")


def test_check_constraints_reject_bad_values(pool, uids):
    with pool.connection() as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO entity (user_id, kind, display_name) VALUES (%s, 'alien', 'x')", (uids[0],))


def conv(c, uid, thread="t1"):
    return c.execute("INSERT INTO conversation (user_id, channel, surface, thread_id, closed_at)"
                     " VALUES (%s, 'chat', 'chat', %s, now())"
                     " RETURNING id", (uid, thread)).fetchone()[0]


def cand(c, uid, conv_id, **kw):
    row = {"subject_name": "Anna", "subject_kind": "person", "predicate": "city", "value": "Pune",
           "quote": "Anna lives in Pune", "subject_norm": "anna", "value_norm": "pune", **kw}
    cols = ", ".join(row)
    return c.execute(f"INSERT INTO fact_candidate (user_id, conversation_id, {cols})"
                     f" VALUES (%s, %s, {', '.join(['%s'] * len(row))}) RETURNING id", (uid, conv_id, *row.values())).fetchone()[0]


def test_candidate_defaults_and_cascade_from_conversation(pool, uids):
    with pool.connection() as c:
        cv = conv(c, uids[0])
        cid = cand(c, uids[0], cv)
        assert c.execute("SELECT status, decided_at FROM fact_candidate WHERE id = %s", (cid,)).fetchone() == ("new", None)
        c.execute("DELETE FROM conversation WHERE id = %s", (cv,))
        assert c.execute("SELECT count(*) FROM fact_candidate").fetchone()[0] == 0


def test_candidate_cascades_from_user(pool, uids):
    with pool.connection() as c:
        cand(c, uids[1], conv(c, uids[1]))
        c.execute("DELETE FROM users WHERE id = %s", (uids[1],))
        assert c.execute("SELECT count(*) FROM fact_candidate").fetchone()[0] == 0


def test_candidate_cannot_point_at_another_users_conversation(pool, uids):
    with pool.connection() as c:
        cv = conv(c, uids[0])
        with pytest.raises(ForeignKeyViolation):
            cand(c, uids[1], cv)


def test_candidate_rejects_bad_status_and_kind(pool, uids):
    with pool.connection() as c:
        cv = conv(c, uids[0])
        with pytest.raises(psycopg.errors.CheckViolation):
            cand(c, uids[0], cv, status="maybe")
    with pool.connection() as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            cand(c, uids[0], conv(c, uids[0], "t2"), subject_kind="alien")


def test_deleting_the_replaced_fact_keeps_the_candidate(pool, uids):
    with pool.connection() as c:
        e = ent(c, uids[0])
        f = fact(c, uids[0], e)
        cid = cand(c, uids[0], conv(c, uids[0]), replaces_fact_id=f)
        c.execute("DELETE FROM fact WHERE id = %s", (f,))
        assert c.execute("SELECT replaces_fact_id FROM fact_candidate WHERE id = %s", (cid,)).fetchone() == (None,)


def test_candidate_cannot_replace_another_users_fact(pool, uids):
    with pool.connection() as c:
        f = fact(c, uids[0], ent(c, uids[0]))
        with pytest.raises(ForeignKeyViolation):
            cand(c, uids[1], conv(c, uids[1]), replaces_fact_id=f)


@pytest.fixture
def scratch(pool):
    import os
    from psycopg_pool import ConnectionPool

    with pool.connection() as c:
        c.execute("DROP SCHEMA IF EXISTS scratch CASCADE")
        c.execute("CREATE SCHEMA scratch")
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], kwargs={"options": "-c search_path=scratch"}, open=True)
    yield p
    p.close()
    with pool.connection() as c:
        c.execute("DROP SCHEMA scratch CASCADE")


def test_migration_marks_existing_conversations_extracted_once(scratch):
    """A database from before the extractor: its conversations must not be bulk-extracted (spec non-goal)."""
    init_schema(scratch)
    with scratch.connection() as c:
        uid = c.execute("INSERT INTO users (email, tz) VALUES ('o@example.com', 'UTC') RETURNING id").fetchone()[0]
        c.execute("ALTER TABLE conversation DROP COLUMN extracted_at, DROP COLUMN extract_attempts")
        old = conv(c, uid, "old")
    init_schema(scratch)
    with scratch.connection() as c:
        got = c.execute("SELECT extracted_at, extract_attempts FROM conversation WHERE id = %s", (old,)).fetchone()
        assert got[0] is not None and got[1] == 0
        fresh = conv(c, uid, "fresh")
        assert c.execute("SELECT extracted_at FROM conversation WHERE id = %s", (fresh,)).fetchone() == (None,)
    init_schema(scratch)  # a re-run must not touch rows
    with scratch.connection() as c:
        assert c.execute("SELECT extracted_at FROM conversation WHERE id = %s", (fresh,)).fetchone() == (None,)
