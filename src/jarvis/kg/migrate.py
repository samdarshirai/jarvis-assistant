import logging

log = logging.getLogger(__name__)


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
        try:
            kg.add_fact(kg.self_entity().id, "note", text, source_kind="user_stated", ref=f"memory:{mid}",
                        excerpt=text[:200], observed_at=created)
        except Exception as e:  # one bad row must not stop the rest or the boot; never log its text
            log.warning("memory %s not migrated: %s", mid, type(e).__name__)
            continue
        n += 1
    return n
