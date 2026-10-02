import re

from psycopg_pool import ConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  args JSONB,
  result JSONB,
  confirmation TEXT,
  latency_ms INTEGER,
  model TEXT,
  tokens_in INTEGER,
  tokens_out INTEGER,
  cost_usd NUMERIC
);
CREATE TABLE IF NOT EXISTS oauth_tokens (
  provider TEXT PRIMARY KEY,
  blob BYTEA NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
  id SERIAL PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  fcm_token TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS memories (
  id SERIAL PRIMARY KEY,
  text TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def make_pool(url: str) -> ConnectionPool:
    return ConnectionPool(url, min_size=1, max_size=5, open=True)


def init_schema(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute(SCHEMA)


def like_pattern(q: str) -> str:
    """Contains-match pattern for ILIKE with %, _ and backslash taken literally."""
    return "%" + re.sub(r"([\\%_])", r"\\\1", q) + "%"
