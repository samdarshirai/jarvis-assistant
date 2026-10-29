"""JSON shapes for the graph control API (user-scoped reads over a GraphStore). Unknown ids raise LookupError."""


from jarvis.kg.facts import norm


class SelfEntity(Exception):
    """The self entity cannot be forgotten."""


def fact_json(f) -> dict:
    iso = lambda d: d and d.isoformat()
    return {"id": f.id, "predicate": f.predicate, "value": f.value, "source": f.source_kind, "excerpt": f.excerpt,
            "observed_at": iso(f.observed_at), "recorded_at": iso(f.recorded_at), "status": f.status}


def _entity(store, entity_id: int):
    e = store.get_entity(entity_id)
    if e is None:
        raise LookupError("entity")
    return e


def entity_list(store) -> dict:
    with store.pool.connection() as conn:
        counts = dict(conn.execute("SELECT subject_id, count(*) FROM fact WHERE user_id = %s AND status = 'active'"
                                   " GROUP BY subject_id", (store.user_id,)).fetchall())
        facts = conn.execute("SELECT subject_id, predicate, object_value FROM fact WHERE user_id = %s"
                             " AND status = 'active' AND object_value IS NOT NULL", (store.user_id,)).fetchall()
    ents = store.list_entities()
    rows = [{"id": e.id, "name": e.display_name, "kind": e.kind, "fact_count": counts.get(e.id, 0)} for e in ents]
    by_name: dict[str, list[int]] = {}
    for e in ents:
        by_name.setdefault(norm(e.display_name), []).append(e.id)
    ids = {e.id for e in ents}
    rel = {(s, t, p) for s, p, v in facts if s in ids for t in by_name.get(norm(v), ()) if t != s}
    return {"entities": sorted(rows, key=lambda r: (r["name"].casefold(), r["id"])),
            "relations": [{"from": s, "to": t, "predicate": p} for s, t, p in sorted(rel)]}


def entity_detail(store, entity_id: int) -> dict:
    e = _entity(store, entity_id)
    every = store.facts_for(e.id, history=True)
    return {"id": e.id, "name": e.display_name, "kind": e.kind,
            "facts": [fact_json(f) for f in every if f.status == "active"],
            "history": [fact_json(f) for f in every if f.status != "active"],
            "pending": [fact_json(f) for f in store.pending_for(e.id)]}


def fact_correct(store, fact_id: int, value: str) -> dict:
    with store.pool.connection() as conn:
        if store._fact(conn, fact_id) is None:  # user-scoped lookup
            raise LookupError("fact")
    return fact_json(store.correct_fact(fact_id, value))


def fact_delete(store, fact_id: int) -> dict:
    if not store.delete_fact(fact_id):
        raise LookupError("fact")
    return {"ok": True}


def forget_preview(store, entity_id: int, mentions: bool = True) -> dict:
    if _entity(store, entity_id).kind == "self":
        raise SelfEntity
    return store.preview_forget(entity_id, include_mentions=mentions)


def forget(store, entity_id: int, mentions: bool) -> dict:
    if _entity(store, entity_id).kind == "self":
        raise SelfEntity
    return store.forget_entity(entity_id, include_mentions=mentions)


def suppressions(store) -> dict:
    return {"suppressions": [{**s, "created_at": s["created_at"].isoformat()} for s in store.list_suppressions()]}


def lift(store, key_type: str, key_hmac: str) -> dict:
    if not store.lift_suppression(key_type, key_hmac):
        raise LookupError("suppression")
    return {"ok": True}
