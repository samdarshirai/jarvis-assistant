import pytest
from pydantic import ValidationError

from jarvis.kg.store import GraphStore
from jarvis.tools.graph_tools import LookupArgs, RememberFactArgs, register_graph_tools
from jarvis.tools.registry import Registry


@pytest.fixture
def store(pool, uids):
    return GraphStore(pool, uids[0])


@pytest.fixture
def reg(store):
    r = Registry()
    register_graph_tools(r, store)
    return r


def run(reg, tool, **args):
    t = reg.get(tool)
    return t.fn(**t.args_schema(**args).model_dump(exclude_unset=True))


def vals(store, ent, pred):
    return [f.value for f in store.facts_for(ent.id) if f.predicate == pred]


def test_remember_fact_creates_sister_with_relation(reg, store):
    r = run(reg, "remember_fact", subject="Meera", predicate="city", value="Pune", relation="sister")
    assert r["remembered"]["subject"] == "Meera"
    (e,) = store.find_by_name("Meera")
    assert vals(store, e, "relation_to_user") == ["sister"]
    assert vals(store, e, "city") == ["Pune"]
    assert [x.id for x in store.find_by_name("sister")] == [e.id]


def test_remember_fact_updates_existing_instead_of_duplicating(reg, store):
    run(reg, "remember_fact", subject="Meera", predicate="city", value="Pune", relation="sister")
    run(reg, "remember_fact", subject="Meera", predicate="city", value="Delhi")
    (e,) = store.find_by_name("Meera")
    assert vals(store, e, "city") == ["Delhi"]


def test_remember_fact_ambiguous_writes_nothing(reg, store):
    store.create_entity("Anna Lee")
    store.create_entity("Anna Roy")
    r = run(reg, "remember_fact", subject="Anna", predicate="city", value="Pune")
    assert r["error"] == "ambiguous" and len(r["candidates"]) == 2
    assert all(not store.facts_for(e.id) for e in store.find_by_name("Anna"))


def test_remember_fact_me_goes_to_self(reg, store):
    run(reg, "remember_fact", subject="Me", predicate="employer", value="Acme")
    assert vals(store, store.self_entity(), "employer") == ["Acme"]


def test_value_over_200_rejected():
    with pytest.raises(ValidationError):
        RememberFactArgs(subject="me", predicate="note", value="x" * 201)


def test_unknown_predicate_rejected_by_schema():
    with pytest.raises(ValidationError):
        RememberFactArgs(subject="me", predicate="shoe_size", value="9")


def test_graph_lookup_shows_source_and_time_and_pending(reg, store):
    run(reg, "remember_fact", subject="Meera", predicate="city", value="Pune")
    (e,) = store.find_by_name("Meera")
    store.add_fact(e.id, "city", "Goa", source_kind="email")  # lower trust than user_stated -> pending
    (r,) = run(reg, "graph_lookup", name="Meera")
    assert r["name"] == "Meera" and r["kind"] == "person"
    assert r["facts"][0]["predicate"] == "city" and r["facts"][0]["source"] == "user_stated"
    assert "observed_at" in r["facts"][0]
    assert [p["value"] for p in r["pending"]] == ["Goa"]
    assert run(reg, "graph_lookup", name="Nobody") == []


def test_graph_lookup_other_user_invisible(reg, pool, uids):
    other = GraphStore(pool, uids[1])
    other.create_entity("Secret Sam")
    assert run(reg, "graph_lookup", name="Secret Sam") == []


def test_describe_text(reg):
    t = reg.get("remember_fact")
    assert t.describe({"subject": "Meera", "predicate": "city", "value": "Pune"}) == "Remember: Meera — city: Pune"
    assert t.needs_confirm and t.refuse_after_untrusted
    assert LookupArgs(name="x") and not reg.get("graph_lookup").needs_confirm


@pytest.mark.parametrize("field,bad", [("subject", ""), ("subject", "  "), ("value", "   "), ("relation", "   ")])
def test_blank_inputs_rejected(field, bad):
    args = {"subject": "Meera", "predicate": "city", "value": "Pune", field: bad}
    with pytest.raises(ValidationError):
        RememberFactArgs(**args)


def test_inputs_are_stripped():
    a = RememberFactArgs(subject=" Meera ", predicate="city", value=" Pune ", relation=" sister ")
    assert (a.subject, a.value, a.relation) == ("Meera", "Pune", "sister")


def test_failed_write_leaves_no_half_created_entity(reg, store, monkeypatch):
    real = store.add_fact

    def boom(eid, predicate, *a, **k):
        if predicate == "city":
            raise RuntimeError("db down")
        return real(eid, predicate, *a, **k)

    monkeypatch.setattr(store, "add_fact", boom)
    with pytest.raises(RuntimeError):
        run(reg, "remember_fact", subject="Meera", predicate="city", value="Pune", relation="sister")
    assert store.find_by_name("Meera") == [] and store.find_by_name("sister") == []
