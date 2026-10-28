import threading
from datetime import datetime, timedelta, timezone

import pytest

from jarvis.kg.store import Fact, GraphStore

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def day(n: int) -> datetime:
    return T0 + timedelta(days=n)


@pytest.fixture
def store(pool, uids):
    return GraphStore(pool, uids[0])


@pytest.fixture
def anna(store):
    return store.create_entity("Anna")


def _links(pool, fact_id):
    with pool.connection() as c:
        return c.execute("SELECT source_id, first_seen, last_confirmed_at FROM fact_source WHERE fact_id = %s",
                         (fact_id,)).fetchall()


def test_user_stated_city_is_stored_with_provenance(store, anna):
    f = store.add_fact(anna.id, "city", "  Pune ", source_kind="user_stated", excerpt="Anna lives in Pune",
                       observed_at=day(1))
    assert isinstance(f, Fact)
    assert (f.subject_id, f.predicate, f.value, f.status) == (anna.id, "city", "Pune", "active")
    assert (f.source_kind, f.excerpt, f.observed_at, f.valid_from, f.valid_to) == (
        "user_stated", "Anna lives in Pune", day(1), day(1), None)
    assert f.recorded_at is not None
    assert store.facts_for(anna.id) == [f]


def test_newer_user_fact_supersedes_older(pool, store, anna):
    pune = store.add_fact(anna.id, "city", "Pune", source_kind="user_stated", observed_at=day(1))
    berlin = store.add_fact(anna.id, "city", "Berlin", source_kind="user_stated", observed_at=day(5))
    assert [f.value for f in store.facts_for(anna.id)] == ["Berlin"]
    hist = store.facts_for(anna.id, history=True)
    assert [f.value for f in hist] == ["Berlin", "Pune"]
    old = hist[1]
    assert (old.status, old.valid_to) == ("superseded", day(5))
    with pool.connection() as c:
        assert c.execute("SELECT superseded_by FROM fact WHERE id = %s", (pune.id,)).fetchone()[0] == berlin.id


def test_email_fact_does_not_override_user_stated(store, anna):
    store.add_fact(anna.id, "employer", "Celonis", source_kind="user_stated", observed_at=day(1))
    acme = store.add_fact(anna.id, "employer", "Acme", source_kind="email", ref="msg-1", observed_at=day(9))
    assert acme.status == "pending" and acme.valid_to is None
    assert [f.value for f in store.facts_for(anna.id)] == ["Celonis"]
    assert [f.value for f in store.pending_for(anna.id)] == ["Acme"]


def test_email_fact_older_than_active_goes_to_history(pool, store, anna):
    cel = store.add_fact(anna.id, "employer", "Celonis", source_kind="user_stated", observed_at=day(5))
    old = store.add_fact(anna.id, "employer", "Acme", source_kind="email", ref="msg-1", observed_at=day(1))
    assert (old.status, old.valid_from, old.valid_to) == ("superseded", day(1), day(5))
    assert store.pending_for(anna.id) == []
    assert [f.value for f in store.facts_for(anna.id)] == ["Celonis"]
    with pool.connection() as c:
        assert c.execute("SELECT superseded_by FROM fact WHERE id = %s", (old.id,)).fetchone()[0] == cel.id


def test_higher_trust_wins_regardless_of_age(store, anna):
    store.add_fact(anna.id, "city", "Hamburg", source_kind="email", ref="m1", observed_at=day(9))
    user = store.add_fact(anna.id, "city", "Pune", source_kind="user_stated", observed_at=day(1))
    assert user.status == "active"
    assert [f.value for f in store.facts_for(anna.id)] == ["Pune"]
    hist = {f.value: f for f in store.facts_for(anna.id, history=True)}
    assert hist["Hamburg"].status == "superseded" and hist["Hamburg"].valid_to == day(9)  # never before valid_from
    # a newer email after the user's statement stays pending
    later = store.add_fact(anna.id, "city", "Munich", source_kind="email", ref="m2", observed_at=day(20))
    assert later.status == "pending"
    assert [f.value for f in store.facts_for(anna.id)] == ["Pune"]


