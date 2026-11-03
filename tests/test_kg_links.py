"""Links between entities: a fact whose object_entity_id points at the other entity (kg.facts._add_link)."""
from types import SimpleNamespace

import pytest

from jarvis.kg.store import GraphStore

SECRET = b"s" * 32


@pytest.fixture
def kg(pool, uids):
    return GraphStore(pool, uids[0], SECRET)


def link(kg, a, pred, b, **kw):
    return kg.add_link(a.id, pred, b.id, source_kind="user_stated", **kw)


def labels(kg, e):
    return [(l.label, l.other_name, l.outgoing) for l in kg.links_for(e.id)]


def test_a_symmetric_link_is_stored_once_and_seen_from_both_people(kg):
    anna, raj = kg.create_entity("Anna"), kg.create_entity("Raj Mehta")
    f = link(kg, anna, "spouse_of", raj)
    assert f.object_id == raj.id and f.value == "Raj Mehta" and f.predicate == "spouse_of"
    assert labels(kg, anna) == [("spouse of", "Raj Mehta", True)]
    assert labels(kg, raj) == [("spouse of", "Anna", False)]
    again = link(kg, raj, "spouse_of", anna)  # said from the other side: the same link, confirmed
    assert again.id == f.id and len(kg.links_for(anna.id)) == 1


def test_a_directed_link_reads_differently_from_each_end_and_child_of_is_stored_swapped(kg):
    mum, kid = kg.create_entity("Mira"), kg.create_entity("Kabir")
    f = link(kg, kid, "child_of", mum)  # "Kabir is Mira's child"
    assert (f.predicate, f.object_id) == ("parent_of", kid.id)
    assert labels(kg, mum) == [("parent of", "Kabir", True)]
    assert labels(kg, kid) == [("child of", "Mira", False)]
    boss, dev = kg.create_entity("Dev"), kg.create_entity("Priya")
    link(kg, dev, "reports_to", boss)
    assert labels(kg, boss) == [("manager of", "Priya", True)] and labels(kg, dev) == [("reports to", "Dev", False)]


def test_a_repeat_confirms_instead_of_duplicating_and_two_rajs_are_two_links(kg):
    anna, raj1, raj2 = kg.create_entity("Anna"), kg.create_entity("Raj"), kg.create_entity("Raj")
    a = link(kg, anna, "friend_of", raj1, ref="m1")
    b = link(kg, anna, "friend_of", raj1, ref="m2")
    c = link(kg, anna, "friend_of", raj2)
    assert a.id == b.id and c.id != a.id
    assert sorted(l.other_id for l in kg.links_for(anna.id)) == sorted([raj1.id, raj2.id])
    assert len(kg.facts_for(anna.id)) == 2


def test_bad_links_are_refused_and_write_nothing(kg):
    anna, raj = kg.create_entity("Anna"), kg.create_entity("Raj")
    for pred, a, b in (("spouse_of", anna, anna), ("neighbour_of", anna, raj), ("spouse_of", anna, SimpleNamespace(id=9999))):
        with pytest.raises(ValueError):
            link(kg, a, pred, b)
    with pytest.raises(ValueError):
        kg.add_link(anna.id, "spouse_of", raj.id, source_kind="rumour")
    assert kg.links_for(anna.id) == [] and kg.facts_for(anna.id) == []


def test_a_renamed_person_shows_the_new_name_in_the_link(pool, uids, kg):
    anna, raj = kg.create_entity("Anna"), kg.create_entity("Raj")
    link(kg, anna, "spouse_of", raj)
    with pool.connection() as c:
        c.execute("UPDATE entity SET display_name = 'Rajesh' WHERE id = %s", (raj.id,))
    assert labels(kg, anna) == [("spouse of", "Rajesh", True)]


def test_forgetting_the_other_person_removes_the_link(kg):
    anna, raj = kg.create_entity("Anna"), kg.create_entity("Raj Mehta")
    link(kg, anna, "spouse_of", raj)
    kg.forget_entity(raj.id)
    assert kg.links_for(anna.id) == [] and kg.facts_for(anna.id) == []


def test_users_cannot_link_to_each_others_entities(pool, uids, kg):
    other = GraphStore(pool, uids[1], SECRET)
    mine, theirs = kg.create_entity("Anna"), other.create_entity("Raj")
    with pytest.raises(ValueError):
        link(kg, mine, "friend_of", theirs)
    assert kg.links_for(mine.id) == [] and other.links_for(theirs.id) == []


def test_text_facts_with_a_link_predicate_still_work_and_are_not_links(kg):
    anna = kg.create_entity("Anna")
    f = kg.add_fact(anna.id, "friend_of", "Bob", source_kind="user_stated")
    assert f.object_id is None and kg.links_for(anna.id) == []
    with pytest.raises(ValueError):
        kg.add_fact(anna.id, "spouse_of", "Raj", source_kind="user_stated")  # only friend_of has a text form


def test_add_statements_creates_the_other_person_once_and_links_in_one_transaction(kg):
    out = kg.add_statements([
        {"entity_id": None, "name": "Anna", "kind": "person", "relation": "sister", "facts": [("city", "Pune")],
         "links": [{"predicate": "spouse_of", "entity_id": None, "name": "Raj", "kind": "person"}]},
        {"entity_id": None, "name": "Rohan", "kind": "person", "relation": None, "facts": [],
         "links": [{"predicate": "colleague_of", "entity_id": None, "name": "Raj", "kind": "person"}]}])
    anna, rohan = out[0]["entity"], out[1]["entity"]
    assert out[0]["links"][0]["other_created"] is True and out[1]["links"][0]["other_created"] is False
    raj = out[0]["links"][0]["other"]
    assert out[1]["links"][0]["other"].id == raj.id and len(kg.find_by_name("Raj")) == 1
    assert labels(kg, raj) == [("spouse of", "Anna", False), ("colleague of", "Rohan", False)]
    assert {f.predicate for f in kg.facts_for(anna.id)} == {"city", "relation_to_user", "spouse_of"}


def test_add_statements_rolls_back_the_new_people_when_a_link_fails(kg):
    with pytest.raises(ValueError):
        kg.add_statements([{"entity_id": None, "name": "Anna", "kind": "person", "relation": None, "facts": [],
                            "links": [{"predicate": "spouse_of", "entity_id": None, "name": "Anna", "kind": "person"}]}])
    assert kg.find_by_name("Anna") == []


def test_a_link_to_an_existing_person_by_id(kg):
    raj = kg.create_entity("Raj")
    out = kg.add_statements([{"entity_id": None, "name": "Anna", "kind": "person", "relation": None, "facts": [],
                              "links": [{"predicate": "partner_of", "entity_id": raj.id, "name": "Raj", "kind": "person"}]}])
    assert out[0]["links"][0]["other_created"] is False and labels(kg, raj) == [("partner of", "Anna", False)]
