"""Candidate facts proposed by the conversation extractor, waiting for the user's decision. Nothing here writes to the
graph; every query is scoped by user_id."""
from datetime import datetime, timedelta
from typing import NamedTuple

from jarvis.conversations import IDLE

MAX_ATTEMPTS = 3  # failed extraction runs before a conversation is given up on (marked extracted, no candidates)
MAX_AGE = timedelta(days=7)  # older conversations are never extracted: switching the flag on does not backfill history
EXPIRE_AFTER = timedelta(days=30)  # an undecided candidate this old is dropped from the Learned inbox
COLUMNS = ("subject_name", "subject_kind", "relation", "predicate", "value", "quote", "subject_norm", "value_norm",
           "replaces_fact_id")


class Due(NamedTuple):
    id: int
    thread_id: str
    last_at: datetime


class CandidateStore:
    def __init__(self, pool, user_id: int):
        self.pool, self.user_id = pool, user_id

    def due(self, now: datetime, limit: int) -> list[Due]:
        """Conversations that have ended and not been read yet: closed, or open but idle longer than IDLE
        (chat and Telegram conversations are closed lazily, by the next message)."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, thread_id, last_at FROM conversation WHERE user_id = %s AND extracted_at IS NULL"
                " AND NOT off_record AND last_at > %s AND (closed_at IS NOT NULL OR last_at < %s)"
                " ORDER BY last_at, id LIMIT %s",
                (self.user_id, now - MAX_AGE, now - IDLE, limit)).fetchall()
        return [Due(*r) for r in rows]

    def save(self, conv_id: int, seen_last_at: datetime, rows: list[dict], now: datetime, extra=None) -> bool:
        """Store the candidates and mark the conversation extracted in one transaction. False (nothing written) when it
        was already extracted, or has moved on since `seen_last_at` (resumed and extended while the model ran).
        `extra(conn)` writes the rest of the digest (summary, short-term notes) in the same transaction."""
        with self.pool.connection() as conn:
            if not conn.execute(
                    "UPDATE conversation SET extracted_at = %s WHERE id = %s AND user_id = %s AND extracted_at IS NULL"
                    " AND last_at = %s RETURNING 1", (now, conv_id, self.user_id, seen_last_at)).fetchone():
                return False
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO fact_candidate (user_id, conversation_id, {', '.join(COLUMNS)})"
                    f" VALUES (%s, %s, {', '.join(['%s'] * len(COLUMNS))})",
                    [(self.user_id, conv_id, *(r.get(c) for c in COLUMNS)) for r in rows])
            if extra is not None:
                extra(conn)
        return True

    def fail(self, conv_id: int, now: datetime) -> bool:
        """Count a failed run; True when this was the last attempt and the conversation is now given up on."""
        with self.pool.connection() as conn:
            r = conn.execute(
                "UPDATE conversation SET extract_attempts = extract_attempts + 1,"
                " extracted_at = CASE WHEN extract_attempts + 1 >= %s THEN %s END"
                " WHERE id = %s AND user_id = %s AND extracted_at IS NULL RETURNING extracted_at IS NOT NULL",
                (MAX_ATTEMPTS, now, conv_id, self.user_id)).fetchone()
        return bool(r and r[0])

    def list_new(self) -> list[dict]:
        """Undecided candidates grouped by conversation, newest conversation first, oldest candidate first within it.
        `replaces` is the current value of the fact a candidate would change (None when it adds something new)."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT c.id, c.conversation_id, v.title, v.last_at, c.subject_name, c.subject_kind, c.relation,"
                " c.predicate, c.value, c.quote, f.object_value FROM fact_candidate c"
                " JOIN conversation v ON v.user_id = c.user_id AND v.id = c.conversation_id"
                " LEFT JOIN fact f ON f.user_id = c.user_id AND f.id = c.replaces_fact_id AND f.status = 'active'"
                " WHERE c.user_id = %s AND c.status = 'new' ORDER BY v.last_at DESC, c.conversation_id DESC, c.id",
                (self.user_id,)).fetchall()
        groups: dict[int, dict] = {}
        for cid, conv_id, title, last_at, subject, kind, relation, predicate, value, quote, replaces in rows:
            g = groups.setdefault(conv_id, {"conversation": {"id": conv_id, "title": title,
                                                             "last_at": last_at.isoformat()}, "items": []})
            g["items"].append({"id": cid, "subject": subject, "subject_kind": kind, "relation": relation,
                               "predicate": predicate, "value": value, "quote": quote, "replaces": replaces})
        return list(groups.values())

    def count_new(self) -> int:
        with self.pool.connection() as conn:
            return conn.execute("SELECT count(*) FROM fact_candidate WHERE user_id = %s AND status = 'new'",
                                (self.user_id,)).fetchone()[0]

    def decide(self, ids: list[int], status: str, now: datetime) -> list[int]:
        """Mark undecided candidates dismissed (approval goes through kg.review, which also writes the graph). Returns
        the ids actually changed: unknown, foreign and already-decided ones are skipped."""
        if status != "dismissed":
            raise ValueError(f"Cannot decide a candidate as {status!r} here.")
        with self.pool.connection() as conn:
            rows = conn.execute(
                "UPDATE fact_candidate SET status = %s, decided_at = %s WHERE user_id = %s AND id = ANY(%s)"
                " AND status = 'new' RETURNING id", (status, now, self.user_id, ids)).fetchall()
        return sorted(r[0] for r in rows)

    def expire(self, now: datetime) -> int:
        """Undecided candidates older than EXPIRE_AFTER become 'expired': never decided, so not remembered as dismissed."""
        with self.pool.connection() as conn:
            return conn.execute(
                "UPDATE fact_candidate SET status = 'expired' WHERE user_id = %s AND status = 'new' AND created_at < %s",
                (self.user_id, now - EXPIRE_AFTER)).rowcount

    def known(self, keys: list[tuple[str, str, str]]) -> set[tuple[str, str, str]]:
        """Of (subject_norm, predicate, value_norm) keys, those already proposed and not expired: waiting, approved or
        dismissed (an expired one was never decided, so it may be proposed again)."""
        if not keys:
            return set()
        subj, pred, val = ([k[i] for k in keys] for i in range(3))
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT subject_norm, predicate, value_norm FROM fact_candidate WHERE user_id = %s"
                " AND status IN ('new', 'approved', 'dismissed') AND (subject_norm, predicate, value_norm) IN"
                " (SELECT * FROM unnest(%s::text[], %s::text[], %s::text[]))", (self.user_id, subj, pred, val)).fetchall()
        return {tuple(r) for r in rows}
