from jarvis.db import like_pattern
from jarvis.memory import MAX_TEXT


def _row(r) -> dict:
    return {"id": r[0], "text": r[1]}


_NOTES = ("FROM fact f JOIN entity e ON e.user_id = f.user_id AND e.id = f.subject_id"
          " WHERE f.user_id = %s AND e.kind = 'self' AND f.predicate = 'note' AND f.status = 'active'")


class GraphMemoryStore:
    """MemoryStore's interface over `note` facts on the user's self entity; shares the GraphStore so its writes bump version."""

    def __init__(self, store):
        self.store = store
        self.pool, self.user_id = store.pool, store.user_id

    def add(self, text: str) -> dict:
        text = " ".join(text.split())
        if not text:
            return {"error": "Nothing to remember: the text is empty."}
        if len(text) > MAX_TEXT:
            return {"error": f"Too long for a memory ({len(text)} chars, max {MAX_TEXT}). "
                             "Shorten it, or save a note instead."}
        f = self.store.add_fact(self.store.self_entity().id, "note", text, source_kind="user_stated")
        return {"id": f.id, "text": f.value}

    def _q(self, tail: str, *args) -> list[dict]:
        with self.pool.connection() as conn:
            return [_row(r) for r in conn.execute(f"SELECT f.id, f.object_value {_NOTES}{tail}", (self.user_id, *args)).fetchall()]

    def get(self, memory_id: int) -> dict | None:
        return next(iter(self._q(" AND f.id = %s", memory_id)), None)

    def remove(self, memory_id: int) -> bool:
        if self.get(memory_id) is None:
            return False
        return self.store.delete_fact(memory_id)

    def all(self) -> list[dict]:
        return self._q(" ORDER BY f.id")

    def search(self, query: str) -> list[dict]:
        return self._q(" AND f.object_value ILIKE %s ORDER BY f.id", like_pattern(query))


def migrate_memories(pool, secret: bytes = b"") -> int:
    """Copy legacy `memories` rows into note facts; a source ref already present means done. Returns newly migrated count."""
    from jarvis.kg.store import GraphStore
    with pool.connection() as conn:
        rows = conn.execute("SELECT id, user_id, text, created_at FROM memories WHERE user_id IS NOT NULL ORDER BY id").fetchall()
    stores, n = {}, 0
    for mid, uid, text, created in rows:
        kg = stores.setdefault(uid, GraphStore(pool, uid, secret))
        with pool.connection() as conn:
            if conn.execute("SELECT 1 FROM source WHERE user_id = %s AND kind = 'user_stated' AND ref = %s",
                            (uid, f"memory:{mid}")).fetchone():
                continue
        kg.add_fact(kg.self_entity().id, "note", text, source_kind="user_stated", ref=f"memory:{mid}",
                    excerpt=text[:200], observed_at=created)
        n += 1
    return n
