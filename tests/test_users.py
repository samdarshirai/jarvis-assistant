import os
from types import SimpleNamespace

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg_pool import ConnectionPool

from jarvis.db import SCHEMA, init_schema
from jarvis.kg.schema import KG_SCHEMA
from jarvis.users import MAX_USERS, SecretStore, UserStore, ensure_owner

KEY = Fernet.generate_key().decode()
TZ = "Europe/Berlin"

# The single-owner schema as it was before users existed: what a deployed database looks like before the upgrade.
LEGACY_SCHEMA = """
CREATE TABLE audit_log (id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(), kind TEXT NOT NULL,
  name TEXT NOT NULL, args JSONB, result JSONB, confirmation TEXT, latency_ms INTEGER, model TEXT,
  tokens_in INTEGER, tokens_out INTEGER, cost_usd NUMERIC);
CREATE TABLE oauth_tokens (provider TEXT PRIMARY KEY, blob BYTEA NOT NULL);
CREATE TABLE devices (id SERIAL PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, fcm_token TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen TIMESTAMPTZ);
CREATE TABLE memories (id SERIAL PRIMARY KEY, text TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE notes (id SERIAL PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body)) STORED);
CREATE TABLE mail_seen (message_id TEXT PRIMARY KEY, outcome TEXT NOT NULL, event_id TEXT, at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE alerts_sent (key TEXT PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE auto_events (event_id TEXT PRIMARY KEY, message_id TEXT NOT NULL, at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE proactive_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL,
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id));
"""
LEGACY_ROWS = """
INSERT INTO audit_log (kind, name) VALUES ('tool', 'x');
INSERT INTO oauth_tokens VALUES ('google', 'blob');
INSERT INTO devices (token_hash) VALUES ('h');
INSERT INTO memories (text) VALUES ('m');
INSERT INTO notes (title) VALUES ('n');
INSERT INTO mail_seen (message_id, outcome) VALUES ('m1', 'created');
INSERT INTO alerts_sent (key) VALUES ('k');
INSERT INTO auto_events (event_id, message_id) VALUES ('e1', 'm1');
INSERT INTO proactive_state VALUES ('cursor', '1');
INSERT INTO checkpoints (thread_id, checkpoint_id) VALUES ('owner', 'c1');
"""
DATA_TABLES = ("audit_log", "oauth_tokens", "devices", "memories", "notes", "mail_seen", "alerts_sent",
               "auto_events", "proactive_state")
NEW_PKS = {"oauth_tokens": ["user_id", "provider"], "mail_seen": ["user_id", "message_id"],
           "alerts_sent": ["user_id", "key"], "auto_events": ["user_id", "event_id"],
           "proactive_state": ["user_id", "key"]}


def settings(**over):
    base = dict(owner_email="", timezone=TZ, fernet_key=KEY, openrouter_api_key="sk-or-secret",
                models_fast="a/x", models_strong="b/y", deepgram_api_key="", tavily_api_key="")
    return SimpleNamespace(**{**base, **over})


@pytest.fixture
def scratch(pool):
    """A pool on an empty private schema, so a test can build legacy or checkpoint tables without touching the shared ones."""
    with pool.connection() as c:
        c.execute("DROP SCHEMA IF EXISTS scratch CASCADE")
        c.execute("CREATE SCHEMA scratch")
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], kwargs={"options": "-c search_path=scratch"}, open=True)
    yield p
    p.close()
    with pool.connection() as c:
        c.execute("DROP SCHEMA scratch CASCADE")


def pk(c, table):
    return [r[0] for r in c.execute(
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
        " WHERE i.indrelid = %s::regclass AND i.indisprimary ORDER BY array_position(i.indkey::int2[], a.attnum)",
        (table,)).fetchall()]


def test_add_lowercases_and_rejects_duplicate(pool):
    users = UserStore(pool)
    u = users.add("  Ann@Example.COM ", TZ)
    assert (u.email, u.tz, u.is_owner, u.disabled, u.google_sub) == ("ann@example.com", TZ, False, False, None)
    assert users.by_email("ANN@example.com") == u == users.get(u.id)
    with pytest.raises(ValueError):
        users.add("ann@EXAMPLE.com", TZ)
    for bad in ("", "ann", "ann@", "@x.com", "a b@x.com", "ann@localhost"):
        with pytest.raises(ValueError):
            users.add(bad, TZ)
    users.set_sub(u.id, "sub-1")
    users.set_tz(u.id, "Asia/Kolkata")
    users.set_disabled(u.id, True)
    assert users.get(u.id) == u.__class__(u.id, "ann@example.com", "sub-1", "Asia/Kolkata", False, True)
    assert users.enabled() == [] and users.list() == [users.get(u.id)]


