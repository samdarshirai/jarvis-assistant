"""Short-term notes and conversation summaries (jarvis.memory.store)."""
from datetime import datetime, timedelta, timezone

import pytest

from jarvis.kg.store import GraphStore
from jarvis.memory.store import DEFAULT_TTL, MAX_TTL, MIN_TTL, MemoryStore, clamp_expiry, same

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def mem(pool, uids, clock):
    return MemoryStore(pool, uids[0], clock)


def conv(pool, uid, thread, last_at=NOW, off_record=False):
    with pool.connection() as c:
        return c.execute("INSERT INTO conversation (user_id, channel, surface, thread_id, created_at, last_at, closed_at,"
                         " off_record) VALUES (%s, 'chat', 'chat', %s, %s, %s, %s, %s) RETURNING id",
                         (uid, thread, last_at, last_at, last_at, off_record)).fetchone()[0]


def test_same_matches_rewordings_not_different_notes():
    assert same("In Pune till Friday", "in pune until friday")
    assert same("Waiting for the plumber to call back", "waiting for plumber to call back")
    assert not same("In Pune till Friday", "In Delhi till Friday")
    assert not same("", "x")


def test_expiry_defaults_to_a_week_and_is_clamped():
    assert clamp_expiry(None, NOW) == NOW + DEFAULT_TTL
    assert clamp_expiry(NOW - timedelta(days=1), NOW) == NOW + MIN_TTL
    assert clamp_expiry(NOW + timedelta(days=400), NOW) == NOW + MAX_TTL
    with pytest.raises(ValueError):
        clamp_expiry(datetime(2026, 11, 5), NOW)


def test_add_saves_and_a_rewording_refreshes_instead_of_duplicating(pool, uids, mem):
    c1, c2 = conv(pool, uids[0], "a"), conv(pool, uids[0], "b")
    first, created = mem.add_item("In Pune till Friday", "context", NOW + timedelta(days=2), conversation_id=c1)
    assert created and first.status == "active" and first.seen_count == 1
    v = mem.version
    again, created = mem.add_item("in pune until friday", "context", NOW + timedelta(days=4), origin="digest",
                                  conversation_id=c2, durable=True)
    assert not created and again.id == first.id and mem.version > v
    assert again.expires_at == NOW + timedelta(days=4) and again.seen_count == 2 and again.durable
    same_conv, _ = mem.add_item("In Pune till Friday", conversation_id=c2)
    assert same_conv.seen_count == 2  # the same conversation does not count twice
    assert [i.id for i in mem.active()] == [first.id]


def test_bad_input_is_refused(mem):
    for bad in (dict(text="  "), dict(text="x", kind="mood"), dict(text="x", origin="email")):
        with pytest.raises(ValueError):
            mem.add_item(**bad)


def test_active_lists_soonest_first_and_hides_ended_items(mem, clock):
    later = mem.add_item("Guest staying", "context", NOW + timedelta(days=5))[0]
    soon = mem.add_item("Promised Anna the slides", "commitment", NOW + timedelta(days=1))[0]
    assert [i.id for i in mem.active()] == [soon.id, later.id]
    assert [i.id for i in mem.active(1)] == [soon.id]
    clock.t = NOW + timedelta(days=2)
    assert [i.id for i in mem.active()] == [later.id]  # ended by the clock before the expiry job runs
    assert mem.expire_due() == 1 and mem.get(soon.id).status == "expired"


def test_update_delete_and_latest_in_conversation(pool, uids, mem, clock):
    cid = conv(pool, uids[0], "t")
    item = mem.add_item("Waiting for the plumber", conversation_id=cid)[0]
    assert mem.latest_in(cid).id == item.id
    edited = mem.update(item.id, text="Waiting for the electrician", expires_at=NOW + timedelta(days=3))
    assert edited.text == "Waiting for the electrician" and edited.expires_at == NOW + timedelta(days=3)
    with pytest.raises(ValueError):
        mem.update(item.id)
    assert mem.delete(item.id) and mem.get(item.id) is None and not mem.delete(item.id)
    with pytest.raises(LookupError):
        mem.update(item.id, text="x")
    assert mem.latest_in(cid) is None


