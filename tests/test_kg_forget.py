import pytest

from jarvis.kg.store import GraphStore, Suppressed

SECRET = b"s" * 32


@pytest.fixture
def store(pool, uids):
    return GraphStore(pool, uids[0], secret=SECRET)


@pytest.fixture
def anna(store):
    a = store.create_entity("Anna Müller", aliases=("Annie",))
    store.add_key(a.id, "email", "Anna@X.com")
    store.add_key(a.id, "phone", "+49 170 1234567")
    return a


def _count(pool, sql, *args):
    with pool.connection() as c:
        return c.execute(sql, args).fetchone()[0]


def test_forget_removes_entity_keys_facts_and_orphan_sources(pool, store, anna):
    store.add_fact(anna.id, "city", "Pune", source_kind="user_stated", ref="r1", excerpt="she lives in Pune")
    r = store.forget_entity(anna.id)
    assert (r["entity"], r["facts"]) == ("Anna Müller", 1)
    assert store.get_entity(anna.id) is None
    assert _count(pool, "SELECT count(*) FROM entity_key WHERE entity_id = %s", anna.id) == 0
    assert _count(pool, "SELECT count(*) FROM fact WHERE subject_id = %s", anna.id) == 0
    assert _count(pool, "SELECT count(*) FROM source") == 0


def test_forget_deletes_facts_pointing_at_entity(pool, store, anna, uids):
    maria = store.create_entity("Maria")
    with pool.connection() as c:
        c.execute("INSERT INTO fact (user_id, subject_id, predicate, object_entity_id) VALUES (%s, %s, 'friend_of', %s)",
                  (uids[0], maria.id, anna.id))
    assert store.forget_entity(anna.id)["facts"] == 1
    assert _count(pool, "SELECT count(*) FROM fact WHERE object_entity_id IS NOT NULL") == 0
    assert store.get_entity(maria.id) is not None


def test_forget_deletes_mentions_in_other_facts_by_default_and_can_skip(pool, store, anna):
    maria = store.create_entity("Maria")
    store.add_fact(maria.id, "note", "her boss is Anna", source_kind="user_stated")
    trip = store.add_fact(maria.id, "note", "hiked Annapurna", source_kind="user_stated")
    store.add_fact(maria.id, "note", "cc anna@x.com on invites", source_kind="user_stated")
    r = store.forget_entity(anna.id)
    assert r["mentions_deleted"] == 2
    assert {f.id for f in store.facts_for(maria.id)} == {trip.id}

    bo = store.create_entity("Bo Li")
    store.add_fact(maria.id, "note", "her boss is Bo Li", source_kind="user_stated")
    short = store.add_fact(maria.id, "note", "bo is a word", source_kind="user_stated")
    assert store.forget_entity(bo.id)["mentions_deleted"] == 1  # full name matches, the short token alone does not
    assert {f.id for f in store.facts_for(maria.id)} == {trip.id, short.id}

    carl = store.create_entity("Carl")
    kept = store.add_fact(maria.id, "note", "dates Carl", source_kind="user_stated")
    assert store.forget_entity(carl.id, include_mentions=False)["mentions_deleted"] == 0
    assert kept.id in {f.id for f in store.facts_for(maria.id)}


def test_forget_redacts_excerpt_but_keeps_other_entities_fact(store, anna):
    maria = store.create_entity("Maria")
    f = store.add_fact(maria.id, "city", "Pune", source_kind="email", ref="m1",
                       excerpt="Anna said Maria lives in Pune")
    r = store.forget_entity(anna.id)
    assert r["redacted_excerpts"] == 1
    [kept] = store.facts_for(maria.id)
    assert (kept.id, kept.value, kept.excerpt) == (f.id, "Pune", "")


def test_forget_leaves_notes_audit_and_history_alone(pool, store, anna, uids):
    with pool.connection() as c:
        c.execute("INSERT INTO notes (user_id, title, body) VALUES (%s, 'Anna Müller', 'call Anna')", (uids[0],))
        c.execute("INSERT INTO audit_log (user_id, kind, name, args) VALUES (%s, 'tool', 'remember', %s)",
                  (uids[0], '{"who": "Anna Müller"}'))
    store.forget_entity(anna.id)
    assert _count(pool, "SELECT count(*) FROM notes WHERE body = 'call Anna'") == 1
    assert _count(pool, "SELECT count(*) FROM audit_log WHERE args->>'who' = 'Anna Müller'") == 1