def test_equal_trust_newer_wins_older_arriving_later_is_history(store, anna):
    store.add_fact(anna.id, "city", "Pune", source_kind="email", ref="m1", observed_at=day(1))
    store.add_fact(anna.id, "city", "Berlin", source_kind="whatsapp", ref="w1", observed_at=day(5))
    assert [f.value for f in store.facts_for(anna.id)] == ["Berlin"]
    late = store.add_fact(anna.id, "city", "Rome", source_kind="email", ref="m0", observed_at=day(3))
    assert (late.status, late.valid_to) == ("superseded", day(5))
    assert [f.value for f in store.facts_for(anna.id)] == ["Berlin"]


def test_older_equal_trust_arriving_later_is_history(pool, store, anna):
    cur = store.add_fact(anna.id, "office", "Tower A", source_kind="email", ref="m2", observed_at=day(4))
    old = store.add_fact(anna.id, "office", "Tower B", source_kind="email", ref="m1", observed_at=day(2))
    assert (old.status, old.valid_from, old.valid_to) == ("superseded", day(2), day(4))
    assert store.facts_for(anna.id) == [cur]
    with pool.connection() as c:
        assert c.execute("SELECT superseded_by FROM fact WHERE id = %s", (old.id,)).fetchone()[0] == cur.id


def test_multi_valued_predicates_accumulate(store, anna):
    store.add_fact(anna.id, "friend_of", "Bob", source_kind="user_stated", observed_at=day(1))
    store.add_fact(anna.id, "friend_of", "Carl", source_kind="email", ref="m1", observed_at=day(0))
    assert sorted(f.value for f in store.facts_for(anna.id)) == ["Bob", "Carl"]
    assert all(f.status == "active" for f in store.facts_for(anna.id))


def test_repeat_observation_updates_confirmation_not_duplicates(pool, store, anna):
    a = store.add_fact(anna.id, "city", "Pune", source_kind="email", ref="m1", observed_at=day(1))
    (before,) = _links(pool, a.id)
    b = store.add_fact(anna.id, "city", " pune ", source_kind="email", ref="m1", observed_at=day(1))
    assert b.id == a.id
    (after,) = _links(pool, a.id)
    assert after[2] > before[2] and after[1] == before[1]
    c = store.add_fact(anna.id, "city", "PUNE", source_kind="contacts", ref="c1")
    assert c.id == a.id and len(_links(pool, a.id)) == 2
    assert c.source_kind == "contacts"  # highest-trust source now backs it
    assert len(store.facts_for(anna.id, history=True)) == 1
    store.add_fact(anna.id, "friend_of", "Bob", source_kind="user_stated")
    store.add_fact(anna.id, "friend_of", "bob", source_kind="user_stated")
    assert [f.value for f in store.facts_for(anna.id) if f.predicate == "friend_of"] == ["Bob"]


def test_unknown_predicate_rejected(store, anna):
    with pytest.raises(ValueError):
        store.add_fact(anna.id, "shoe_size", "42", source_kind="user_stated")
    with pytest.raises(ValueError):
        store.add_fact(anna.id, "city", "Pune", source_kind="rumour")
    for v in ("", "   \t", "x" * 201):
        with pytest.raises(ValueError):
            store.add_fact(anna.id, "city", v, source_kind="user_stated")


def test_excerpt_truncated_to_200(store, anna):
    f = store.add_fact(anna.id, "city", "Pune", source_kind="user_stated", excerpt="y" * 500)
    assert f.excerpt == "y" * 200


def test_correct_fact_creates_user_stated_superseding_fact(store, anna):
    old = store.add_fact(anna.id, "employer", "Acme", source_kind="email", ref="m1", observed_at=day(1))
    new = store.correct_fact(old.id, "Celonis")
    assert (new.value, new.status, new.source_kind, new.excerpt) == ("Celonis", "active", "user_stated",
                                                                    "corrected by user")
    assert [f.value for f in store.facts_for(anna.id)] == ["Celonis"]
    hist = {f.value: f.status for f in store.facts_for(anna.id, history=True)}
    assert hist == {"Celonis": "active", "Acme": "superseded"}
    # correcting a pending fact's predicate also works
    p = store.add_fact(anna.id, "employer", "Initech", source_kind="email", ref="m2", observed_at=day(400))
    assert p.status == "pending"
    assert store.correct_fact(p.id, "Globex").status == "active"
    assert [f.value for f in store.facts_for(anna.id)] == ["Globex"]