def test_cap_is_100(pool):
    users = UserStore(pool)
    assert MAX_USERS == 100
    for i in range(MAX_USERS):
        users.add(f"u{i}@example.com", TZ)
    with pytest.raises(ValueError):
        users.add("one-more@example.com", TZ)
    assert len(users.list()) == MAX_USERS


def test_secret_roundtrip_and_encrypted_at_rest(pool):
    users, secrets = UserStore(pool), SecretStore(pool, KEY)
    a, b = users.add("a@example.com", TZ).id, users.add("b@example.com", TZ).id
    secrets.put(a, "llm_key", "sk-plaintext-123")
    secrets.put(a, "llm_key", "sk-plaintext-456")  # replaces
    secrets.put(a, "model_fast", "m/f")
    assert secrets.get(a, "llm_key") == "sk-plaintext-456"
    assert secrets.all(a) == {"llm_key": "sk-plaintext-456", "model_fast": "m/f"}
    assert secrets.all(b) == {} and secrets.get(b, "llm_key") is None
    with pool.connection() as c:
        blobs = [bytes(r[0]) for r in c.execute("SELECT blob FROM user_secrets").fetchall()]
    assert blobs and all(b"sk-plaintext" not in x for x in blobs)
    with pytest.raises(ValueError) as e:
        secrets.put(a, "password", "hunter2")
    assert "hunter2" not in str(e.value)
    secrets.delete(a, "llm_key")
    assert secrets.all(a) == {"model_fast": "m/f"}


def test_init_schema_drops_stored_deepgram_and_tavily_keys(pool):
    from jarvis.db import init_schema
    uid = UserStore(pool).add("a@example.com", TZ).id
    with pool.connection() as c:
        for n in ("deepgram_key", "tavily_key", "llm_key"):
            c.execute("INSERT INTO user_secrets (user_id, name, blob) VALUES (%s, %s, %s)", (uid, n, b"x"))
    init_schema(pool)
    with pool.connection() as c:
        assert [r[0] for r in c.execute("SELECT name FROM user_secrets").fetchall()] == ["llm_key"]


def test_delete_cascades_to_every_data_table(scratch):
    init_schema(scratch)
    with scratch.connection() as c:
        c.execute("CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL)")
    users = UserStore(scratch)
    ids = [users.add(f"{n}@example.com", TZ).id for n in ("gone", "kept")]
    SecretStore(scratch, KEY).put(ids[0], "llm_key", "k")
    with scratch.connection() as c:
        for uid in ids:
            c.execute("INSERT INTO audit_log (user_id, kind, name) VALUES (%s, 'tool', 'x')", (uid,))
            c.execute("INSERT INTO oauth_tokens (user_id, provider, blob) VALUES (%s, 'google', 'b')", (uid,))
            c.execute("INSERT INTO devices (user_id, token_hash) VALUES (%s, %s)", (uid, f"h{uid}"))
            c.execute("INSERT INTO memories (user_id, text) VALUES (%s, 'm')", (uid,))
            c.execute("INSERT INTO notes (user_id, title) VALUES (%s, 'n')", (uid,))
            c.execute("INSERT INTO mail_seen (user_id, message_id, outcome) VALUES (%s, 'm1', 'x')", (uid,))
            c.execute("INSERT INTO alerts_sent (user_id, key) VALUES (%s, 'k')", (uid,))
            c.execute("INSERT INTO auto_events (user_id, event_id, message_id) VALUES (%s, 'e1', 'm1')", (uid,))
            c.execute("INSERT INTO proactive_state (user_id, key, value) VALUES (%s, 'k', 'v')", (uid,))
            c.execute("INSERT INTO checkpoints VALUES (%s, 'c1')", (f"user:{uid}",))
    users.delete(ids[0])
    assert users.get(ids[0]) is None
    with scratch.connection() as c:
        for t in (*DATA_TABLES, "user_secrets"):
            left = dict(c.execute(f"SELECT user_id, count(*) FROM {t} GROUP BY user_id").fetchall())
            assert ids[0] not in left, t
        for t in DATA_TABLES:
            assert c.execute(f"SELECT count(*) FROM {t} WHERE user_id = %s", (ids[1],)).fetchone()[0] == 1, t
        assert c.execute("SELECT thread_id FROM checkpoints").fetchall() == [(f"user:{ids[1]}",)]


def test_fresh_schema_requires_user_id(pool):
    with pool.connection() as c:
        with pytest.raises(psycopg.errors.NotNullViolation):
            c.execute("INSERT INTO notes (title) VALUES ('x')")


