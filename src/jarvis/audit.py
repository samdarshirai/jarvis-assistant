import json

from psycopg.types.json import Jsonb


def _dumps(o) -> str:
    return json.dumps(o, default=str)


def _j(v):
    return None if v is None else Jsonb(v, dumps=_dumps)


class Audit:
    def __init__(self, pool, user_id: int | None = None):
        self.pool, self.user_id = pool, user_id  # None only for system rows that belong to no user

    def for_user(self, user_id: int) -> "Audit":
        return Audit(self.pool, user_id)

    def record(self, kind, name, *, args=None, result=None, confirmation=None, latency_ms=None,
               model=None, tokens_in=None, tokens_out=None, cost_usd=None) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO audit_log (user_id, kind, name, args, result, confirmation, latency_ms, model,"
                " tokens_in, tokens_out, cost_usd) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (self.user_id, kind, name, _j(args), _j(result), confirmation, latency_ms, model,
                 tokens_in, tokens_out, cost_usd),
            )


def purge_all(pool, days: int = 90) -> int:
    """Retention for every user's audit rows at once."""
    with pool.connection() as conn:
        cur = conn.execute("DELETE FROM audit_log WHERE ts < now() - make_interval(days => %s)", (days,))
        return cur.rowcount