def test_suppression_is_hashed_not_plain(pool, store, anna):
    assert store.forget_entity(anna.id)["suppressed"] == 4  # email, phone, full name, alias
    with pool.connection() as c:
        rows = c.execute("SELECT * FROM suppression").fetchall()
    dump = repr(rows).lower()
    for plain in ("anna", "müller", "annie", "x.com", "1234567"):
        assert plain not in dump
    assert sorted((r[3], r[1]) for r in rows) == [("hard", "email"), ("hard", "phone"), ("name", "alias"),
                                                  ("name", "name")]


def test_extracted_creation_blocked_for_forgotten_name_and_key(store, anna):
    store.forget_entity(anna.id)
    with pytest.raises(Suppressed):
        store.create_entity("anna  MÜLLER", origin="extracted")
    with pytest.raises(Suppressed):
        store.create_entity("Bob", aliases=("Annie",), origin="extracted")
    other = store.create_entity("Someone", origin="extracted")
    with pytest.raises(Suppressed):
        store.add_key(other.id, "email", "anna@x.COM", origin="extracted")
    with pytest.raises(Suppressed):
        store.add_key(other.id, "phone", "+49-170-1234567", origin="extracted")
    with pytest.raises(Suppressed):
        store.add_key(other.id, "alias", "Annie", origin="extracted")
    assert store.is_suppressed("phone", "+49 (170) 1234567")
    store.add_key(other.id, "email", "anna@x.com")  # the user's own word is never blocked
    with pytest.raises(ValueError):
        store.create_entity("X", origin="sync")


def test_user_statement_recreates_and_lifts_name_suppression(store, anna):
    store.forget_entity(anna.id)
    again = store.create_entity("Anna Müller")
    assert store.get_entity(again.id).display_name == "Anna Müller"
    assert not store.is_suppressed("name", "Anna Müller")
    assert store.is_suppressed("alias", "Annie")  # only the names the user restated are lifted
    assert store.is_suppressed("email", "anna@x.com")  # hard keys stay
    store.create_entity("Anna Müller", origin="extracted")


def test_different_hard_key_not_blocked_by_name_suppression(store, anna):
    store.forget_entity(anna.id)
    with pytest.raises(Suppressed):
        store.create_entity("Anna Müller", origin="extracted")
    other = store.create_entity("Anna Müller", origin="user")
    store.add_key(other.id, "email", "other@y.com", origin="extracted")
    with pytest.raises(Suppressed):
        store.add_key(other.id, "email", "anna@x.com", origin="extracted")


def test_suppression_is_per_user_key(pool, store, anna, uids):
    b = GraphStore(pool, uids[1], secret=SECRET)
    bob_anna = b.create_entity("Anna")
    b.add_key(bob_anna.id, "email", "anna@x.com")
    store.forget_entity(anna.id)
    assert not b.is_suppressed("email", "anna@x.com")
    b.add_key(b.create_entity("Z").id, "email", "anna@x.com", origin="extracted")
    b.forget_entity(bob_anna.id)
    with pool.connection() as c:
        hs = c.execute("SELECT user_id, key_hmac FROM suppression WHERE key_type = 'email'").fetchall()
    assert len(hs) == 2 and hs[0][1] != hs[1][1]


def test_preview_forget_writes_nothing(pool, store, anna):
    maria = store.create_entity("Maria")
    store.add_fact(maria.id, "city", "Pune", source_kind="email", ref="m1", excerpt="Anna said Maria lives in Pune")
    store.add_fact(maria.id, "note", "her boss is Anna Müller", source_kind="user_stated")
    store.add_fact(anna.id, "city", "Berlin", source_kind="user_stated")
    v = store.version
    p = store.preview_forget(anna.id)
    assert store.version == v
    assert store.get_entity(anna.id) is not None
    assert _count(pool, "SELECT count(*) FROM suppression") == 0
    assert _count(pool, "SELECT count(*) FROM source WHERE excerpt <> ''") == 1
    assert len(store.facts_for(maria.id)) == 2
    assert p == store.forget_entity(anna.id)
    assert p == {"entity": "Anna Müller", "facts": 1, "redacted_excerpts": 1, "mentions_deleted": 1, "suppressed": 4,
                 "merged_entities": 0}


def test_list_and_lift_suppressions(store, anna):
    store.forget_entity(anna.id)
    rows = store.list_suppressions()
    assert {(r["kind"], r["key_type"]) for r in rows} == {("hard", "email"), ("hard", "phone"), ("name", "name"),
                                                          ("name", "alias")}
    assert all(set(r) == {"kind", "key_type", "key_hmac", "created_at"} for r in rows)
    assert "anna" not in repr(rows).lower()
    email = next(r for r in rows if r["key_type"] == "email")
    assert store.lift_suppression("email", email["key_hmac"]) is True
    assert not store.is_suppressed("email", "anna@x.com")
    assert store.lift_suppression("email", email["key_hmac"]) is False
    assert len(store.list_suppressions()) == 3


