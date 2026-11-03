"""The user's decisions on extractor candidates. Approving writes to the graph and marks the candidate in ONE transaction;
a subject that several entities share asks which one and writes nothing. Every query is scoped by user_id."""
from datetime import datetime, timezone

from jarvis.kg.candidates import CandidateStore
from jarvis.kg.facts import _clean, norm
from jarvis.kg.views import fact_json


class Ambiguous(Exception):
    """Several entities share the subject's name; `options` are the entities the user may pick from."""

    def __init__(self, options: list[dict]):
        super().__init__("ambiguous subject")
        self.options = options


def candidates(store) -> dict:
    return {"groups": CandidateStore(store.pool, store.user_id).list_new()}


def count(store) -> dict:
    return {"count": CandidateStore(store.pool, store.user_id).count_new()}


def dismiss(store, cand_id: int) -> dict:
    if not CandidateStore(store.pool, store.user_id).decide([cand_id], "dismissed", datetime.now(timezone.utc)):
        raise LookupError("candidate")
    return {"ok": True}


def approve(store, cand_id: int, *, subject: str | None = None, value: str | None = None,
            relation: str | None = None, entity_id: int | None = None, new: bool = False) -> dict:
    """Write the candidate into the graph as a user-stated fact (quote and conversation as provenance).
    Edits replace subject, value or relation. `entity_id` picks one of several same-named entities; `new` forces a new
    one. Returns {"entity", "created", "fact_ids", "fact"}, or {"choice": [...]} when the subject is ambiguous.
    Unknown, foreign or already-decided candidates raise LookupError."""
    now = datetime.now(timezone.utc)

    def go(conn):
        r = conn.execute(
            "SELECT conversation_id, subject_name, subject_kind, relation, predicate, value, quote FROM fact_candidate"
            " WHERE user_id = %s AND id = %s AND status = 'new' FOR UPDATE", (store.user_id, cand_id)).fetchone()
        if r is None:
            raise LookupError("candidate")
        conv_id, name, kind, rel, predicate, val, quote = r
        name = " ".join(subject.split()) if subject and subject.strip() else name
        val = _clean(value) if value is not None else val
        rel = (" ".join(relation.split()) or None) if relation is not None else rel
        created, ref, excerpt = False, f"conversation:{conv_id}", store._redact(conn, quote[:200])
        if kind == "self":
            conn.execute("INSERT INTO entity (user_id, kind, display_name) VALUES (%s, 'self', 'Me')"
                         " ON CONFLICT DO NOTHING", (store.user_id,))
            ent = conn.execute("SELECT id, display_name FROM entity WHERE user_id = %s AND kind = 'self'",
                               (store.user_id,)).fetchone()
        elif entity_id is not None:
            ent = conn.execute("SELECT id, display_name FROM entity WHERE user_id = %s AND id = %s AND kind <> 'self'"
                               " AND merged_into IS NULL", (store.user_id, entity_id)).fetchone()
            if ent is None:
                raise LookupError("entity")
        else:
            matches = [] if new else conn.execute(
                "SELECT DISTINCT e.id, e.display_name, e.kind FROM entity e JOIN entity_key k"
                " ON k.user_id = e.user_id AND k.entity_id = e.id WHERE e.user_id = %s AND k.type IN ('name', 'alias')"
                " AND k.value_norm = %s AND e.kind = %s AND e.merged_into IS NULL ORDER BY e.id",
                (store.user_id, norm(name), kind)).fetchall()
            if len(matches) > 1:
                raise Ambiguous([{"id": m[0], "name": m[1], "kind": m[2]} for m in matches])
            if matches:
                ent = matches[0][:2]
            else:
                e = store._create_entity(conn, name, kind, (rel,) if rel else (), "user")
                ent, created = (e.id, e.display_name), True
        ids = []
        if rel and kind != "self" and not conn.execute(
                "SELECT 1 FROM fact WHERE user_id = %s AND subject_id = %s AND predicate = 'relation_to_user'"
                " AND status = 'active'", (store.user_id, ent[0])).fetchone():
            ids.append(store._add(conn, ent[0], "relation_to_user", rel, "user_stated", ref, excerpt, now).id)
        fact = store._add(conn, ent[0], predicate, val, "user_stated", ref, excerpt, now)
        ids.append(fact.id)
        conn.execute("UPDATE fact_candidate SET status = 'approved', decided_at = %s WHERE user_id = %s AND id = %s",
                     (now, store.user_id, cand_id))
        return {"entity": {"id": ent[0], "name": ent[1]}, "created": created, "fact_ids": ids, "fact": fact_json(fact)}

    try:
        return store._write(go)
    except Ambiguous as a:
        return {"choice": a.options}


def bulk(store, ids: list[int], decision: str) -> dict:
    """Group buttons. Dismiss is one statement; approve runs one transaction per candidate so a failure (or an
    ambiguous subject, which needs the user's pick) never blocks the rest. Returns what happened to each id."""
    if decision == "dismiss":
        done = CandidateStore(store.pool, store.user_id).decide(ids, "dismissed", datetime.now(timezone.utc))
        return {"dismissed": done, "skipped": [i for i in ids if i not in done]}
    approved, choice, skipped = [], [], []
    for i in dict.fromkeys(ids):
        try:
            out = approve(store, i)
        except (LookupError, ValueError):
            skipped.append(i)
            continue
        if "choice" in out:
            choice.append(i)
        else:
            approved.append({"id": i, "entity": out["entity"], "fact_ids": out["fact_ids"], "created": out["created"]})
    return {"approved": approved, "needs_choice": choice, "skipped": skipped}
