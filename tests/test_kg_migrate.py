from datetime import datetime, timezone

from jarvis.kg.memory_store import migrate_memories
from jarvis.kg.store import GraphStore


def _notes(pool, uid):
    kg = GraphStore(pool, uid)
    return kg, kg.facts_for(kg.self_entity().id)


def _legacy(pool, uid, text, created=None):
    with pool.connection() as c:
        c.execute("INSERT INTO memories (user_id, text, created_at) VALUES (%s, %s, coalesce(%s, now()))",
                  (uid, text, created))


def test_migrates_each_memory_once(pool, uids):
    _legacy(pool, uids[0], "Priya is my manager")
    _legacy(pool, uids[0], "I prefer mornings")
    assert migrate_memories(pool) == 2
    assert migrate_memories(pool) == 0
    _, facts = _notes(pool, uids[0])
    assert sorted(f.value for f in facts) == ["I prefer mornings", "Priya is my manager"]
    assert all(f.predicate == "note" and f.source_kind == "user_stated" for f in facts)


def test_migration_is_per_user(pool, uids):
    _legacy(pool, uids[0], "a fact")
    _legacy(pool, uids[1], "b fact")
    assert migrate_memories(pool) == 2
    assert [f.value for f in _notes(pool, uids[0])[1]] == ["a fact"]
    assert [f.value for f in _notes(pool, uids[1])[1]] == ["b fact"]


def test_migration_keeps_original_table(pool, uids):
    _legacy(pool, uids[0], "keep me")
    migrate_memories(pool)
    with pool.connection() as c:
        assert c.execute("SELECT count(*) FROM memories").fetchone()[0] == 1


def test_migrated_fact_keeps_created_at_as_observed_at(pool, uids):
    when = datetime(2024, 1, 2, 3, 4, tzinfo=timezone.utc)
    _legacy(pool, uids[0], "old one", when)
    migrate_memories(pool)
    assert _notes(pool, uids[0])[1][0].observed_at == when


def test_migrates_300_char_memory(pool, uids):
    _legacy(pool, uids[0], "x" * 300)
    assert migrate_memories(pool) == 1
    f = _notes(pool, uids[0])[1][0]
    assert f.value == "x" * 300 and f.excerpt == "x" * 200


def _legacy_ids(pool, uid):
    with pool.connection() as c:
        return [r[0] for r in c.execute("SELECT id FROM memories WHERE user_id = %s ORDER BY id", (uid,)).fetchall()]


def test_forget_then_migrate_again_does_not_resurrect(pool, uids):
    from jarvis.kg.memory_store import GraphMemoryStore
    _legacy(pool, uids[0], "keep")
    _legacy(pool, uids[0], "forget me")
    migrate_memories(pool)
    kg = GraphStore(pool, uids[0])
    mem = GraphMemoryStore(kg)
    gone = next(m for m in mem.all() if m["text"] == "forget me")
    assert mem.remove(gone["id"]) is True
    assert migrate_memories(pool) == 0
    assert [m["text"] for m in mem.all()] == ["keep"]
    assert len(_legacy_ids(pool, uids[0])) == 1


def test_forget_duplicate_text_removes_both_legacy_rows(pool, uids):
    from jarvis.kg.memory_store import GraphMemoryStore
    _legacy(pool, uids[0], "same")
    _legacy(pool, uids[0], "same")
    migrate_memories(pool)
    mem = GraphMemoryStore(GraphStore(pool, uids[0]))
    assert len(mem.all()) == 1
    assert mem.remove(mem.all()[0]["id"]) is True
    assert migrate_memories(pool) == 0 and mem.all() == [] and _legacy_ids(pool, uids[0]) == []


def test_forget_leaves_other_users_legacy_rows(pool, uids):
    from jarvis.kg.memory_store import GraphMemoryStore
    _legacy(pool, uids[0], "mine")
    _legacy(pool, uids[1], "mine")
    migrate_memories(pool)
    mem = GraphMemoryStore(GraphStore(pool, uids[0]))
    mem.remove(mem.all()[0]["id"])
    assert _legacy_ids(pool, uids[0]) == [] and len(_legacy_ids(pool, uids[1])) == 1
    assert migrate_memories(pool) == 0
