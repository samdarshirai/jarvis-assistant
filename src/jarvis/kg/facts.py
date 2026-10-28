import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from psycopg.errors import UniqueViolation

from jarvis.kg.predicates import PREDICATES, SINGLE, TRUST


def norm(text: str) -> str:
    """NFKC, casefold, collapse whitespace, strip."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True)
class Fact:
    id: int
    subject_id: int
    predicate: str
    value: str
    status: str
    valid_from: datetime
    valid_to: datetime | None
    source_kind: str
    excerpt: str
    observed_at: datetime
    recorded_at: datetime


# provenance shown is the fact's highest-trust source, earliest link on ties
_SELECT = """
SELECT f.id, f.subject_id, f.predicate, f.object_value, f.status, f.valid_from, f.valid_to,
       s.kind, coalesce(s.excerpt, ''), s.observed_at, f.recorded_at
FROM fact f LEFT JOIN LATERAL (
  SELECT s.kind, s.excerpt, s.observed_at FROM fact_source fs
  JOIN source s ON s.user_id = fs.user_id AND s.id = fs.source_id
  WHERE fs.user_id = f.user_id AND fs.fact_id = f.id ORDER BY s.trust DESC, fs.first_seen, s.id LIMIT 1
) s ON true
WHERE f.user_id = %s"""


def _clean(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("Fact value is blank.")
    if len(value) > 200:
        raise ValueError("Fact value is longer than 200 characters.")
    return value


class FactsMixin:
    """Facts with provenance; needs self.pool, self.user_id, self.version."""

    def _write(self, fn):
        """Run fn(conn) in one transaction; retry once if a concurrent writer took the active slot."""
        for attempt in (0, 1):
            try:
                with self.pool.connection() as conn:
                    out = fn(conn)
                break
            except UniqueViolation:
                if attempt:
                    raise
        if out is not None and out is not False:
            self.version += 1
        return out

    def _fact(self, conn, fact_id: int) -> Fact | None:
        r = conn.execute(_SELECT + " AND f.id = %s", (self.user_id, fact_id)).fetchone()
        return Fact(*r) if r else None

    def _facts(self, entity_id: int, statuses: tuple[str, ...]) -> list[Fact]:
        with self.pool.connection() as conn:
            rows = conn.execute(_SELECT + " AND f.subject_id = %s AND f.status = ANY(%s)"
                                " ORDER BY f.valid_from DESC NULLS LAST, f.id DESC",
                                (self.user_id, entity_id, list(statuses))).fetchall()
        return [Fact(*r) for r in rows]

    def facts_for(self, entity_id: int, *, history: bool = False) -> list[Fact]:
        return self._facts(entity_id, ("active", "superseded", "retracted") if history else ("active",))

    def pending_for(self, entity_id: int) -> list[Fact]:
        return self._facts(entity_id, ("pending",))

    def add_fact(self, subject_id: int, predicate: str, value: str, *, source_kind: str, ref: str | None = None,
                 excerpt: str = "", observed_at: datetime | None = None) -> Fact:
        if predicate not in PREDICATES:
            raise ValueError(f"Unknown predicate: {predicate!r}.")
        if source_kind not in TRUST:
            raise ValueError(f"Unknown source kind: {source_kind!r}.")
        value = _clean(value)
        if observed_at is not None and observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware.")
        obs = observed_at or datetime.now(timezone.utc)
        ref = ref or f"user:{uuid.uuid4()}"
        return self._write(lambda conn: self._add(conn, subject_id, predicate, value, source_kind, ref,
                                                  excerpt[:200], obs))

    def _add(self, conn, subject_id, predicate, value, kind, ref, excerpt, obs) -> Fact:
        uid = self.user_id
        # locking the subject serialises its fact writers: N racers, dedupe of multi values (FK checks still pass)
        if not conn.execute("SELECT 1 FROM entity WHERE user_id = %s AND id = %s FOR NO KEY UPDATE",
                            (uid, subject_id)).fetchone():
            raise ValueError("unknown entity")
        t_in = TRUST[kind]
        active = None
        if predicate in SINGLE:
            active = conn.execute(
                "SELECT f.id, coalesce(f.valid_from, f.recorded_at), coalesce((SELECT max(s.trust) FROM fact_source fs"
                " JOIN source s ON s.user_id = fs.user_id AND s.id = fs.source_id"
                " WHERE fs.user_id = f.user_id AND fs.fact_id = f.id AND s.status = 'live'), 0)"
                " FROM fact f WHERE f.user_id = %s AND f.subject_id = %s AND f.predicate = %s AND f.status = 'active'"
                " FOR UPDATE OF f", (uid, subject_id, predicate)).fetchone()
        src = conn.execute(
            "INSERT INTO source (user_id, kind, ref, observed_at, excerpt, trust) VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (user_id, kind, ref) DO UPDATE SET last_seen_at = now() RETURNING id",
            (uid, kind, ref, obs, excerpt, t_in)).fetchone()[0]

        def link(fid):
            conn.execute("INSERT INTO fact_source (user_id, fact_id, source_id) VALUES (%s, %s, %s)"
                         " ON CONFLICT (fact_id, source_id) DO UPDATE SET last_confirmed_at = now()", (uid, fid, src))
            return self._fact(conn, fid)

        def insert(status, valid_to=None, superseded_by=None):
            return conn.execute(
                "INSERT INTO fact (user_id, subject_id, predicate, object_value, valid_from, valid_to, status,"
                " superseded_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (uid, subject_id, predicate, value, obs, valid_to, status, superseded_by)).fetchone()[0]

        same = norm(value)
        for fid, v in conn.execute("SELECT id, object_value FROM fact WHERE user_id = %s AND subject_id = %s"
                                   " AND predicate = %s AND status IN ('active', 'pending') ORDER BY id",
                                   (uid, subject_id, predicate)).fetchall():
            if norm(v) == same:
                return link(fid)  # repeat observation: confirm, never duplicate
        if active is None:
            return link(insert("active"))
        a_id, a_from, t_a = active
        if t_in > t_a or (t_in == t_a and obs > a_from):
            conn.execute("UPDATE fact SET status = 'superseded', valid_to = %s WHERE user_id = %s AND id = %s",
                         (max(obs, a_from), uid, a_id))
            new = insert("active")
            conn.execute("UPDATE fact SET superseded_by = %s WHERE user_id = %s AND id = %s", (new, uid, a_id))
            return link(new)
        if obs > a_from:  # newer but less trusted: ask the user
            return link(insert("pending"))
        return link(insert("superseded", a_from, a_id))  # older than the active fact: history

    def correct_fact(self, fact_id: int, value: str) -> Fact:
        value, ref = _clean(value), f"user:{uuid.uuid4()}"

        def go(conn):
            old = self._fact(conn, fact_id)
            if old is None:
                raise ValueError("unknown fact")
            new = self._add(conn, old.subject_id, old.predicate, value, "user_stated", ref, "corrected by user",
                            datetime.now(timezone.utc))
            # supersession leaves pending and multi-valued originals alone; retire them explicitly
            conn.execute("UPDATE fact SET status = 'superseded', superseded_by = %s,"
                         " valid_to = greatest(coalesce(valid_from, now()), now())"
                         " WHERE user_id = %s AND id = %s AND id <> %s AND status IN ('active', 'pending')",
                         (new.id, self.user_id, old.id, new.id))
            return self._fact(conn, new.id)
        return self._write(go)

    def retract_fact(self, fact_id: int) -> bool:
        return self._write(lambda conn: conn.execute(
            "UPDATE fact SET status = 'retracted', valid_to = coalesce(valid_to, now())"
            " WHERE user_id = %s AND id = %s RETURNING 1", (self.user_id, fact_id)).fetchone() is not None)

    def delete_fact(self, fact_id: int) -> bool:
        def go(conn):
            srcs = [r[0] for r in conn.execute("SELECT source_id FROM fact_source WHERE user_id = %s AND fact_id = %s",
                                               (self.user_id, fact_id)).fetchall()]
            if not conn.execute("DELETE FROM fact WHERE user_id = %s AND id = %s RETURNING 1",
                                (self.user_id, fact_id)).fetchone():
                return False
            conn.execute("DELETE FROM source s WHERE s.user_id = %s AND s.id = ANY(%s) AND NOT EXISTS"
                         " (SELECT 1 FROM fact_source fs WHERE fs.user_id = s.user_id AND fs.source_id = s.id)",
                         (self.user_id, srcs))
            return True
        return self._write(go)
