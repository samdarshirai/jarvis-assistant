from typing import Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator
from typing import Annotated

from jarvis.kg.predicates import LINK_TYPES, PREDICATES, label_for
from jarvis.kg.store import GraphStore
from jarvis.tools.registry import Registry, Tool

_SELF_WORDS = {"me", "i", "self", "myself"}


class RememberFactArgs(BaseModel):
    subject: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = Field(
        description="Who the fact is about: a name, a kin word like 'sister', or 'me'")
    predicate: Literal[tuple(sorted(PREDICATES))]  # type: ignore[valid-type]
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    relation: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)] | None = Field(default=None, description="How the subject relates to the user, e.g. 'sister'; pass when introducing someone")
    kind: Literal["person", "place", "org", "thing"] = "person"


MAX_STATEMENTS, MAX_FACTS = 8, 10


class FactItem(BaseModel):
    predicate: Literal[tuple(sorted(PREDICATES))]  # type: ignore[valid-type]
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class LinkItem(BaseModel):
    type: Literal[LINK_TYPES]  # type: ignore[valid-type]
    other: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = Field(
        description="The other person: a name, a kin word like 'wife', or 'me'")
    other_kind: Literal["person", "place", "org", "thing"] = "person"


class StatementArgs(BaseModel):
    subject: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = Field(
        description="Who the facts are about: a name, a kin word like 'sister', or 'me'")
    kind: Literal["person", "place", "org", "thing"] = "person"
    relation: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)] | None = Field(
        default=None, description="How the subject relates to the user, e.g. 'wife'; pass when introducing someone")
    facts: list[FactItem] = Field(default_factory=list, max_length=MAX_FACTS)
    links: list[LinkItem] = Field(
        default_factory=list, max_length=MAX_FACTS,
        description="Relationships between this subject and another person, e.g. {type: spouse_of, other: Raj}. "
                    "Use for people related to each other; how someone relates to the user is `relation`")

    @model_validator(mode="after")
    def _something_to_save(self):
        if not self.facts and not self.links:
            raise ValueError("A statement needs at least one fact or link.")
        return self


class RememberFactsArgs(BaseModel):
    statements: list[StatementArgs] = Field(
        min_length=1, max_length=MAX_STATEMENTS,
        description="One entry per person, place or organisation mentioned in the user's message")


class LookupArgs(BaseModel):
    name: str = Field(description="A name, a kin word like 'sister', or 'me'")


class ForgetEntityArgs(BaseModel):
    entity_id: int = Field(description="The id of the person, place or organisation to forget, from graph_lookup")