def test_promote_turns_an_item_into_a_lasting_fact(pool, uids, mem):
    kg = GraphStore(pool, uids[0])
    item = mem.add_item("Works from the Pune office on Fridays")[0]
    out = mem.promote(item.id, kg)
    facts = kg.facts_for(kg.self_entity().id)
    assert [f.value for f in facts] == ["Works from the Pune office on Fridays"] and out["fact_ids"] == [facts[0].id]
    assert mem.get(item.id).status == "promoted" and mem.active() == []
    with pytest.raises(LookupError):
        mem.promote(item.id, kg)


def test_merge_keeps_the_later_end_and_both_mentions(mem):
    a = mem.add_item("In Pune till Friday", expires_at=NOW + timedelta(days=1))[0]
    b = mem.add_item("Visiting Pune", expires_at=NOW + timedelta(days=4))[0]
    assert mem.merge(a.id, b.id) and not mem.merge(a.id, b.id)
    kept = mem.get(a.id)
    assert kept.expires_at == NOW + timedelta(days=4) and kept.seen_count == 2 and mem.get(b.id) is None


def test_recent_summaries_skip_the_current_old_and_off_record_conversations(pool, uids, mem):
    old = conv(pool, uids[0], "old", NOW - timedelta(days=3))
    off = conv(pool, uids[0], "off", NOW - timedelta(hours=2), off_record=True)
    flight = conv(pool, uids[0], "flight", NOW - timedelta(hours=5))
    here = conv(pool, uids[0], "here", NOW - timedelta(hours=1))
    with pool.connection() as c:
        for cid in (old, off, flight, here):
            mem.save_digest(c, cid, f"summary {cid}  with   spaces", ["  book it  ", "", 3], cid == flight)
    got = mem.recent_summaries(exclude_thread="here")
    assert [r["id"] for r in got] == [flight]
    assert got[0]["summary"] == f"summary {flight} with spaces" and got[0]["open_loops"] == ["book it"]
    assert got[0]["untrusted"] is True
    assert [r["id"] for r in mem.recent_summaries()] == [here, flight]
    assert mem.open_loops() == ["book it"]  # the untrusted summary's loops stay out of the brief, the rest dedupe
    assert mem.summaries_for([old, here]) == {old: f"summary {old} with spaces", here: f"summary {here} with spaces"}
    assert mem.conversation_for("off") == (off, True) and mem.conversation_for("nope") is None


def test_rolling_summary_only_moves_forward(pool, uids, mem):
    conv(pool, uids[0], "long")
    assert mem.rolling("long") == (None, 0) and mem.rolling("missing") == (None, 0)
    mem.set_rolling("long", "first  part", 20)
    mem.set_rolling("long", "stale", 10)
    assert mem.rolling("long") == ("first part", 20)
    conv(pool, uids[0], "secret", off_record=True)
    mem.set_rolling("secret", "kept?", 20)
    assert mem.rolling("secret") == (None, 0)


def test_users_never_see_each_others_memory(pool, uids, mem):
    other = MemoryStore(pool, uids[1])
    item = mem.add_item("In Pune till Friday")[0]
    assert other.active() == [] and other.get(item.id) is None and not other.delete(item.id)
    with pytest.raises(LookupError):
        other.update(item.id, text="x")
    cid = conv(pool, uids[0], "mine")
    with pool.connection() as c:
        mem.save_digest(c, cid, "private", [], False)
        other.save_digest(c, cid, "overwritten?", [], False)  # scoped by user: changes nothing
    assert other.recent_summaries() == [] and other.summaries_for([cid]) == {}
    assert mem.summaries_for([cid]) == {cid: "private"} and other.conversation_for("mine") is None