def test_retract_fact(store, anna, uids, pool):
    f = store.add_fact(anna.id, "city", "Pune", source_kind="user_stated")
    assert GraphStore(pool, uids[1]).retract_fact(f.id) is False
    assert store.retract_fact(f.id) is True
    assert store.facts_for(anna.id) == []
    (r,) = store.facts_for(anna.id, history=True)
    assert r.status == "retracted" and r.valid_to is not None
    assert store.retract_fact(999999) is False


def test_delete_fact_removes_orphan_source(pool, store, anna):
    a = store.add_fact(anna.id, "city", "Pune", source_kind="email", ref="m1")
    b = store.add_fact(anna.id, "friend_of", "Bob", source_kind="email", ref="m1")  # shares source m1
    c = store.add_fact(anna.id, "friend_of", "Carl", source_kind="email", ref="m2")
    assert store.delete_fact(a.id) is True
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM source WHERE ref = 'm1'").fetchone()[0] == 1
    assert store.delete_fact(b.id) is True and store.delete_fact(c.id) is True
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM source").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM fact").fetchone()[0] == 0
    assert store.delete_fact(a.id) is False


def test_facts_isolated_between_users(pool, uids, store, anna):
    f = store.add_fact(anna.id, "city", "Pune", source_kind="user_stated")
    other = GraphStore(pool, uids[1])
    assert other.facts_for(anna.id, history=True) == [] and other.pending_for(anna.id) == []
    assert other.delete_fact(f.id) is False
    with pytest.raises(ValueError):
        other.correct_fact(f.id, "Berlin")
    assert store.facts_for(anna.id) == [f]


def test_cross_user_subject_rejected(pool, uids, anna):
    with pytest.raises(ValueError, match="unknown entity"):
        GraphStore(pool, uids[1]).add_fact(anna.id, "city", "Pune", source_kind="user_stated")
    with pool.connection() as c:
        assert c.execute("SELECT count(*) FROM fact").fetchone()[0] == 0


def test_version_bumps_on_each_write(store, anna):
    v = store.version
    f = store.add_fact(anna.id, "city", "Pune", source_kind="user_stated")
    assert store.version == v + 1
    store.add_fact(anna.id, "city", "pune", source_kind="user_stated")
    assert store.version == v + 2
    g = store.correct_fact(f.id, "Berlin")
    assert store.version == v + 3
    store.retract_fact(g.id)
    assert store.version == v + 4
    store.delete_fact(g.id)
    assert store.version == v + 5
    store.delete_fact(g.id)  # no-op
    store.facts_for(anna.id)
    assert store.version == v + 5


def test_two_active_single_valued_writes_race_to_one(store, anna):
    barrier = threading.Barrier(8)
    errors = []

    def go(i):
        try:
            barrier.wait()
            store.add_fact(anna.id, "city", f"City {i}", source_kind="user_stated", observed_at=day(i % 3))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []
    assert len(store.facts_for(anna.id)) == 1


def test_pending_promoted_when_higher_trust_confirms_it(pool, store, anna):
    cel = store.add_fact(anna.id, "employer", "Celonis", source_kind="contacts", ref="c1", observed_at=day(1))
    acme = store.add_fact(anna.id, "employer", "Acme", source_kind="email", ref="m1", observed_at=day(5))
    assert acme.status == "pending"
    again = store.add_fact(anna.id, "employer", "acme", source_kind="user_stated", observed_at=day(6))
    assert (again.id, again.status, again.valid_from, again.source_kind) == (acme.id, "active", day(5), "user_stated")
    assert store.facts_for(anna.id) == [again] and store.pending_for(anna.id) == []
    hist = {f.value: f for f in store.facts_for(anna.id, history=True)}
    assert (hist["Celonis"].status, hist["Celonis"].valid_to) == ("superseded", day(5))
    with pool.connection() as c:
        assert c.execute("SELECT superseded_by FROM fact WHERE id = %s", (cel.id,)).fetchone()[0] == acme.id