def test_ensure_owner_adopts_legacy_rows(scratch):
    with scratch.connection() as c:
        c.execute(LEGACY_SCHEMA)
        c.execute(LEGACY_ROWS)
    init_schema(scratch)
    init_schema(scratch)  # the upgrade path is idempotent
    uid = ensure_owner(scratch, settings(owner_email="Owner@Example.com"))
    owner = UserStore(scratch).get(uid)
    assert (owner.email, owner.tz, owner.is_owner) == ("owner@example.com", TZ, True)
    with scratch.connection() as c:
        for t in DATA_TABLES:
            assert c.execute(f"SELECT array_agg(DISTINCT user_id) FROM {t}").fetchone()[0] == [uid], t
        for t in DATA_TABLES[1:]:  # audit_log keeps a nullable user_id for system rows
            assert c.execute("SELECT is_nullable FROM information_schema.columns WHERE table_schema = 'scratch'"
                             " AND table_name = %s AND column_name = 'user_id'", (t,)).fetchone()[0] == "NO", t
        for t, cols in NEW_PKS.items():
            assert pk(c, t) == cols, t
        assert c.execute("SELECT thread_id FROM checkpoints").fetchall() == [(f"user:{uid}",)]
        with pytest.raises(psycopg.errors.NotNullViolation):
            c.execute("INSERT INTO notes (title) VALUES ('x')")
    assert SecretStore(scratch, KEY).all(uid) == {"llm_key": "sk-or-secret", "model_fast": "a/x", "model_strong": "b/y"}


def test_init_schema_on_an_upgraded_database_runs_no_add_column(pool):
    # ALTER TABLE takes ACCESS EXCLUSIVE even when IF NOT EXISTS makes it a no-op: every start and CLI run would queue
    seen = []

    class Recording:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, params=None):
            seen.append(sql)
            return self.conn.execute(sql, params)

    class Pool:
        def connection(self):
            from contextlib import contextmanager

            @contextmanager
            def cm():
                with pool.connection() as c:
                    yield Recording(c)
            return cm()

    init_schema(Pool())
    # the whole statement set of a re-run: the CREATE ... IF NOT EXISTS script, then catalog/row SELECTs only
    assert seen[0] is SCHEMA and "ALTER" not in SCHEMA.upper()
    # then the graph's CREATE ... IF NOT EXISTS script, and the one deliberate write: drop operator-level keys
    # that older versions stored per user
    assert "ALTER" not in KG_SCHEMA.upper()
    assert [s for s in seen[1:] if not s.lstrip().upper().startswith("SELECT")] == [
        KG_SCHEMA, "DELETE FROM user_secrets WHERE name IN ('deepgram_key', 'tavily_key')"]
    # a database still missing a guarded column gets it
    with pool.connection() as c:
        c.execute("ALTER TABLE auth_attempts DROP COLUMN finish_code_hash")
    init_schema(pool)
    with pool.connection() as c:
        assert c.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema()"
                         " AND table_name = 'auth_attempts' AND column_name = 'finish_code_hash'").fetchone()


def test_ensure_owner_is_idempotent(pool):
    assert ensure_owner(pool, settings()) is None  # empty database, no owner email: nothing to adopt, nobody created
    assert UserStore(pool).list() == []
    uid = ensure_owner(pool, settings(owner_email="owner@example.com"))
    assert ensure_owner(pool, settings(owner_email="owner@example.com")) == uid
    assert ensure_owner(pool, settings(owner_email="someone-else@example.com")) == uid
    assert [u.id for u in UserStore(pool).list()] == [uid]


def test_ensure_owner_requires_owner_email_when_legacy_rows_exist(scratch):
    with scratch.connection() as c:
        c.execute(LEGACY_SCHEMA)
        c.execute(LEGACY_ROWS)
    init_schema(scratch)
    with pytest.raises(ValueError, match="JARVIS_OWNER_EMAIL"):
        ensure_owner(scratch, settings())
    assert UserStore(scratch).list() == []


def test_display_name_schema_upgrade_and_reinitialization(pool, uids):
    users = UserStore(pool)
    assert users.get(uids[0]).display_name is None
    with pool.connection() as c:
        c.execute('ALTER TABLE users DROP COLUMN display_name')
    init_schema(pool)
    assert users.get(uids[0]).display_name is None
    with pool.connection() as c:
        c.execute('UPDATE users SET display_name = %s WHERE id = %s', ('Élodie', uids[0]))
    init_schema(pool)
    init_schema(pool)
    assert users.get(uids[0]).display_name == 'Élodie'
    assert users.get(uids[1]).display_name is None
