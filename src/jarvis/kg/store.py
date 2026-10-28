import re
import unicodedata
from dataclasses import dataclass

from jarvis.kg.predicates import KINDS

KEY_TYPES = ("email", "phone", "google_contact_id", "alias", "name")


def norm(text: str) -> str:
    """NFKC, casefold, collapse whitespace, strip."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


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


class GraphStore:
    def __init__(self, pool, user_id: int, secret: bytes = b""):
        self.pool, self.user_id, self.secret = pool, user_id, secret
        self.version: int = 0

    def self_entity(self) -> Entity:
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO entity (user_id, kind, display_name) VALUES (%s, 'self', 'Me')"
                         " ON CONFLICT DO NOTHING", (self.user_id,))
            return _ent(conn.execute("SELECT id, kind, display_name FROM entity"
                                     " WHERE user_id = %s AND kind = 'self'", (self.user_id,)).fetchone())

    def create_entity(self, name: str, kind: str = "person", aliases: tuple[str, ...] = ()) -> Entity:
        full = norm(name)
        if not full:
            raise ValueError("Entity name is blank.")
        if kind not in KINDS:
            raise ValueError(f"Unknown entity kind: {kind!r}.")
        keys = {("name", full), *(("name", t) for t in full.split())}
        keys |= {("alias", a) for a in map(norm, aliases) if a}
        with self.pool.connection() as conn:
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

    def add_key(self, entity_id: int, type: str, value: str) -> None:
        if type not in KEY_TYPES:
            raise ValueError(f"Unknown key type: {type!r}.")
        v = _norm_key(type, value)
        if not v:
            raise ValueError("Key value is blank.")
        with self.pool.connection() as conn:
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
