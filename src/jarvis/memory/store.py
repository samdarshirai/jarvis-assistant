"""Short-term and episodic memory, next to the long-term knowledge graph (jarvis.kg).

- Short-term items (`memory_item`): time-bound things the user mentioned, like "in Pune till Friday" or "waiting on the
  plumber". They are saved without a confirmation (the chat shows them with Undo), shown in every prompt while active,
  and expire on their own. "Keep forever" turns one into a long-term graph fact.
- Episodes (`conversation.summary`): what each ended conversation was about and what it left open, written by the
  conversation-end digest; the last two days are shown in the prompt, older ones are found with the recall tool.
- The rolling summary (`conversation.rolling_summary`): the older part of one long conversation, past the history window.

Every query is scoped by user_id."""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from jarvis.kg.facts import norm

KINDS = ("context", "plan", "commitment")
ORIGINS = ("tool", "digest")
MAX_TEXT, MAX_QUOTE = 300, 300
DEFAULT_TTL = timedelta(days=7)
MIN_TTL, MAX_TTL = timedelta(hours=1), timedelta(days=60)
SAME = 0.7  # word overlap (Jaccard) at which a new item is the same as an active one
RECENT = timedelta(hours=48)
MAX_SUMMARY, MAX_LOOPS, MAX_LOOP = 600, 5, 160
_STOP = frozenset("the a an of to in on at is am are was be i im i'm my me we you it its and or for with till until "
                  "by from this that".split())
COLS = "id, kind, text, expires_at, status, origin, conversation_id, created_at, seen_count, durable"


@dataclass(frozen=True)
class Item:
    id: int
    kind: str
    text: str
    expires_at: datetime
    status: str
    origin: str
    conversation_id: int | None
    created_at: datetime
    seen_count: int
    durable: bool


def item_json(i: Item) -> dict:
    return {"id": i.id, "kind": i.kind, "text": i.text, "expires_at": i.expires_at.isoformat(), "status": i.status,
            "origin": i.origin, "conversation_id": i.conversation_id, "created_at": i.created_at.isoformat()}


def words(text: str) -> frozenset[str]:
    return frozenset(t for t in re.split(r"[\W_]+", norm(text)) if t and t not in _STOP)


def same(a: str, b: str) -> bool:
    """Two short notes say the same thing: equal once normalised, or mostly the same words."""
    if norm(a) == norm(b):
        return True
    wa, wb = words(a), words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= SAME


def clamp_expiry(expires_at: datetime | None, now: datetime) -> datetime:
    """A sane end: the default week when none was given, never sooner than an hour or later than 60 days."""
    if expires_at is None:
        return now + DEFAULT_TTL
    if expires_at.tzinfo is None:
        raise ValueError("expires_at must carry a timezone")
    return min(max(expires_at, now + MIN_TTL), now + MAX_TTL)


def clean(text: str, cap: int) -> str:
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("Text is blank.")
    return text[:cap]


