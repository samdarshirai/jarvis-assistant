from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg

IDLE = timedelta(minutes=30)
LEGACY_TITLE = "Earlier conversation"
MAX_MESSAGES = 200  # shown per conversation
COLS = "id, channel, surface, title, thread_id, created_at, last_at, closed_at"


@dataclass(frozen=True)
class Conversation:
    id: int
    channel: str  # where it started; never changes
    surface: str  # which of voice | chat | telegram currently holds it
    title: str | None
    thread_id: str
    created_at: datetime
    last_at: datetime
    closed_at: datetime | None


def _conv(r) -> Conversation | None:
    return Conversation(*r) if r else None


class ConversationStore:
    def __init__(self, pool, user_id: int, clock=lambda: datetime.now(timezone.utc)):
        self.pool, self.user_id, self.clock = pool, user_id, clock

    def open_for(self, surface: str) -> Conversation | None:
        with self.pool.connection() as conn:
            return _conv(conn.execute(f"SELECT {COLS} FROM conversation WHERE user_id = %s AND surface = %s"
                                      " AND closed_at IS NULL", (self.user_id, surface)).fetchone())

    def current(self, surface: str) -> Conversation:
        c = self.open_for(surface)
        if c and self.clock() - c.last_at <= IDLE:
            return c
        return self.start(surface)

    def start(self, surface: str, off_record: bool = False) -> Conversation:
        """A new conversation; `off_record`: no summary, no short-term notes, no fact candidates from it."""
        now = self.clock()
        try:
            with self.pool.connection() as conn:
                conn.execute("UPDATE conversation SET closed_at = %s WHERE user_id = %s AND surface = %s"
                             " AND closed_at IS NULL", (now, self.user_id, surface))
                return _conv(conn.execute(
                    f"INSERT INTO conversation (user_id, channel, surface, thread_id, created_at, last_at, off_record)"
                    f" VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {COLS}",
                    (self.user_id, surface, surface, f"user:{self.user_id}:c:{uuid.uuid4().hex}", now, now,
                     off_record)).fetchone())
        except psycopg.errors.UniqueViolation:  # a concurrent start won the partial index: use theirs
            c = self.open_for(surface)
            if c is None:
                raise
            return c

    def touch(self, conv_id: int, text: str | None = None) -> None:
        title = (text or "").strip()[:60] or None
        with self.pool.connection() as conn:
            conn.execute("UPDATE conversation SET last_at = %s, title = COALESCE(title, %s)"
                         " WHERE id = %s AND user_id = %s", (self.clock(), title, conv_id, self.user_id))

    def close(self, conv_id: int) -> None:
        with self.pool.connection() as conn:
            conn.execute("UPDATE conversation SET closed_at = %s WHERE id = %s AND user_id = %s AND closed_at IS NULL",
                         (self.clock(), conv_id, self.user_id))

    def get(self, conv_id: int) -> Conversation | None:
        with self.pool.connection() as conn:
            return _conv(conn.execute(f"SELECT {COLS} FROM conversation WHERE id = %s AND user_id = %s",
                                      (conv_id, self.user_id)).fetchone())

    def list(self, limit: int = 50) -> list[Conversation]:
        self._import_legacy()
        with self.pool.connection() as conn:
            rows = conn.execute(f"SELECT {COLS} FROM conversation WHERE user_id = %s ORDER BY last_at DESC LIMIT %s",
                                (self.user_id, limit)).fetchall()
        return [_conv(r) for r in rows]

    def resume(self, conv_id: int, surface: str) -> Conversation | None:
        target = self.get(conv_id)
        now = self.clock()
        # an open voice row is a live session, unless it is past IDLE (a redeploy mid-session leaves it open)
        if target is None or (target.closed_at is None and target.surface == "voice" and now - target.last_at <= IDLE):
            return None
        with self.pool.connection() as conn:
            conn.execute("UPDATE conversation SET closed_at = %s WHERE user_id = %s AND surface = %s"
                         " AND closed_at IS NULL AND id <> %s", (now, self.user_id, surface, conv_id))
            return _conv(conn.execute(f"UPDATE conversation SET surface = %s, closed_at = NULL, last_at = %s,"
                                      f" extracted_at = NULL, extract_attempts = 0 WHERE id = %s AND user_id = %s RETURNING {COLS}",
                                      (surface, now, conv_id, self.user_id)).fetchone())

    def _import_legacy(self) -> None:
        """The pre-conversations single thread `user:<id>` becomes one closed conversation, once."""
        legacy = f"user:{self.user_id}"
        with self.pool.connection() as conn:
            if conn.execute("SELECT to_regclass('checkpoints')").fetchone()[0] is None:
                return
            if conn.execute("SELECT 1 FROM checkpoints WHERE thread_id = %s LIMIT 1", (legacy,)).fetchone() is None:
                return
            conn.execute("INSERT INTO conversation (user_id, channel, surface, title, thread_id, closed_at, extracted_at)"
                         " VALUES (%s, 'chat', 'chat', %s, %s, now(), now()) ON CONFLICT (thread_id) DO NOTHING",
                         (self.user_id, LEGACY_TITLE, legacy))


def conversation_json(c: Conversation) -> dict:
    return {"id": c.id, "title": c.title, "channel": c.channel, "last_at": c.last_at.isoformat(),
            "open": c.closed_at is None}


async def messages_json(graph, thread_id: str) -> list[dict]:
    state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    out = []
    for m in state.values.get("messages", []):
        if m.type in ("human", "ai") and isinstance(m.content, str) and m.content:
            out.append({"role": "user" if m.type == "human" else "assistant", "text": m.content})
    return out[-MAX_MESSAGES:]  # the legacy thread is untrimmed
