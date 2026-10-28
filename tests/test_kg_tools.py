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
    assert t.describe({"subject": "sister", "predicate": "city", "value": "Pune", "relation": "sister"}) == \
        "Remember: sister — city: Pune (relation: sister)"
    assert t.describe({"subject": "Acme", "predicate": "note", "value": "my gym", "kind": "org"}) == \
        "Remember: Acme — note: my gym (kind: org)"
    assert t.describe({"subject": "Ravi", "predicate": "city", "value": "Goa", "relation": "boss", "kind": "person"}) == \
        "Remember: Ravi — city: Goa (relation: boss)"
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


@pytest.fixture
def fstore(pool, uids):
    return GraphStore(pool, uids[0], b"s" * 32)


@pytest.fixture
def freg(fstore):
    r = Registry()
    register_graph_tools(r, fstore)
    return r


def test_forget_entity_describe_includes_counts_and_not_erased_line(freg, fstore):
    e = fstore.create_entity("Meera")
    fstore.add_fact(e.id, "city", "Pune", source_kind="user_stated", excerpt="Pune")
    t = freg.get("forget_entity")
    text = t.describe({"entity_id": e.id})
    assert "Meera" in text and "1 fact" in text
    assert "Chat history, notes and the activity log are not erased." in text
    assert fstore.find_by_name("Meera")  # preview is read-only


def test_forget_entity_removes_everything_and_suppresses(freg, fstore):
    e = fstore.create_entity("Meera")
    fstore.add_fact(e.id, "city", "Pune", source_kind="user_stated", excerpt="Pune")
    r = run(freg, "forget_entity", entity_id=e.id)
    assert r["forgotten"] is True and r["facts"] == 1
    assert fstore.find_by_name("Meera") == []
    assert fstore.is_suppressed("name", "Meera")
    assert freg.get("forget_entity").done(r) == "Okay, forgotten."


def test_forget_entity_unknown_or_self_returns_error(freg, fstore):
    t = freg.get("forget_entity")
    for eid in (999999, fstore.self_entity().id):
        assert set(run(freg, "forget_entity", entity_id=eid)) == {"error"}
        assert isinstance(t.describe({"entity_id": eid}), str)


def test_forget_entity_requires_confirmation(freg):
    assert freg.get("forget_entity").needs_confirm is True


def test_forget_other_users_entity_refused(pool, uids, fstore, freg):
    other = GraphStore(pool, uids[1], b"s" * 32)
    e = other.create_entity("Meera")
    assert set(run(freg, "forget_entity", entity_id=e.id)) == {"error"}
    assert other.find_by_name("Meera")


def test_forget_entity_result_has_counts_only_never_the_name(freg, fstore):
    import json
    e = fstore.create_entity("Meera Iyer")
    fstore.add_fact(fstore.self_entity().id, "note", "lunch with Meera Iyer", source_kind="user_stated")
    r = run(freg, "forget_entity", entity_id=e.id)  # graph.py writes this result to audit_log
    assert "Meera" not in json.dumps(r) and "lunch" not in json.dumps(r)
    assert r["forgotten"] is True and r["mentions_deleted"] == 1
    assert all(isinstance(v, int) for v in r.values())


def test_forget_entity_describe_lists_the_mentioned_facts(freg, fstore):
    e = fstore.create_entity("Meera Iyer")
    me = fstore.self_entity().id
    for i in range(7):
        fstore.add_fact(me, "note", f"lunch {i} with Meera Iyer", source_kind="user_stated")
    text = freg.get("forget_entity").describe({"entity_id": e.id})
    assert '"lunch 0 with Meera Iyer"' in text and '"lunch 4 with Meera Iyer"' in text
    assert "lunch 5" not in text and "and 2 more" in text
    assert text.endswith("Chat history, notes and the activity log are not erased.")
