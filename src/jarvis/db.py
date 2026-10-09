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
CREATE TABLE IF NOT EXISTS notes (
  id SERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body)) STORED
);
CREATE INDEX IF NOT EXISTS notes_tsv_idx ON notes USING GIN (tsv);
CREATE TABLE IF NOT EXISTS mail_seen (
  message_id TEXT PRIMARY KEY,
  outcome TEXT NOT NULL,
  event_id TEXT,
  at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS alerts_sent (
  key TEXT PRIMARY KEY,
  at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS auto_events (
  event_id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS proactive_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def make_pool(url: str) -> ConnectionPool:
    return ConnectionPool(url, min_size=1, max_size=5, open=True,
                          check=ConnectionPool.check_connection, max_idle=240)  # drop stale conns after a DB restart/redeploy


def init_schema(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute(SCHEMA)


def like_pattern(q: str) -> str:
    """Contains-match pattern for ILIKE with %, _ and backslash taken literally."""
    return "%" + re.sub(r"([\\%_])", r"\\\1", q) + "%"
