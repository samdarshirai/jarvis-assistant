import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from jarvis.kg import forget
from jarvis.kg.facts import Fact, FactsMixin, Link, _clean, norm  # noqa: F401  (Fact, Link re-exported for callers)
from jarvis.kg.predicates import KINDS, PREDICATES

KEY_TYPES = ("email", "phone", "google_contact_id", "alias", "name")
ORIGINS = ("user", "extracted")


class Suppressed(Exception):
    """The user asked to forget this name or key; extracted data about it is dropped.
    The message is only "name" or "key", never the value, so logging it leaks nothing."""


def _norm_key(type: str, value: str) -> str:
    v = norm(value)
    if type == "phone":
        v = ("+" if v.startswith("+") else "") + re.sub(r"\D", "", v)
    return v


@dataclass(frozen=True)
class Entity:
    id: int
    kind: str
    display_name: str


def _ent(r) -> Entity:
    return Entity(r[0], r[1], r[2])


class GraphStore(FactsMixin):
    def __init__(self, pool, user_id: int, secret: bytes = b""):
        self.pool, self.user_id, self.secret = pool, user_id, secret
        self.version: int = 0

    def self_entity(self) -> Entity:
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO entity (user_id, kind, display_name) VALUES (%s, 'self', 'Me')"
                         " ON CONFLICT DO NOTHING", (self.user_id,))
            return _ent(conn.execute("SELECT id, kind, display_name FROM entity"
                                     " WHERE user_id = %s AND kind = 'self'", (self.user_id,)).fetchone())

    def create_entity(self, name: str, kind: str = "person", aliases: tuple[str, ...] = (), *,
                      origin: str = "user") -> Entity:
        with self.pool.connection() as conn:
            return self._create_entity(conn, name, kind, aliases, origin)

    def _create_entity(self, conn, name: str, kind: str, aliases: tuple[str, ...], origin: str) -> Entity:
        """create_entity in the caller's transaction."""
        full = norm(name)
        if not full:
            raise ValueError("Entity name is blank.")
        if kind not in KINDS:
            raise ValueError(f"Unknown entity kind: {kind!r}.")
        if origin not in ORIGINS:
            raise ValueError(f"Unknown origin: {origin!r}.")
        keys = {("name", full), *(("name", t) for t in full.split())}
        keys |= {("alias", a) for a in map(norm, aliases) if a}
        named = [full, *(v for t, v in keys if t == "alias")]
        if origin == "extracted":
            if self._suppressed(conn, "name", named):
                raise Suppressed("name")
        elif self.secret:  # the user's own statement brings the name back
            conn.execute("DELETE FROM suppression WHERE user_id = %s AND kind = 'name' AND key_hmac = ANY(%s)",
                         (self.user_id, forget.hmacs(self, conn, named)))
        r = conn.execute("INSERT INTO entity (user_id, kind, display_name) VALUES (%s, %s, %s)"
                         " RETURNING id, kind, display_name", (self.user_id, kind, " ".join(name.split()))).fetchone()
        for t, v in keys:
            conn.execute("INSERT INTO entity_key (user_id, entity_id, type, value_norm) VALUES (%s, %s, %s, %s)"
                         " ON CONFLICT DO NOTHING", (self.user_id, r[0], t, v))
        return _ent(r)

    def add_link(self, subject_id: int, predicate: str, object_id: int, *, source_kind: str, ref: str | None = None,
                 excerpt: str = "", observed_at: datetime | None = None) -> Fact:
        """Link two existing entities (see kg.predicates.LINKS). ValueError for an unknown type or entity, or a self-link."""
        obs = observed_at or datetime.now(timezone.utc)
        if obs.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware.")
        return self._write(lambda conn: self._add_link(conn, subject_id, predicate, object_id, source_kind,
                                                       ref or f"user:{uuid.uuid4()}", self._redact(conn, excerpt[:200]), obs))

    def add_statements(self, statements: list[dict]) -> list[dict]:
        """Save several user statements in ONE transaction: new entities, their relation to the user, facts, and links.
        Each statement is {entity_id (None = create from name/kind), name, kind, relation, facts: [(predicate, value)],
        links: [{predicate, entity_id (None = create from name/kind), name, kind}] (optional)}.
        Any failure rolls everything back, new entities included. Returns one {entity, created, facts, links} per
        statement; a link is {fact, other, other_created}."""
        def go(conn):
            made: dict[str, Entity] = {}  # new entities of this call, so a repeated name reuses them
            out = []
            for st in statements:
                created, relation = False, st.get("relation")
                if st["entity_id"] is not None:
                    ent = Entity(st["entity_id"], st.get("kind", "person"), st["name"])
                elif norm(st["name"]) in made:
                    ent = made[norm(st["name"])]
                else:
                    ent = self._create_entity(conn, st["name"], st.get("kind", "person"),
                                              (relation,) if relation else (), "user")
                    made[norm(st["name"])] = ent
                    created = True
                saved = []
                pairs = list(st["facts"])
                if relation and not conn.execute(
                        "SELECT 1 FROM fact WHERE user_id = %s AND subject_id = %s AND predicate = 'relation_to_user'"
                        " AND status = 'active'", (self.user_id, ent.id)).fetchone():
                    pairs.insert(0, ("relation_to_user", relation))
                    rel_only = True
                else:
                    rel_only = False
                for i, (predicate, value) in enumerate(pairs):
                    if predicate not in PREDICATES:
                        raise ValueError(f"Unknown predicate: {predicate!r}.")
                    value = _clean(value)
                    f = self._add(conn, ent.id, predicate, value, "user_stated", f"user:{uuid.uuid4()}",
                                  self._redact(conn, value[:200]), datetime.now(timezone.utc))
                    if not (rel_only and i == 0):  # the relation is bookkeeping, not a stated fact
                        saved.append(f)
                made_links = []
                for ln in st.get("links") or ():
                    if ln.get("entity_id") is not None:
                        other, other_created = Entity(ln["entity_id"], ln.get("kind", "person"), ln["name"]), False
                    elif norm(ln["name"]) in made:
                        other, other_created = made[norm(ln["name"])], False
                    else:
                        other = self._create_entity(conn, ln["name"], ln.get("kind", "person"), (), "user")
                        made[norm(ln["name"])], other_created = other, True
                    f = self._add_link(conn, ent.id, ln["predicate"], other.id, "user_stated", f"user:{uuid.uuid4()}",
                                       self._redact(conn, f"{ent.display_name} {ln['predicate']} {other.display_name}"[:200]),
                                       datetime.now(timezone.utc))
                    made_links.append({"fact": f, "other": other, "other_created": other_created})
                out.append({"entity": ent, "created": created, "facts": saved, "links": made_links})
            return out
        return self._write(go)

    def find_by_name(self, name: str, kind: str | None = None) -> list[Entity]:
        q = norm(name)
        if not q:
            return []
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT e.id, e.kind, e.display_name FROM entity e"
                " JOIN entity_key k ON k.user_id = e.user_id AND k.entity_id = e.id"
                " WHERE e.user_id = %s AND k.type IN ('name', 'alias') AND k.value_norm = %s"
                " AND e.merged_into IS NULL AND (%s::text IS NULL OR e.kind = %s) ORDER BY e.id",
                (self.user_id, q, kind, kind)).fetchall()
        return [_ent(r) for r in rows]

    def add_key(self, entity_id: int, type: str, value: str, *, origin: str = "user") -> None:
        if type not in KEY_TYPES:
            raise ValueError(f"Unknown key type: {type!r}.")
        if origin not in ORIGINS:
            raise ValueError(f"Unknown origin: {origin!r}.")
        v = _norm_key(type, value)
        if not v:
            raise ValueError("Key value is blank.")
        with self.pool.connection() as conn:
            if origin == "extracted" and self._suppressed(conn, type, [v]):
                raise Suppressed("key")
            # serialise holders of the same key so each sees the other's row when flagging
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                         (f"{self.user_id}\x1f{type}\x1f{v}",))
            if conn.execute("INSERT INTO entity_key (user_id, entity_id, type, value_norm) VALUES (%s, %s, %s, %s)"
                            " ON CONFLICT DO NOTHING RETURNING 1", (self.user_id, entity_id, type, v)).fetchone():
                conn.execute("UPDATE entity_key SET shared = true WHERE user_id = %s AND type = %s AND value_norm = %s"
                             " AND (SELECT count(*) FROM entity_key WHERE user_id = %s AND type = %s AND value_norm = %s) > 1",
                             (self.user_id, type, v) * 2)

    def get_entity(self, entity_id: int) -> Entity | None:
        with self.pool.connection() as conn:
            r = conn.execute("SELECT id, kind, display_name FROM entity WHERE user_id = %s AND id = %s",
                             (self.user_id, entity_id)).fetchone()
        return _ent(r) if r else None

    def list_entities(self) -> list[Entity]:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT id, kind, display_name FROM entity WHERE user_id = %s AND merged_into IS NULL"
                                " ORDER BY id", (self.user_id,)).fetchall()
        return [_ent(r) for r in rows]

    def _suppressed(self, conn, type: str, values: list[str]) -> bool:
        """Names and aliases match any name suppression; hard keys match their own type."""
        hs = forget.hmacs(self, conn, values)
        if hs is None:
            return False
        if type in ("name", "alias"):
            q = "SELECT 1 FROM suppression WHERE user_id = %s AND kind = 'name' AND key_hmac = ANY(%s) LIMIT 1"
            return conn.execute(q, (self.user_id, hs)).fetchone() is not None
        q = "SELECT 1 FROM suppression WHERE user_id = %s AND key_type = %s AND key_hmac = ANY(%s) LIMIT 1"
        return conn.execute(q, (self.user_id, type, hs)).fetchone() is not None

    def _redact(self, conn, excerpt: str) -> str:
        return forget.redact(self, conn, excerpt)

    def is_suppressed(self, type: str, value: str) -> bool:
        with self.pool.connection() as conn:
            return self._suppressed(conn, type, [_norm_key(type, value)])

    def mentions_suppressed(self, *texts: str) -> bool:
        """True if any text mentions a name or key the user asked to forget (same matcher as excerpt redaction)."""
        words = sorted(set().union(*(forget.grams(t) for t in texts)))
        with self.pool.connection() as conn:
            hs = forget.hmacs(self, conn, words) if words else None
            return bool(hs) and conn.execute("SELECT 1 FROM suppression WHERE user_id = %s AND key_hmac = ANY(%s) LIMIT 1",
                                             (self.user_id, hs)).fetchone() is not None

    def list_suppressions(self) -> list[dict]:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT kind, key_type, key_hmac, created_at FROM suppression WHERE user_id = %s"
                                " ORDER BY created_at, key_type, key_hmac", (self.user_id,)).fetchall()
        return [dict(zip(("kind", "key_type", "key_hmac", "created_at"), r)) for r in rows]

    def lift_suppression(self, key_type: str, key_hmac: str) -> bool:
        with self.pool.connection() as conn:
            return conn.execute("DELETE FROM suppression WHERE user_id = %s AND key_type = %s AND key_hmac = %s"
                                " RETURNING 1", (self.user_id, key_type, key_hmac)).fetchone() is not None

    def preview_forget(self, entity_id: int, *, include_mentions: bool = True) -> dict:
        return forget.preview_forget(self, entity_id, include_mentions=include_mentions)

    def forget_entity(self, entity_id: int, *, include_mentions: bool = True) -> dict:
        return forget.forget_entity(self, entity_id, include_mentions=include_mentions)
