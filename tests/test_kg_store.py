import unicodedata
from concurrent.futures import ThreadPoolExecutor

import pytest

from jarvis.kg.store import GraphStore, norm


@pytest.fixture
def store(pool, uids):
    return GraphStore(pool, uids[0])


def test_norm():
    assert norm("  ANNA \t Müller ") == "anna müller"


def test_two_annas_stay_separate(store):
    a = store.create_entity("Anna Müller")
    b = store.create_entity("Anna Schmidt")
    assert {e.id for e in store.find_by_name("Anna")} == {a.id, b.id}
    assert [e.id for e in store.find_by_name("Anna Müller")] == [a.id]


def test_find_is_case_and_unicode_insensitive(store):
    a = store.create_entity("Anna Müller")
    for q in ("anna müller", "ANNA MÜLLER", unicodedata.normalize("NFD", "Anna Müller")):
        assert [e.id for e in store.find_by_name(q)] == [a.id]


def test_alias_and_kin_word_resolve(store):
    a = store.create_entity("Sarah", aliases=("Sis",))
    assert [e.id for e in store.find_by_name("sis")] == [a.id]


def test_kind_filter(store):
    store.create_entity("Paris", kind="place")
    assert store.find_by_name("paris", kind="person") == []
    assert len(store.find_by_name("paris", kind="place")) == 1


def test_blank_and_whitespace_names_rejected(store):
    for n in ("", "   ", "\t\n"):
        with pytest.raises(ValueError):
            store.create_entity(n)
    with pytest.raises(ValueError):
        store.create_entity("X", kind="bogus")
    assert store.find_by_name("   ") == []


def test_wildcards_apostrophes_emoji_match_literally(store):
    ents = [store.create_entity(n) for n in ("O'Brien", "100%_sure", "🙂")]
    assert store.find_by_name("%") == []
    assert store.find_by_name("_") == []
    assert [e.id for e in store.find_by_name("O'Brien")] == [ents[0].id]
    assert [e.id for e in store.find_by_name("100%_sure")] == [ents[1].id]
    assert [e.id for e in store.find_by_name("🙂")] == [ents[2].id]


def test_other_users_entities_invisible(pool, uids, store):
    e = store.create_entity("Anna")
    other = GraphStore(pool, uids[1])
    assert other.find_by_name("Anna") == []
    assert other.get_entity(e.id) is None
    assert other.list_entities() == []


def test_merged_entities_excluded(pool, uids, store):
    a, b = store.create_entity("Anna"), store.create_entity("Anna")
    with pool.connection() as c:
        c.execute("UPDATE entity SET merged_into = %s WHERE id = %s", (b.id, a.id))
    assert [e.id for e in store.find_by_name("anna")] == [b.id]


def test_self_entity_is_unique_per_user(pool, uids, store):
    assert store.self_entity() == store.self_entity()
    with ThreadPoolExecutor(8) as ex:
        ids = {f.result().id for f in [ex.submit(GraphStore(pool, uids[1]).self_entity) for _ in range(8)]}
    assert len(ids) == 1
    assert store.self_entity().id != GraphStore(pool, uids[1]).self_entity().id


def test_shared_key_flagged_on_second_holder(pool, store):
    a, b = store.create_entity("Anna"), store.create_entity("Anne")
    store.add_key(a.id, "email", " Foo@X.com ")
    store.add_key(a.id, "email", "foo@x.com")  # no-op
    with pool.connection() as c:
        assert c.execute("SELECT count(*) FROM entity_key WHERE type='email'").fetchone()[0] == 1
        assert not c.execute("SELECT shared FROM entity_key WHERE type='email'").fetchone()[0]
    store.add_key(b.id, "email", "foo@x.com")
    with pool.connection() as c:
        assert [r[0] for r in c.execute("SELECT shared FROM entity_key WHERE type='email'")] == [True, True]


def test_phone_normalised_and_bad_type(store):
    a = store.create_entity("Anna")
    store.add_key(a.id, "phone", "+49 (170) 123-4567")
    with store.pool.connection() as c:
        assert c.execute("SELECT value_norm FROM entity_key WHERE type='phone'").fetchone()[0] == "+491701234567"
    with pytest.raises(ValueError):
        store.add_key(a.id, "ssn", "1")


def test_concurrent_add_key_flags_both(pool, uids):
    from threading import Barrier
    s = GraphStore(pool, uids[0])
    a, b = s.create_entity("Anna"), s.create_entity("Anne")
    for i in range(15):
        bar, val = Barrier(2), f"r{i}@x.com"

        def go(eid):
            bar.wait()
            s.add_key(eid, "email", val)
        with ThreadPoolExecutor(2) as ex:
            for f in [ex.submit(go, a.id), ex.submit(go, b.id)]:
                f.result()
        with pool.connection() as c:
            assert [r[0] for r in c.execute("SELECT shared FROM entity_key WHERE value_norm = %s", (val,))] == [True, True]
