import json

from psycopg.types.json import Jsonb


def _dumps(o) -> str:
    return json.dumps(o, default=str)


def _j(v):
    return None if v is None else Jsonb(v, dumps=_dumps)


class Audit:
    def __init__(self, pool):
        self.pool = pool

    def record(self, kind, name, *, args=None, result=None, confirmation=None, latency_ms=None,
               model=None, tokens_in=None, tokens_out=None, cost_usd=None) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO audit_log (kind, name, args, result, confirmation, latency_ms, model,"
                " tokens_in, tokens_out, cost_usd) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (kind, name, _j(args), _j(result), confirmation, latency_ms, model,
                 tokens_in, tokens_out, cost_usd),
            )

    def purge(self, days: int = 90) -> int:
        with self.pool.connection() as conn:
            cur = conn.execute("DELETE FROM audit_log WHERE ts < now() - make_interval(days => %s)", (days,))
            return cur.rowcount
