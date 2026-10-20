from jarvis.proactive.store import ProactiveStore, purge_all
from tests.fakes import FakeProactiveStore


def contract(s):
    assert not s.mail_seen("m1")
    s.record_mail("m1", "created", "e1")
    s.record_mail("m1", "skipped")  # first write wins
    assert s.mail_seen("m1")
    assert s.claim_alert("k") is True
    assert s.claim_alert("k") is False
    assert s.auto_events_last_day() == 0
    s.add_auto_event("e1", "m1")
    s.add_auto_event("e1", "m1")  # idempotent
    assert s.is_auto_event("e1") and s.auto_events_last_day() == 1
    assert s.remove_auto_event("e1") is True
    assert s.remove_auto_event("e1") is False
    assert not s.is_auto_event("e1") and s.auto_events_last_day() == 0
    assert s.get_state("cursor") is None
    s.set_state("cursor", "1")
    s.set_state("cursor", "2")
    assert s.get_state("cursor") == "2"


def test_fake_store_follows_the_contract():
    contract(FakeProactiveStore())


def test_pg_store_follows_the_contract(pool, uids):
    contract(ProactiveStore(pool, uids[0]))


def test_first_recorded_outcome_is_kept(pool, uids):
    s = ProactiveStore(pool, uids[0])
    s.record_mail("m1", "created", "e1")
    s.record_mail("m1", "skipped")
    with pool.connection() as c:
        assert c.execute("SELECT outcome, event_id FROM mail_seen WHERE message_id='m1'").fetchone() == ("created", "e1")


def test_cap_counts_only_the_last_24_hours(pool, uids):
    s = ProactiveStore(pool, uids[0])
    s.add_auto_event("old", "m0")
    s.add_auto_event("new", "m1")
    with pool.connection() as c:
        c.execute("UPDATE auto_events SET at = now() - interval '25 hours' WHERE event_id = 'old'")
    assert s.auto_events_last_day() == 1


def test_purge_drops_rows_older_than_the_window_and_keeps_state(pool, uids):
    s = ProactiveStore(pool, uids[0])
    s.record_mail("old", "created")
    s.record_mail("new", "created")
    s.claim_alert("old-key")
    s.add_auto_event("old-event", "old")
    s.set_state("mail_cursor", "123")
    with pool.connection() as c:
        c.execute("UPDATE mail_seen SET at = now() - interval '100 days' WHERE message_id = 'old'")
        c.execute("UPDATE alerts_sent SET at = now() - interval '100 days'")
        c.execute("UPDATE auto_events SET at = now() - interval '100 days'")
    purge_all(pool, 90)
    assert not s.mail_seen("old") and s.mail_seen("new")
    assert s.claim_alert("old-key") is True  # the old claim was purged
    assert not s.is_auto_event("old-event")
    assert s.get_state("mail_cursor") == "123"


def test_proactive_store_isolated_between_users(pool, uids):
    a, b = ProactiveStore(pool, uids[0]), ProactiveStore(pool, uids[1])
    assert a.claim_alert("k") is True and b.claim_alert("k") is True
    a.record_mail("m1", "created", "e1")
    assert not b.mail_seen("m1")
    b.record_mail("m1", "skipped")  # same Gmail id in another mailbox is its own row
    a.add_auto_event("e1", "m1")
    assert not b.is_auto_event("e1") and b.auto_events_last_day() == 0 and b.remove_auto_event("e1") is False
    a.set_state("cursor", "a")
    assert b.get_state("cursor") is None
    b.set_state("cursor", "b")
    assert (a.get_state("cursor"), b.get_state("cursor")) == ("a", "b")
