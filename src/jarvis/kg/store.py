import re
from dataclasses import dataclass

from jarvis.kg import forget
from jarvis.kg.facts import Fact, FactsMixin, norm  # noqa: F401  (Fact re-exported for callers)
from jarvis.kg.predicates import KINDS

KEY_TYPES = ("email", "phone", "google_contact_id", "alias", "name")
ORIGINS = ("user", "extracted")


class Suppressed(Exception):
    """The user asked to forget this name or key; extracted data about it is dropped."""


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
        with self.pool.connection() as conn:
            if origin == "extracted":
                if self._suppressed(conn, "name", named):
                    raise Suppressed(name)
            elif self.secret:  # the user's own statement brings the name back
                conn.execute("DELETE FROM suppression WHERE user_id = %s AND kind = 'name' AND key_hmac = ANY(%s)",
                             (self.user_id, forget.hmacs(self, conn, named)))
            r = conn.execute("INSERT INTO entity (user_id, kind, display_name) VALUES (%s, %s, %s)"
                             " RETURNING id, kind, display_name", (self.user_id, kind, " ".join(name.split()))).fetchone()
            for t, v in keys:
                conn.execute("INSERT INTO entity_key (user_id, entity_id, type, value_norm) VALUES (%s, %s, %s, %s)"
                             " ON CONFLICT DO NOTHING", (self.user_id, r[0], t, v))
        return _ent(r)

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
                raise Suppressed(type)
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
