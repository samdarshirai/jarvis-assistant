"""JSON shapes for the graph control API (user-scoped reads over a GraphStore). Unknown ids raise LookupError."""


from jarvis.kg.facts import norm
from jarvis.kg.predicates import LINK_TYPES, LINKS


class SelfEntity(Exception):
    """The self entity cannot be forgotten."""


class AmbiguousOther(Exception):
    """Several entities have the other person's name; `options` are what the user may pick from."""

    def __init__(self, options: list[dict]):
        super().__init__("ambiguous other")
        self.options = options


def fact_json(f) -> dict:
    iso = lambda d: d and d.isoformat()
    return {"id": f.id, "predicate": f.predicate, "value": f.value, "source": f.source_kind, "excerpt": f.excerpt,
            "observed_at": iso(f.observed_at), "recorded_at": iso(f.recorded_at), "status": f.status}


def _entity(store, entity_id: int):
    e = store.get_entity(entity_id)
    if e is None:
        raise LookupError("entity")
    return e


def _link_rows(store) -> list[tuple[int, int, str]]:
    """(subject, object, link type) of every active link, as stored."""
    with store.pool.connection() as conn:
        return conn.execute("SELECT subject_id, object_entity_id, predicate FROM fact WHERE user_id = %s AND status = 'active'"
                            " AND object_entity_id IS NOT NULL AND predicate = ANY(%s) ORDER BY id",
                            (store.user_id, list(LINKS))).fetchall()


def link_json(ln) -> dict:
    return {"fact_id": ln.fact_id, "type": ln.predicate, "label": ln.label, "other_id": ln.other_id,
            "other_name": ln.other_name, "other_kind": ln.other_kind, "outgoing": ln.outgoing}


def entity_list(store) -> dict:
    with store.pool.connection() as conn:
        counts = dict(conn.execute("SELECT subject_id, count(*) FROM fact WHERE user_id = %s AND status = 'active'"
                                   " GROUP BY subject_id", (store.user_id,)).fetchall())
        # a fact with an object entity is a link (below); only the text ones are matched to people by name
        facts = conn.execute("SELECT subject_id, predicate, object_value FROM fact WHERE user_id = %s"
                             " AND status = 'active' AND object_value IS NOT NULL AND object_entity_id IS NULL",
                             (store.user_id,)).fetchall()
    ents = store.list_entities()
    rows = [{"id": e.id, "name": e.display_name, "kind": e.kind, "fact_count": counts.get(e.id, 0)} for e in ents]
    links = _link_rows(store)
    by_name: dict[str, list[int]] = {}
    for e in ents:
        by_name.setdefault(norm(e.display_name), []).append(e.id)
    ids = {e.id for e in ents}
    rel = {(s, t, p) for s, p, v in facts if s in ids for t in by_name.get(norm(v), ()) if t != s}
    rel |= {(s, t, p) for s, t, p in links if s in ids and t in ids and s != t}
    # a kin word links the person to "me", labelled with the word itself ("wife"); deleted with the person's fact
    kin = [(s, v.strip()) for s, p, v in facts if p == "relation_to_user" and s in ids and v.strip()]
    if kin:
        me = next((e.id for e in ents if e.kind == "self"), None) or store.self_entity().id  # created on first use
        rows = rows if any(r["id"] == me for r in rows) else rows + [
            {"id": me, "name": "Me", "kind": "self", "fact_count": counts.get(me, 0)}]
        rel |= {(me, s, v) for s, v in kin if s != me}
    return {"entities": sorted(rows, key=lambda r: (r["name"].casefold(), r["id"])),
            "relations": [{"from": s, "to": t, "predicate": p} for s, t, p in sorted(rel)]}


def entity_detail(store, entity_id: int) -> dict:
    e = _entity(store, entity_id)
    every = [f for f in store.facts_for(e.id, history=True) if f.object_id is None]  # links are listed apart
    return {"id": e.id, "name": e.display_name, "kind": e.kind,
            "facts": [fact_json(f) for f in every if f.status == "active"],
            "links": [link_json(ln) for ln in store.links_for(e.id)],
            "history": [fact_json(f) for f in every if f.status != "active"],
            "pending": [fact_json(f) for f in store.pending_for(e.id)]}


def link_create(store, from_id: int, link_type: str, to_id: int | None = None, to_name: str | None = None) -> dict:
    """The user links two people by hand. The other person is an id, or a name: 'me' is the user, one match is used, none
    creates a new person, several raise AmbiguousOther. One transaction. Unknown entities raise LookupError; a self-link or
    an unknown type ValueError."""
    if link_type not in LINK_TYPES:
        raise ValueError("Unknown link type.")
    src = _entity(store, from_id)
    if (to_id is None) == (not to_name or not to_name.strip()):
        raise ValueError("Give either to_id or to_name.")
    other_id, name, kind = None, to_name, "person"
    if to_id is not None:
        other = _entity(store, to_id)
        other_id, name, kind = other.id, other.display_name, other.kind
    elif norm(to_name) in ("me", "i", "self", "myself"):
        me = store.self_entity()
        other_id, name, kind = me.id, me.display_name, "self"
    else:
        found = store.find_by_name(to_name)
        if len(found) > 1:
            raise AmbiguousOther([{"id": e.id, "name": e.display_name, "kind": e.kind} for e in found])
        if found:
            other_id, name, kind = found[0].id, found[0].display_name, found[0].kind
    (done,) = store.add_statements([{"entity_id": src.id, "name": src.display_name, "kind": src.kind, "relation": None,
                                     "facts": [], "links": [{"predicate": link_type, "entity_id": other_id,
                                                              "name": name, "kind": kind}]}])
    made = done["links"][0]
    return {"fact_id": made["fact"].id, "type": made["fact"].predicate,
            "other": {"id": made["other"].id, "name": made["other"].display_name, "created": made["other_created"]}}


def fact_correct(store, fact_id: int, value: str) -> dict:
    with store.pool.connection() as conn:
        old = store._fact(conn, fact_id)  # user-scoped lookup
    if old is None:
        raise LookupError("fact")
    if old.object_id is not None:
        raise ValueError("A link has no value to edit: delete it and add another.")
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
