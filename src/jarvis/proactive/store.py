class ProactiveStore:
    """Durable state for the scheduled jobs: what mail was handled, which alerts were sent, which events Jarvis auto-created."""

    def __init__(self, pool):
        self.pool = pool

    def mail_seen(self, message_id: str) -> bool:
        with self.pool.connection() as c:
            return c.execute("SELECT 1 FROM mail_seen WHERE message_id = %s", (message_id,)).fetchone() is not None

    def record_mail(self, message_id: str, outcome: str, event_id: str | None = None) -> None:
        with self.pool.connection() as c:
            c.execute("INSERT INTO mail_seen (message_id, outcome, event_id) VALUES (%s, %s, %s)"
                      " ON CONFLICT (message_id) DO NOTHING", (message_id, outcome, event_id))

    def claim_alert(self, key: str) -> bool:
        """True only for the first caller; a repeated sweep or a restart never alerts twice."""
        with self.pool.connection() as c:
            return c.execute("INSERT INTO alerts_sent (key) VALUES (%s) ON CONFLICT (key) DO NOTHING", (key,)).rowcount == 1

    def add_auto_event(self, event_id: str, message_id: str) -> None:
        with self.pool.connection() as c:
            c.execute("INSERT INTO auto_events (event_id, message_id) VALUES (%s, %s) ON CONFLICT (event_id) DO NOTHING",
                      (event_id, message_id))

    def is_auto_event(self, event_id: str) -> bool:
        with self.pool.connection() as c:
            return c.execute("SELECT 1 FROM auto_events WHERE event_id = %s", (event_id,)).fetchone() is not None

    def remove_auto_event(self, event_id: str) -> bool:
        with self.pool.connection() as c:
            return c.execute("DELETE FROM auto_events WHERE event_id = %s", (event_id,)).rowcount > 0

    def auto_events_last_day(self) -> int:
        with self.pool.connection() as c:
            return c.execute("SELECT count(*) FROM auto_events WHERE at > now() - interval '24 hours'").fetchone()[0]

    def get_state(self, key: str) -> str | None:
        with self.pool.connection() as c:
            row = c.execute("SELECT value FROM proactive_state WHERE key = %s", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.pool.connection() as c:
            c.execute("INSERT INTO proactive_state (key, value) VALUES (%s, %s)"
                      " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, value))

    def purge(self, days: int = 90) -> None:
        with self.pool.connection() as c:
            for table in ("mail_seen", "alerts_sent", "auto_events"):
                c.execute(f"DELETE FROM {table} WHERE at < now() - make_interval(days => %s)", (days,))