def register_graph_tools(registry: Registry, store: GraphStore) -> None:
    def _has_relation(entity_id):
        return any(f.predicate == "relation_to_user" for f in store.facts_for(entity_id))

    def _resolve(subject):
        """('self', ent) | ('one', ent) | ('none', None) | ('many', [ents])  (read-only)."""
        if subject.strip().lower() in _SELF_WORDS:
            return "self", store.self_entity()
        found = store.find_by_name(subject)
        return ("many", found) if len(found) > 1 else ("one", found[0]) if found else ("none", None)

    def remember_facts(statements):
        plan = []
        for st in statements:
            how, ent = _resolve(st["subject"])
            if how == "many":
                return {"error": "ambiguous", "subject": st["subject"],
                        "candidates": [{"id": e.id, "name": e.display_name} for e in ent]}
            links = []
            for ln in st.get("links") or ():
                ohow, other = _resolve(ln["other"])
                if ohow == "many":  # never guess which Raj: nothing is saved until the user says
                    return {"error": "ambiguous_other", "other": ln["other"],
                            "candidates": [{"id": e.id, "name": e.display_name} for e in other]}
                links.append({"predicate": ln["type"], "entity_id": other.id if other else None,
                              "name": other.display_name if other else ln["other"],
                              "kind": other.kind if other else ln.get("other_kind", "person")})
            plan.append({"entity_id": ent.id if ent else None, "name": ent.display_name if ent else st["subject"],
                         "kind": ent.kind if ent else st.get("kind", "person"),
                         "relation": None if how == "self" else st.get("relation"),
                         "facts": [(f["predicate"], f["value"]) for f in st.get("facts") or ()], "links": links})
        done = store.add_statements(plan)
        return {"remembered": [{"subject": d["entity"].display_name, "created": d["created"],
                                "facts": [{"predicate": f.predicate, "value": f.value, "fact_id": f.id}
                                          for f in d["facts"]],
                                "links": [{"link": label_for(ln["fact"].predicate), "other": ln["other"].display_name,
                                           "other_created": ln["other_created"], "fact_id": ln["fact"].id}
                                          for ln in d["links"]]} for d in done]}

    def remember_facts_describe(a):
        parts = []
        for st in a["statements"]:
            who = st["subject"] + (f" ({st['relation']})" if st.get("relation") else "")
            bits = [f"{f['predicate']}: {f['value']}" for f in st.get("facts") or ()]
            for ln in st.get("links") or ():
                new = " (new)" if ln["other"].strip().lower() not in _SELF_WORDS and not store.find_by_name(ln["other"]) else ""
                bits.append(f"{label_for(ln['type'])} {ln['other']}{new}")
            parts.append(f"{who} — " + "; ".join(bits))
        return "Remember: " + " | ".join(parts)

    def _save(ent, predicate, value):
        f = store.add_fact(ent.id, predicate, value, source_kind="user_stated", excerpt=value)
        return {"remembered": {"subject": ent.display_name, "predicate": predicate, "value": f.value, "fact_id": f.id}}

    def remember_fact(subject, predicate, value, relation=None, kind="person"):
        if subject.strip().lower() in _SELF_WORDS:
            ent = store.self_entity()
        else:
            found = store.find_by_name(subject)
            if len(found) > 1:
                return {"error": "ambiguous", "candidates": [{"id": e.id, "name": e.display_name} for e in found]}
            if found:
                ent = found[0]
                if relation and not _has_relation(ent.id):
                    store.add_fact(ent.id, "relation_to_user", relation, source_kind="user_stated", excerpt=relation)
            else:
                ent = store.create_entity(subject, kind=kind, aliases=(relation,) if relation else ())
                try:
                    if relation:
                        store.add_fact(ent.id, "relation_to_user", relation, source_kind="user_stated", excerpt=relation)
                    return _save(ent, predicate, value)
                except Exception:  # no half-created entity; facts cascade
                    with store.pool.connection() as conn:
                        conn.execute("DELETE FROM entity WHERE user_id = %s AND id = %s", (store.user_id, ent.id))
                    raise
        return _save(ent, predicate, value)

    def _facts(fs):
        return [{"predicate": f.predicate, "value": f.value, "source": f.source_kind,
                 "observed_at": f.observed_at.isoformat() if f.observed_at else None} for f in fs]

    def _links(e):
        return [{"relation": ln.label, "other": ln.other_name, "other_id": ln.other_id} for ln in store.links_for(e.id)]

    def graph_lookup(name):
        ents = [store.self_entity()] if name.strip().lower() in _SELF_WORDS else store.find_by_name(name)
        return [{"id": e.id, "name": e.display_name, "kind": e.kind, "facts": _facts(store.facts_for(e.id)),
                 "links": _links(e), "pending": _facts(store.pending_for(e.id))}
                for e in ents[:8]]

    def _plural(n, word):
        return f"{n} {word}" + ("" if n == 1 else "s")

    def forget_describe(a):
        try:
            c = store.preview_forget(a["entity_id"])
        except ValueError:
            return "Forget this person. I could not find them, so nothing will change."
        listed = c["mention_facts"][:5]
        more = c["mentions_deleted"] - len(listed)
        shown = (": " + "; ".join(f'"{m["value"]}"' for m in listed) + (f" and {more} more" if more else "")) if listed else ""
        return (f"Forget everything about {c['entity']}: {_plural(c['facts'], 'fact')}, "
                f"{_plural(c['merged_entities'], 'merged entity')} and {_plural(c['mentions_deleted'], 'other fact')} "
                f"that mention them{shown}. Chat history, notes and the activity log are not erased.")

    def forget_entity(entity_id):
        try:
            r = store.forget_entity(entity_id)
            # graph.py audits tool results: counts only, never the name or the deleted values
            return {"forgotten": True, **{k: v for k, v in r.items() if isinstance(v, int)}}
        except ValueError:
            return {"error": "I could not forget that: it is unknown or cannot be forgotten."}

    registry.add(Tool(
        name="remember_fact", domain="memory", args_schema=RememberFactArgs, fn=remember_fact,
        description="Save a structured fact about the user or someone in their life (employer, city, birthday, relation, ...).",
        needs_confirm=True, refuse_after_untrusted=True,
        describe=lambda a: f"Remember: {a['subject']} — {a['predicate']}: {a['value']}" + "".join(
            f" ({k}: {v})" for k, v in (("relation", a.get("relation")), ("kind", a.get("kind")))
            if v and not (k == "kind" and v == "person")),
        done=lambda r: "Got it, I'll remember that."))
    registry.add(Tool(
        name="remember_facts", domain="memory", args_schema=RememberFactsArgs, fn=remember_facts,
        description="Save everything the user said about one or more people, places or organisations in ONE call: "
                    "one statement per subject, with its relation to the user (when introducing someone), all its facts "
                    "and its links to other people (spouse_of, sibling_of, parent_of, child_of, manager_of, reports_to, "
                    "colleague_of, friend_of, partner_of, relative_of).",
        needs_confirm=True, refuse_after_untrusted=True, describe=remember_facts_describe,
        done=lambda r: "Got it, I'll remember that."))
    registry.add(Tool(
        name="graph_lookup", domain="memory", args_schema=LookupArgs, fn=graph_lookup,
        description="Look up what is known about a person, place or organisation (or 'me'): facts with sources and "
                    "times, and links to other people (use them to follow 'my wife's brother').",
        needs_confirm=False))
    registry.add(Tool(
        name="forget_entity", domain="memory", args_schema=ForgetEntityArgs, fn=forget_entity,
        description="Forget everything the graph holds about a person, place or organisation. Look them up with "
                    "graph_lookup first to get the id.",
        needs_confirm=True, describe=forget_describe, done=lambda r: "Okay, forgotten."))
