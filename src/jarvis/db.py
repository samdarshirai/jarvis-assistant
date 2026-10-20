import re

from psycopg_pool import ConnectionPool

USER_REF = "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  google_sub TEXT UNIQUE,
  tz TEXT NOT NULL,
  is_owner BOOLEAN NOT NULL DEFAULT false,
  disabled BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS users_one_owner ON users (is_owner) WHERE is_owner;
CREATE TABLE IF NOT EXISTS user_secrets (
  {USER_REF},
  name TEXT NOT NULL,
  blob BYTEA NOT NULL,
  PRIMARY KEY (user_id, name)
);
CREATE TABLE IF NOT EXISTS auth_attempts (
  nonce TEXT PRIMARY KEY,
  challenge TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  device_token_enc BYTEA,  -- set by the Google callback, handed to the app once by the claim
  finish_code_hash TEXT  -- sha256 of the code the callback put in the deep link; the claim must show it
);
ALTER TABLE auth_attempts ADD COLUMN IF NOT EXISTS finish_code_hash TEXT;
CREATE INDEX IF NOT EXISTS auth_attempts_created_at_idx ON auth_attempts (created_at);  -- the purge on every start
CREATE TABLE IF NOT EXISTS audit_log (
  id BIGSERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,  -- NULL for system rows
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
  {USER_REF},
  provider TEXT NOT NULL,
  blob BYTEA NOT NULL,
  PRIMARY KEY (user_id, provider)
);
CREATE TABLE IF NOT EXISTS devices (
  id SERIAL PRIMARY KEY,
  {USER_REF},
  token_hash TEXT NOT NULL UNIQUE,
  fcm_token TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS memories (
  id SERIAL PRIMARY KEY,
  {USER_REF},
  text TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS notes (
  id SERIAL PRIMARY KEY,
  {USER_REF},
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body)) STORED
);
CREATE INDEX IF NOT EXISTS notes_tsv_idx ON notes USING GIN (tsv);
CREATE TABLE IF NOT EXISTS mail_seen (
  {USER_REF},
  message_id TEXT NOT NULL,
  outcome TEXT NOT NULL,
  event_id TEXT,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, message_id)
);
CREATE TABLE IF NOT EXISTS alerts_sent (
  {USER_REF},
  key TEXT NOT NULL,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS auto_events (
  {USER_REF},
  event_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, event_id)
);
CREATE TABLE IF NOT EXISTS proactive_state (
  {USER_REF},
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);
"""
# Every per-user table, with the column its pre-users primary key was on (None: an id PK that stays).
# audit_log keeps a nullable user_id: system rows (startup, purge) belong to nobody.
DATA_TABLES = {"audit_log": None, "oauth_tokens": "provider", "devices": None, "memories": None, "notes": None,
               "mail_seen": "message_id", "alerts_sent": "key", "auto_events": "event_id", "proactive_state": "key"}


def make_pool(url: str) -> ConnectionPool:
    return ConnectionPool(url, min_size=1, max_size=5, open=True,
                          check=ConnectionPool.check_connection, max_idle=240)  # drop stale conns after a DB restart/redeploy


def init_schema(pool: ConnectionPool) -> None:
    """Create the schema; a database from before users existed gets a nullable user_id on every data table,
    which stays NULL until users.ensure_owner adopts the rows and calls finish_user_columns."""
    with pool.connection() as conn:  # one transaction: committed on exit
        conn.execute(SCHEMA)
        for t in DATA_TABLES:
            conn.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
        finish_user_columns(conn)


def finish_user_columns(conn) -> None:
    """Once no legacy row is left without an owner: user_id becomes NOT NULL and keyed tables get (user_id, key) PKs."""
    for t, key in DATA_TABLES.items():
        if t == "audit_log":
            continue
        nullable = conn.execute("SELECT is_nullable = 'YES' FROM information_schema.columns WHERE table_schema = current_schema()"
                                " AND table_name = %s AND column_name = 'user_id'", (t,)).fetchone()[0]
        if not nullable or conn.execute(f"SELECT 1 FROM {t} WHERE user_id IS NULL LIMIT 1").fetchone():
            continue
        conn.execute(f"ALTER TABLE {t} ALTER COLUMN user_id SET NOT NULL")
        if key:
            conn.execute(f"ALTER TABLE {t} DROP CONSTRAINT {t}_pkey, ADD PRIMARY KEY (user_id, {key})")


def like_pattern(q: str) -> str:
    """Contains-match pattern for ILIKE with %, _ and backslash taken literally."""
    return "%" + re.sub(r"([\\%_])", r"\\\1", q) + "%"
