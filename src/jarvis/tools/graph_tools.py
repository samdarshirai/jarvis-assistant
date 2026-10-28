from typing import Literal

from pydantic import BaseModel, Field

from jarvis.kg.predicates import PREDICATES
from jarvis.kg.store import GraphStore
from jarvis.tools.registry import Registry, Tool

_SELF_WORDS = {"me", "i", "self", "myself"}


class RememberFactArgs(BaseModel):
    subject: str = Field(description="Who the fact is about: a name, a kin word like 'sister', or 'me'")
    predicate: Literal[tuple(sorted(PREDICATES))]  # type: ignore[valid-type]
    value: str = Field(min_length=1, max_length=200)
    relation: str | None = Field(default=None, description="How the subject relates to the user, e.g. 'sister'; pass when introducing someone")
    kind: Literal["person", "place", "org", "thing"] = "person"


class LookupArgs(BaseModel):
    name: str = Field(description="A name, a kin word like 'sister', or 'me'")


def register_graph_tools(registry: Registry, store: GraphStore) -> None:
    def _has_relation(entity_id):
        return any(f.predicate == "relation_to_user" for f in store.facts_for(entity_id))

    def remember_fact(subject, predicate, value, relation=None, kind="person"):
        if subject.strip().lower() in _SELF_WORDS:
            ent = store.self_entity()
        else:
            found = store.find_by_name(subject)
            if len(found) > 1:
                return {"error": "ambiguous", "candidates": [{"id": e.id, "name": e.display_name} for e in found]}
            ent = found[0] if found else store.create_entity(subject, kind=kind, aliases=(relation,) if relation else ())
            if relation and not _has_relation(ent.id):
                store.add_fact(ent.id, "relation_to_user", relation, source_kind="user_stated", excerpt=relation)
        f = store.add_fact(ent.id, predicate, value, source_kind="user_stated", excerpt=value[:200])
        return {"remembered": {"subject": ent.display_name, "predicate": predicate, "value": f.value, "fact_id": f.id}}

    def _facts(fs):
        return [{"predicate": f.predicate, "value": f.value, "source": f.source_kind,
                 "observed_at": f.observed_at.isoformat() if f.observed_at else None} for f in fs]

    def graph_lookup(name):
        ents = [store.self_entity()] if name.strip().lower() in _SELF_WORDS else store.find_by_name(name)
        return [{"id": e.id, "name": e.display_name, "kind": e.kind,
                 "facts": _facts(store.facts_for(e.id)), "pending": _facts(store.pending_for(e.id))}
                for e in ents[:8]]

    registry.add(Tool(
        name="remember_fact", domain="memory", args_schema=RememberFactArgs, fn=remember_fact,
        description="Save a structured fact about the user or someone in their life (employer, city, birthday, relation, ...).",
        needs_confirm=True, refuse_after_untrusted=True,
        describe=lambda a: f"Remember: {a['subject']} — {a['predicate']}: {a['value']}",
        done=lambda r: "Got it, I'll remember that."))
    registry.add(Tool(
        name="graph_lookup", domain="memory", args_schema=LookupArgs, fn=graph_lookup,
        description="Look up what is known about a person, place or organisation (or 'me'), with sources and times.",
        needs_confirm=False))