def test_forget_self_entity_refused(store):
    me = store.self_entity()
    with pytest.raises(ValueError):
        store.forget_entity(me.id)
    assert store.get_entity(me.id) is not None


def test_forget_other_users_entity_refused(pool, store, anna, uids):
    b = GraphStore(pool, uids[1], secret=SECRET)
    with pytest.raises(ValueError, match="unknown entity"):
        b.forget_entity(anna.id)
    with pytest.raises(ValueError, match="unknown entity"):
        b.preview_forget(anna.id)
    assert store.get_entity(anna.id) is not None


def test_empty_secret_refused(pool, uids):
    s = GraphStore(pool, uids[0])
    a = s.create_entity("Anna")
    with pytest.raises(ValueError):
        s.forget_entity(a.id)
    assert s.get_entity(a.id) is not None
    assert _count(pool, "SELECT count(*) FROM suppression") == 0


def test_new_excerpt_with_suppressed_name_is_redacted(store, anna):
    store.forget_entity(anna.id)
    maria = store.create_entity("Maria")
    f1 = store.add_fact(maria.id, "city", "Pune", source_kind="email", ref="m1",
                        excerpt="Anna Müller said Maria lives in Pune")
    f2 = store.add_fact(maria.id, "employer", "Acme", source_kind="email", ref="m2",
                        excerpt="Sent from anna@x.com: Maria joined Acme")
    f3 = store.add_fact(maria.id, "birthday", "May 1", source_kind="email", ref="m3",
                        excerpt="ring +49 170 1234567 for Maria's party")
    f4 = store.add_fact(maria.id, "home", "Baner", source_kind="email", ref="m4", excerpt="Maria lives in Baner")
    assert [f.excerpt for f in (f1, f2, f3, f4)] == ["", "", "", "Maria lives in Baner"]


def test_forget_bumps_version(store, anna):
    v = store.version
    store.forget_entity(anna.id)
    assert store.version == v + 1


def _merge(pool, child, parent):
    with pool.connection() as c:
        c.execute("UPDATE entity SET merged_into = %s WHERE id = %s", (parent, child))


def test_forget_also_forgets_entities_merged_into_it(pool, store, anna):
    dup = store.create_entity("A. Müller")
    store.add_key(dup.id, "email", "am@work.com")
    store.add_fact(dup.id, "employer", "Acme", source_kind="user_stated", excerpt="A. Müller joined Acme")
    maria = store.create_entity("Maria")
    store.add_fact(maria.id, "note", "met A. Müller at work", source_kind="user_stated")
    _merge(pool, dup.id, anna.id)
    r = store.forget_entity(anna.id)
    assert (r["merged_entities"], r["facts"], r["mentions_deleted"]) == (1, 1, 1)
    assert store.get_entity(dup.id) is None and store.get_entity(anna.id) is None
    assert [e.id for e in store.list_entities()] == [maria.id]
    assert store.find_by_name("A. Müller") == [] and store.find_by_name("Anna Müller") == []
    for t, v in (("email", "am@work.com"), ("email", "anna@x.com"), ("name", "A. Müller"), ("name", "Anna Müller")):
        assert store.is_suppressed(t, v), (t, v)
    assert _count(pool, "SELECT count(*) FROM source") == 0


def test_forget_follows_merge_chain_transitively(pool, store, anna):
    b, c = store.create_entity("Ann M"), store.create_entity("Annie Mueller")
    store.add_key(c.id, "phone", "+1 555 0100")
    _merge(pool, b.id, anna.id)
    _merge(pool, c.id, b.id)
    p = store.preview_forget(anna.id)
    assert p["merged_entities"] == 2
    assert store.get_entity(c.id) is not None
    assert store.forget_entity(anna.id) == p
    assert store.get_entity(b.id) is None and store.get_entity(c.id) is None
    assert store.is_suppressed("phone", "+15550100")


def test_forget_merge_cycle_does_not_hang(pool, store, anna):
    b = store.create_entity("Bea")
    _merge(pool, b.id, anna.id)
    _merge(pool, anna.id, b.id)
    assert store.forget_entity(anna.id)["merged_entities"] == 1
    assert store.get_entity(b.id) is None


def test_suppressed_message_never_carries_the_value(store, anna):
    store.forget_entity(anna.id)
    with pytest.raises(Suppressed) as e1:
        store.create_entity("Anna Müller", origin="extracted")
    other = store.create_entity("Someone")
    with pytest.raises(Suppressed) as e2:
        store.add_key(other.id, "email", "anna@x.com", origin="extracted")
    for e in (e1, e2):
        msg = (str(e.value) + repr(e.value)).lower()
        assert "anna" not in msg and "müller" not in msg and "x.com" not in msg
