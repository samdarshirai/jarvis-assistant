import re
from dataclasses import dataclass

import psycopg
from cryptography.fernet import Fernet

from jarvis.db import DATA_TABLES, finish_user_columns

MAX_USERS = 100
SECRET_NAMES = ("llm_base_url", "llm_key", "model_fast", "model_strong", "deepgram_key", "tavily_key")
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")  # LangGraph's Postgres saver
LEGACY_THREAD = "owner"  # the single conversation thread from before users existed
_COLS = "id, email, google_sub, tz, is_owner, disabled"
_email_shape = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True)
class User:
    id: int
    email: str
    google_sub: str | None
    tz: str
    is_owner: bool
    disabled: bool


def thread_id(user_id: int) -> str:
    return f"user:{user_id}"


def clean_email(email: str) -> str:
    """Emails are stored and compared lowercased."""
    email = email.strip().lower()
    if not _email_shape.fullmatch(email):
        raise ValueError(f"Not an email address: {email!r}")
    return email


def _table_exists(c, name: str) -> bool:
    return c.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is not None


class UserStore:
    def __init__(self, pool):
        self.pool = pool

    def _one(self, where: str, arg) -> User | None:
        with self.pool.connection() as c:
            r = c.execute(f"SELECT {_COLS} FROM users WHERE {where}", (arg,)).fetchone()
        return User(*r) if r else None

    def add(self, email: str, tz: str, *, is_owner: bool = False) -> User:
        email = clean_email(email)
        # ponytail: count-then-insert can overshoot the cap under concurrent adds; users are added by hand from the CLI
        with self.pool.connection() as c:
            try:
                r = c.execute(f"INSERT INTO users (email, tz, is_owner) SELECT %s, %s, %s"
                              f" WHERE (SELECT count(*) FROM users) < %s RETURNING {_COLS}",
                              (email, tz, is_owner, MAX_USERS)).fetchone()
            except psycopg.errors.UniqueViolation:
                raise ValueError(f"{email} is already a user (or an owner already exists).") from None
        if r is None:
            raise ValueError(f"User limit reached ({MAX_USERS}).")
        return User(*r)

    def get(self, user_id: int) -> User | None:
        return self._one("id = %s", user_id)

    def by_email(self, email: str) -> User | None:
        return self._one("email = %s", email.strip().lower())

    def list(self) -> list[User]:
        with self.pool.connection() as c:
            return [User(*r) for r in c.execute(f"SELECT {_COLS} FROM users ORDER BY id").fetchall()]

    def enabled(self) -> list[User]:
        return [u for u in self.list() if not u.disabled]

    def _set(self, user_id: int, column: str, value) -> None:
        with self.pool.connection() as c:
            c.execute(f"UPDATE users SET {column} = %s WHERE id = %s", (value, user_id))

    def set_sub(self, user_id: int, sub: str) -> None:
        self._set(user_id, "google_sub", sub)

    def set_tz(self, user_id: int, tz: str) -> None:
        self._set(user_id, "tz", tz)

    def set_disabled(self, user_id: int, disabled: bool) -> None:
        self._set(user_id, "disabled", disabled)

    def delete(self, user_id: int) -> None:
        """The user's rows in every data table go with them (ON DELETE CASCADE), and so does their conversation."""
        with self.pool.connection() as c:
            c.execute("DELETE FROM users WHERE id = %s", (user_id,))
            for t in CHECKPOINT_TABLES:
                if _table_exists(c, t):
                    c.execute(f"DELETE FROM {t} WHERE thread_id = %s", (thread_id(user_id),))


def _put_secret(c, fernet: Fernet, user_id: int, name: str, value: str) -> None:
    if name not in SECRET_NAMES:
        raise ValueError(f"Unknown secret {name!r}")  # the name only: values never reach an error
    c.execute("INSERT INTO user_secrets (user_id, name, blob) VALUES (%s, %s, %s)"
              " ON CONFLICT (user_id, name) DO UPDATE SET blob = EXCLUDED.blob",
              (user_id, name, fernet.encrypt(value.encode())))


class SecretStore:
    """Per-user keys and endpoint settings, Fernet-encrypted at rest."""

    def __init__(self, pool, fernet_key: str):
        self.pool, self.fernet = pool, Fernet(fernet_key)

    def put(self, user_id: int, name: str, value: str) -> None:
        with self.pool.connection() as c:
            _put_secret(c, self.fernet, user_id, name, value)

    def get(self, user_id: int, name: str) -> str | None:
        with self.pool.connection() as c:
            r = c.execute("SELECT blob FROM user_secrets WHERE user_id = %s AND name = %s", (user_id, name)).fetchone()
        return self.fernet.decrypt(bytes(r[0])).decode() if r else None

    def delete(self, user_id: int, name: str) -> None:
        with self.pool.connection() as c:
            c.execute("DELETE FROM user_secrets WHERE user_id = %s AND name = %s", (user_id, name))

    def all(self, user_id: int) -> dict[str, str]:
        with self.pool.connection() as c:
            rows = c.execute("SELECT name, blob FROM user_secrets WHERE user_id = %s", (user_id,)).fetchall()
        return {n: self.fernet.decrypt(bytes(b)).decode() for n, b in rows}


def ensure_owner(pool, s) -> int | None:
    """The owner's user id, creating the owner from s.owner_email on first run: rows from the single-owner era are
    adopted, the env keys are copied into the owner's secrets and the old conversation thread is renamed, all in
    one transaction. None when there is no owner, no owner email and nothing to adopt. Idempotent."""
    with pool.connection() as c:
        r = c.execute("SELECT id FROM users WHERE is_owner").fetchone()
        if r:
            return r[0]
        legacy = any(c.execute(f"SELECT 1 FROM {t} WHERE user_id IS NULL LIMIT 1").fetchone() for t in DATA_TABLES)
        if not s.owner_email.strip():
            if legacy:
                raise ValueError("Existing data has no owner yet: set JARVIS_OWNER_EMAIL to the owner's Google email.")
            return None
        uid = c.execute("INSERT INTO users (email, tz, is_owner) VALUES (%s, %s, true)"
                        " ON CONFLICT (email) DO UPDATE SET is_owner = true RETURNING id",
                        (clean_email(s.owner_email), s.timezone)).fetchone()[0]
        for t in DATA_TABLES:
            c.execute(f"UPDATE {t} SET user_id = %s WHERE user_id IS NULL", (uid,))
        finish_user_columns(c)
        fernet = Fernet(s.fernet_key)
        for name, value in (("llm_key", s.openrouter_api_key), ("model_fast", s.models_fast),
                            ("model_strong", s.models_strong), ("deepgram_key", s.deepgram_api_key),
                            ("tavily_key", s.tavily_api_key)):
            if value:
                _put_secret(c, fernet, uid, name, value)
        for t in CHECKPOINT_TABLES:
            if _table_exists(c, t):
                c.execute(f"UPDATE {t} SET thread_id = %s WHERE thread_id = %s", (thread_id(uid), LEGACY_THREAD))
        return uid
