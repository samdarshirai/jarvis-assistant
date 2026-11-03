"""Candidate facts proposed by the conversation extractor, waiting for the user's decision. Nothing here writes to the
graph; every query is scoped by user_id."""
from datetime import datetime, timedelta
from typing import NamedTuple

from jarvis.conversations import IDLE

MAX_ATTEMPTS = 3  # failed extraction runs before a conversation is given up on (marked extracted, no candidates)
MAX_AGE = timedelta(days=7)  # older conversations are never extracted: switching the flag on does not backfill history
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
                " AND last_at > %s AND (closed_at IS NOT NULL OR last_at < %s) ORDER BY last_at, id LIMIT %s",
                (self.user_id, now - MAX_AGE, now - IDLE, limit)).fetchall()
        return [Due(*r) for r in rows]

    def save(self, conv_id: int, seen_last_at: datetime, rows: list[dict], now: datetime) -> bool:
        """Store the candidates and mark the conversation extracted in one transaction. False (nothing written) when it
        was already extracted, or has moved on since `seen_last_at` (resumed and extended while the model ran)."""
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
