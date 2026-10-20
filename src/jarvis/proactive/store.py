class ProactiveStore:
    """Durable state for the scheduled jobs: what mail was handled, which alerts were sent, which events Jarvis auto-created."""

    def __init__(self, pool, user_id: int):
        self.pool, self.user_id = pool, user_id

    def mail_seen(self, message_id: str) -> bool:
        with self.pool.connection() as c:
            return c.execute("SELECT 1 FROM mail_seen WHERE user_id = %s AND message_id = %s",
                             (self.user_id, message_id)).fetchone() is not None

    def record_mail(self, message_id: str, outcome: str, event_id: str | None = None) -> None:
        with self.pool.connection() as c:
            c.execute("INSERT INTO mail_seen (user_id, message_id, outcome, event_id) VALUES (%s, %s, %s, %s)"
                      " ON CONFLICT (user_id, message_id) DO NOTHING", (self.user_id, message_id, outcome, event_id))

    def claim_alert(self, key: str) -> bool:
        """True only for the first caller; a repeated sweep or a restart never alerts twice."""
        with self.pool.connection() as c:
            return c.execute("INSERT INTO alerts_sent (user_id, key) VALUES (%s, %s) ON CONFLICT (user_id, key) DO NOTHING",
                             (self.user_id, key)).rowcount == 1

    def add_auto_event(self, event_id: str, message_id: str) -> None:
        with self.pool.connection() as c:
            c.execute("INSERT INTO auto_events (user_id, event_id, message_id) VALUES (%s, %s, %s)"
                      " ON CONFLICT (user_id, event_id) DO NOTHING", (self.user_id, event_id, message_id))

    def is_auto_event(self, event_id: str) -> bool:
        with self.pool.connection() as c:
            return c.execute("SELECT 1 FROM auto_events WHERE user_id = %s AND event_id = %s",
                             (self.user_id, event_id)).fetchone() is not None

    def remove_auto_event(self, event_id: str) -> bool:
        with self.pool.connection() as c:
            return c.execute("DELETE FROM auto_events WHERE user_id = %s AND event_id = %s",
                             (self.user_id, event_id)).rowcount > 0

    def auto_events_last_day(self) -> int:
        with self.pool.connection() as c:
            return c.execute("SELECT count(*) FROM auto_events WHERE user_id = %s AND at > now() - interval '24 hours'",
                             (self.user_id,)).fetchone()[0]

    def get_state(self, key: str) -> str | None:
        with self.pool.connection() as c:
            row = c.execute("SELECT value FROM proactive_state WHERE user_id = %s AND key = %s",
                            (self.user_id, key)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.pool.connection() as c:
            c.execute("INSERT INTO proactive_state (user_id, key, value) VALUES (%s, %s, %s)"
                      " ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value", (self.user_id, key, value))


def purge_all(pool, days: int = 90) -> None:
    """Retention for every user's job history at once; proactive_state is kept."""
    with pool.connection() as c:
        for table in ("mail_seen", "alerts_sent", "auto_events"):
            c.execute(f"DELETE FROM {table} WHERE at < now() - make_interval(days => %s)", (days,))
