from datetime import date

from jarvis.audit import Audit


def test_record_roundtrip(pool):
    a = Audit(pool)
    a.record("tool", "create_event", args={"d": date(2026, 1, 1)}, result={"ok": True},
             confirmation="approved", latency_ms=12)
    with pool.connection() as c:
        row = c.execute("SELECT kind, name, args, confirmation, latency_ms FROM audit_log").fetchone()
    assert row == ("tool", "create_event", {"d": "2026-01-01"}, "approved", 12)


def test_purge_removes_only_old_rows(pool):
    a = Audit(pool)
    a.record("tool", "new")
    a.record("tool", "old")
    with pool.connection() as c:
        c.execute("UPDATE audit_log SET ts = now() - interval '91 days' WHERE name = 'old'")
    assert a.purge(90) == 1
    with pool.connection() as c:
        assert c.execute("SELECT name FROM audit_log").fetchall() == [("new",)]