class MemoryStore:
    def __init__(self, pool, user_id: int, clock=lambda: datetime.now(timezone.utc)):
        self.pool, self.user_id, self.clock = pool, user_id, clock
        self.version = 0  # bumped on every write: the prompt block's cache key

    # --- short-term items ---
    def add_item(self, text: str, kind: str = "context", expires_at: datetime | None = None, *, origin: str = "tool",
                 conversation_id: int | None = None, quote: str | None = None, durable: bool = False,
                 conn=None) -> tuple[Item, bool]:
        """Save an item, or refresh the active one that says the same thing (later expiry wins, and a mention in
        another conversation counts). Returns (item, created). `conn` runs it inside the caller's transaction."""
        if kind not in KINDS:
            raise ValueError(f"Unknown kind: {kind!r}.")
        if origin not in ORIGINS:
            raise ValueError(f"Unknown origin: {origin!r}.")
        text, now = clean(text, MAX_TEXT), self.clock()
        expires_at = clamp_expiry(expires_at, now)
        quote = " ".join(quote.split())[:MAX_QUOTE] if quote else None

        def go(c):
            for row in c.execute(f"SELECT {COLS} FROM memory_item WHERE user_id = %s AND status = 'active'"
                                 " AND expires_at > %s ORDER BY id FOR UPDATE", (self.user_id, now)).fetchall():
                old = Item(*row)
                if same(old.text, text):
                    new_conv = ("%(conv)s::int IS NOT NULL AND NOT (%(conv)s::int = ANY(seen_in))"
                                " AND %(conv)s::int IS DISTINCT FROM conversation_id")
                    r = c.execute(
                        f"UPDATE memory_item SET expires_at = greatest(expires_at, %(exp)s), last_seen_at = %(now)s,"
                        f" seen_count = seen_count + CASE WHEN {new_conv} THEN 1 ELSE 0 END,"
                        f" seen_in = CASE WHEN {new_conv} THEN array_append(seen_in, %(conv)s::int) ELSE seen_in END,"
                        f" durable = durable OR %(durable)s, conversation_id = coalesce(conversation_id, %(conv)s::int)"
                        f" WHERE user_id = %(u)s AND id = %(id)s RETURNING {COLS}",
                        {"exp": expires_at, "now": now, "conv": conversation_id, "durable": durable,
                         "u": self.user_id, "id": old.id}).fetchone()
                    return Item(*r), False
            r = c.execute(
                f"INSERT INTO memory_item (user_id, kind, text, text_norm, expires_at, origin, conversation_id, quote,"
                f" durable, created_at, last_seen_at, seen_in) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                f" %s) RETURNING {COLS}",
                (self.user_id, kind, text, norm(text), expires_at, origin, conversation_id, quote, durable, now,
                 now, [conversation_id] if conversation_id else [])).fetchone()
            return Item(*r), True

        out = go(conn) if conn is not None else self._tx(go)
        self.version += 1
        return out

    def _tx(self, fn):
        with self.pool.connection() as conn:
            return fn(conn)

    def active(self, limit: int | None = None) -> list[Item]:
        """Items in force now, the soonest to end first."""
        with self.pool.connection() as conn:
            rows = conn.execute(f"SELECT {COLS} FROM memory_item WHERE user_id = %s AND status = 'active'"
                                f" AND expires_at > %s ORDER BY expires_at, id LIMIT %s",
                                (self.user_id, self.clock(), limit)).fetchall()
        return [Item(*r) for r in rows]

    def get(self, item_id: int) -> Item | None:
        with self.pool.connection() as conn:
            r = conn.execute(f"SELECT {COLS} FROM memory_item WHERE user_id = %s AND id = %s",
                             (self.user_id, item_id)).fetchone()
        return Item(*r) if r else None

    def update(self, item_id: int, *, text: str | None = None, expires_at: datetime | None = None) -> Item:
        """Edit an active item's text or end. LookupError for an unknown, foreign or no longer active one."""
        now = self.clock()
        sets, args = [], []
        if text is not None:
            t = clean(text, MAX_TEXT)
            sets += ["text = %s", "text_norm = %s"]
            args += [t, norm(t)]
        if expires_at is not None:
            sets.append("expires_at = %s")
            args.append(clamp_expiry(expires_at, now))
        if not sets:
            raise ValueError("Nothing to change.")
        with self.pool.connection() as conn:
            r = conn.execute(f"UPDATE memory_item SET {', '.join(sets)} WHERE user_id = %s AND id = %s"
                             f" AND status = 'active' RETURNING {COLS}", (*args, self.user_id, item_id)).fetchone()
        if r is None:
            raise LookupError("item")
        self.version += 1
        return Item(*r)

    def delete(self, item_id: int) -> bool:
        """Gone for good (Undo, or the user deleting it): not kept as a tombstone."""
        with self.pool.connection() as conn:
            gone = conn.execute("DELETE FROM memory_item WHERE user_id = %s AND id = %s RETURNING 1",
                                (self.user_id, item_id)).fetchone() is not None
            if gone:
                conn.execute("DELETE FROM memory_embedding WHERE user_id = %s AND ref_kind = 'item' AND ref_id = %s",
                             (self.user_id, item_id))
        self.version += 1
        return gone

    def latest_in(self, conversation_id: int, within: timedelta = timedelta(hours=2)) -> Item | None:
        """The newest active item saved in this conversation lately: what "forget that" means."""
        with self.pool.connection() as conn:
            r = conn.execute(f"SELECT {COLS} FROM memory_item WHERE user_id = %s AND conversation_id = %s"
                             f" AND status = 'active' AND last_seen_at > %s ORDER BY last_seen_at DESC, id DESC LIMIT 1",
                             (self.user_id, conversation_id, self.clock() - within)).fetchone()
        return Item(*r) if r else None

    def promote(self, item_id: int, kg) -> dict:
        """Keep forever: the item becomes a user-stated `note` fact about the user, and stops expiring."""
        item = self.get(item_id)
        if item is None or item.status != "active":
            raise LookupError("item")
        me = kg.self_entity()
        saved = kg.add_statements([{"entity_id": me.id, "name": me.display_name, "kind": "self",
                                    "facts": [("note", item.text)]}])
        with self.pool.connection() as conn:
            conn.execute("UPDATE memory_item SET status = 'promoted', decided_at = %s WHERE user_id = %s AND id = %s",
                         (self.clock(), self.user_id, item_id))
        self.version += 1
        return {"fact_ids": [f.id for s in saved for f in s["facts"]]}

    def expire_due(self) -> int:
        with self.pool.connection() as conn:
            n = conn.execute("UPDATE memory_item SET status = 'expired', decided_at = %s WHERE user_id = %s"
                             " AND status = 'active' AND expires_at <= %s", (self.clock(), self.user_id,
                                                                             self.clock())).rowcount
        if n:
            self.version += 1
        return n

    def promotable(self, min_seen: int = 3) -> list[tuple[Item, str | None]]:
        """Active items worth suggesting as lasting facts, not suggested before: (item, quote)."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {COLS}, quote FROM memory_item WHERE user_id = %s AND status = 'active' AND suggested_at IS NULL"
                f" AND conversation_id IS NOT NULL AND (durable OR seen_count >= %s) ORDER BY id",
                (self.user_id, min_seen)).fetchall()
        return [(Item(*r[:-1]), r[-1]) for r in rows]

    def mark_suggested(self, item_id: int, conn=None) -> None:
        sql = "UPDATE memory_item SET suggested_at = %s WHERE user_id = %s AND id = %s"
        if conn is not None:
            conn.execute(sql, (self.clock(), self.user_id, item_id))
        else:
            with self.pool.connection() as c:
                c.execute(sql, (self.clock(), self.user_id, item_id))

    # --- episodes: conversation summaries ---
    def save_digest(self, conn, conv_id: int, summary: str | None, open_loops: list[str], untrusted: bool) -> None:
        """In the caller's transaction (the one that marks the conversation read)."""
        summary = " ".join(summary.split())[:MAX_SUMMARY] if summary else None
        loops = [" ".join(x.split())[:MAX_LOOP] for x in open_loops if isinstance(x, str) and x.strip()][:MAX_LOOPS]
        conn.execute("UPDATE conversation SET summary = %s, open_loops = %s, summary_untrusted = %s"
                     " WHERE user_id = %s AND id = %s", (summary, Jsonb(loops), untrusted, self.user_id, conv_id))
        self.version += 1

    def recent_summaries(self, exclude_thread: str | None = None, within: timedelta = RECENT,
                         limit: int = 3) -> list[dict]:
        """The latest summarised conversations of the last two days, newest first, not counting the current one."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, title, last_at, summary, open_loops, summary_untrusted FROM conversation WHERE user_id = %s"
                " AND summary IS NOT NULL AND NOT off_record AND last_at > %s AND thread_id IS DISTINCT FROM %s"
                " ORDER BY last_at DESC LIMIT %s",
                (self.user_id, self.clock() - within, exclude_thread, limit)).fetchall()
        return [{"id": i, "title": t, "last_at": at, "summary": s, "open_loops": loops or [], "untrusted": u}
                for i, t, at, s, loops, u in rows]

    def open_loops(self, within: timedelta = timedelta(days=3)) -> list[str]:
        """Open loops of recent conversations (newest first, deduplicated): the morning brief's "still open"."""
        out: list[str] = []
        for s in self.recent_summaries(within=within, limit=10):
            if s["untrusted"]:
                continue
            for loop in s["open_loops"]:
                if not any(same(loop, x) for x in out):
                    out.append(loop)
        return out[:MAX_LOOPS]

    def summaries_for(self, conv_ids: list[int]) -> dict[int, str]:
        if not conv_ids:
            return {}
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT id, summary FROM conversation WHERE user_id = %s AND id = ANY(%s)"
                                " AND summary IS NOT NULL", (self.user_id, conv_ids)).fetchall()
        return dict(rows)

    def conversation_for(self, thread_id: str) -> tuple[int, bool] | None:
        """(conversation id, off the record) of a checkpoint thread of this user."""
        with self.pool.connection() as conn:
            r = conn.execute("SELECT id, off_record FROM conversation WHERE user_id = %s AND thread_id = %s",
                             (self.user_id, thread_id)).fetchone()
        return (r[0], r[1]) if r else None

    # --- the rolling summary of one long conversation ---
    def rolling(self, thread_id: str) -> tuple[str | None, int]:
        with self.pool.connection() as conn:
            r = conn.execute("SELECT rolling_summary, coalesce(rolling_upto, 0) FROM conversation WHERE user_id = %s"
                             " AND thread_id = %s AND NOT off_record", (self.user_id, thread_id)).fetchone()
        return (r[0], r[1]) if r else (None, 0)

    def set_rolling(self, thread_id: str, summary: str, upto: int) -> None:
        """Only moves forward: a slower, older run never overwrites a newer summary. Never kept off the record."""
        with self.pool.connection() as conn:
            conn.execute("UPDATE conversation SET rolling_summary = %s, rolling_upto = %s WHERE user_id = %s"
                         " AND thread_id = %s AND NOT off_record AND coalesce(rolling_upto, 0) < %s",
                         (" ".join(summary.split())[:2000], upto, self.user_id, thread_id, upto))

    # --- consolidation helpers ---
    def merge(self, keep_id: int, drop_id: int) -> bool:
        """Fold a duplicate active item into another: the later end and both mentions are kept, the duplicate goes."""
        with self.pool.connection() as conn:
            drop = conn.execute("DELETE FROM memory_item WHERE user_id = %s AND id = %s AND status = 'active'"
                                " RETURNING expires_at, seen_count, durable", (self.user_id, drop_id)).fetchone()
            if drop is None:
                return False
            conn.execute("UPDATE memory_item SET expires_at = greatest(expires_at, %s), seen_count = seen_count + %s,"
                         " durable = durable OR %s WHERE user_id = %s AND id = %s", (*drop, self.user_id, keep_id))
            conn.execute("DELETE FROM memory_embedding WHERE user_id = %s AND ref_kind = 'item' AND ref_id = %s",
                         (self.user_id, drop_id))
        self.version += 1
        return True

    def item_vectors(self, ids: list[int], model: str) -> dict[int, list[float]]:
        with self.pool.connection() as conn:
            return dict(conn.execute("SELECT ref_id, vec FROM memory_embedding WHERE user_id = %s AND ref_kind = 'item'"
                                     " AND model = %s AND ref_id = ANY(%s)", (self.user_id, model, ids)).fetchall())