def test_pending_promoted_when_no_active_fact(store, anna):
    cel = store.add_fact(anna.id, "employer", "Celonis", source_kind="user_stated", observed_at=day(1))
    acme = store.add_fact(anna.id, "employer", "Acme", source_kind="email", ref="m1", observed_at=day(5))
    store.retract_fact(cel.id)
    again = store.add_fact(anna.id, "employer", "Acme", source_kind="email", ref="m2", observed_at=day(7))
    assert (again.id, again.status) == (acme.id, "active")


def test_lower_trust_confirmation_keeps_pending(store, anna):
    store.add_fact(anna.id, "employer", "Celonis", source_kind="user_stated", observed_at=day(1))
    acme = store.add_fact(anna.id, "employer", "Acme", source_kind="email", ref="m1", observed_at=day(5))
    again = store.add_fact(anna.id, "employer", "Acme", source_kind="contacts", ref="c1", observed_at=day(6))
    assert (again.id, again.status) == (acme.id, "pending")
    assert [f.value for f in store.facts_for(anna.id)] == ["Celonis"]


def test_correct_fact_onto_pending_value(store, anna):
    cel = store.add_fact(anna.id, "employer", "Celonis", source_kind="user_stated", observed_at=day(5))
    acme = store.add_fact(anna.id, "employer", "Acme", source_kind="email", ref="m1", observed_at=day(9))
    new = store.correct_fact(cel.id, "Acme")
    assert (new.id, new.status) == (acme.id, "active")
    assert store.facts_for(anna.id) == [new] and store.pending_for(anna.id) == []
    # user_stated active with a later valid_from than the pending value still yields to the correction
    store.add_fact(anna.id, "employer", "Globex", source_kind="email", ref="m2", observed_at=day(30))
    store.add_fact(anna.id, "employer", "Initech", source_kind="user_stated", observed_at=day(40))
    globex = store.pending_for(anna.id)[0]
    fixed = store.correct_fact(globex.id, "Globex")
    assert fixed.status == "active" and [f.value for f in store.facts_for(anna.id)] == ["Globex"]
    for f in store.facts_for(anna.id, history=True):
        assert f.valid_to is None or f.valid_to >= f.valid_from


def test_correct_fact_when_active_valid_from_in_future(store, anna):
    future = datetime.now(timezone.utc) + timedelta(days=30)
    old = store.add_fact(anna.id, "city", "Pune", source_kind="user_stated", observed_at=future)
    new = store.correct_fact(old.id, "Berlin")
    assert new.status == "active" and new.valid_from > future
    assert store.facts_for(anna.id) == [new]
    (pune,) = [f for f in store.facts_for(anna.id, history=True) if f.value == "Pune"]
    assert pune.status == "superseded" and pune.valid_to >= pune.valid_from


def test_correct_fact_on_multi_fact(store, anna):
    bob = store.add_fact(anna.id, "friend_of", "Bob", source_kind="email", ref="m1")
    store.add_fact(anna.id, "friend_of", "Carl", source_kind="email", ref="m2")
    new = store.correct_fact(bob.id, "Rob")
    assert new.status == "active"
    assert sorted(f.value for f in store.facts_for(anna.id)) == ["Carl", "Rob"]


def test_correct_fact_on_superseded_facts_predicate(store, anna):
    old = store.add_fact(anna.id, "city", "Pune", source_kind="user_stated", observed_at=day(1))
    store.add_fact(anna.id, "city", "Berlin", source_kind="user_stated", observed_at=day(5))
    new = store.correct_fact(old.id, "Rome")
    assert new.status == "active" and [f.value for f in store.facts_for(anna.id)] == ["Rome"]
    assert {f.value: f.status for f in store.facts_for(anna.id, history=True)} == {
        "Rome": "active", "Berlin": "superseded", "Pune": "superseded"}
