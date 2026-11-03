import logging

from jarvis.kg.facts import norm

log = logging.getLogger(__name__)


def link_existing(pool) -> int:
    """Turn `friend_of` text facts into links where the name is exactly one other person: the fact keeps its value and
    gets `object_entity_id`. Ambiguous or unknown names, and friendships already linked (from either side), are left
    alone. Idempotent: only facts without an object are looked at. Returns how many were linked. Names never logged."""
    n = 0
    with pool.connection() as conn:
        users = [r[0] for r in conn.execute("SELECT DISTINCT user_id FROM fact WHERE predicate = 'friend_of'"
                                            " AND status = 'active' AND object_entity_id IS NULL").fetchall()]
    for uid in users:
        with pool.connection() as conn:  # one transaction per user
            rows = conn.execute("SELECT id, subject_id, object_value FROM fact WHERE user_id = %s AND predicate = 'friend_of'"
                                " AND status = 'active' AND object_entity_id IS NULL AND object_value IS NOT NULL ORDER BY id",
                                (uid,)).fetchall()
            for fid, sid, value in rows:
                ids = [r[0] for r in conn.execute(
                    "SELECT DISTINCT e.id FROM entity e JOIN entity_key k ON k.user_id = e.user_id AND k.entity_id = e.id"
                    " WHERE e.user_id = %s AND k.type IN ('name', 'alias') AND k.value_norm = %s AND e.merged_into IS NULL"
                    " AND e.kind <> 'self'", (uid, norm(value))).fetchall()]
                if len(ids) != 1 or ids[0] == sid:
                    continue
                if conn.execute(
                        "SELECT 1 FROM fact WHERE user_id = %s AND predicate = 'friend_of' AND status = 'active'"
                        " AND ((subject_id = %s AND object_entity_id = %s) OR (subject_id = %s AND object_entity_id = %s))",
                        (uid, sid, ids[0], ids[0], sid)).fetchone():
                    continue
                conn.execute("UPDATE fact SET object_entity_id = %s WHERE user_id = %s AND id = %s", (ids[0], uid, fid))
                n += 1
    return n


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
