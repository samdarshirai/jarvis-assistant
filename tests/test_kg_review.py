from datetime import timedelta

import pytest

from jarvis.kg import review
from jarvis.kg.candidates import EXPIRE_AFTER, CandidateStore
from jarvis.kg.store import GraphStore
from tests.test_kg_candidates import NOW, conv, q, row


@pytest.fixture
def g(pool, uids):
    return GraphStore(pool, uids[0])


@pytest.fixture
def cs(pool, uids):
    return CandidateStore(pool, uids[0])


def add(pool, cs, uid, rows, **kw):
    cid = conv(pool, uid, **kw)
    assert cs.save(cid, kw.get("last_at", NOW), rows, NOW)
    return cid, [r[0] for r in q(pool, "SELECT id FROM fact_candidate WHERE conversation_id = %s ORDER BY id", cid)]


def status(pool, cand_id):
    return q(pool, "SELECT status, decided_at FROM fact_candidate WHERE id = %s", cand_id)[0]


def test_list_groups_by_conversation_newest_first_with_replaces(pool, uids, g, cs):
    me = g.self_entity()
    old = g.add_fact(me.id, "city", "Berlin", source_kind="user_stated")
    older, _ = add(pool, cs, uids[0], [row(value="Oslo")], last_at=NOW - timedelta(days=2), closed=True)
    newer, _ = add(pool, cs, uids[0], [row(subject_name="me", subject_kind="self", relation=None, subject_norm="me",
                                           value="Pune", value_norm="pune", replaces_fact_id=old.id),
                                       row(value="Delhi", value_norm="delhi")], last_at=NOW, closed=True)
    got = cs.list_new()
    assert [x["conversation"]["id"] for x in got] == [newer, older]
    first = got[0]["items"]
    assert [i["value"] for i in first] == ["Pune", "Delhi"]
    assert first[0]["replaces"] == "Berlin" and first[1]["replaces"] is None
    assert first[0]["quote"] and first[0]["subject"] == "me"
    assert cs.count_new() == 3


def test_decided_and_foreign_candidates_are_not_listed(pool, uids, g, cs):
    _, ids = add(pool, cs, uids[0], [row(), row(value="Delhi", value_norm="delhi")], closed=True)
    add(pool, CandidateStore(pool, uids[1]), uids[1], [row()], closed=True, thread="other")
    assert cs.decide([ids[0]], "dismissed", NOW) == [ids[0]]
    assert [i["id"] for grp in cs.list_new() for i in grp["items"]] == [ids[1]]
    assert cs.count_new() == 1 and CandidateStore(pool, uids[1]).count_new() == 1


def test_approve_creates_entity_relation_and_fact_with_provenance(pool, uids, g, cs):
    cid, (c1,) = add(pool, cs, uids[0], [row()], closed=True)
    out = review.approve(g, c1)
    assert out["created"] and out["entity"]["name"] == "Anna" and len(out["fact_ids"]) == 2
    facts = {f.predicate: f for f in g.facts_for(out["entity"]["id"])}
    assert facts["city"].value == "Pune" and facts["relation_to_user"].value == "sister"
    assert facts["city"].source_kind == "user_stated" and facts["city"].excerpt == "my sister Anna lives in Pune"
    assert q(pool, "SELECT ref FROM source WHERE user_id = %s AND kind = 'user_stated'", uids[0]) == [(f"conversation:{cid}",)]
    assert status(pool, c1)[0] == "approved" and status(pool, c1)[1] is not None


def test_approve_reuses_a_single_match_and_applies_edits(pool, uids, g, cs):
    anna = g.create_entity("Anna")
    _, (c1,) = add(pool, cs, uids[0], [row()], closed=True)
    out = review.approve(g, c1, value="Mumbai", relation="friend")
    assert out["entity"]["id"] == anna.id and not out["created"]
    assert {f.predicate: f.value for f in g.facts_for(anna.id)} == {"city": "Mumbai", "relation_to_user": "friend"}


def test_approve_about_me_uses_the_self_entity(pool, uids, g, cs):
    _, (c1,) = add(pool, cs, uids[0], [row(subject_name="me", subject_kind="self", relation=None, subject_norm="me")],
                   closed=True)
    out = review.approve(g, c1)
    assert out["entity"]["id"] == g.self_entity().id and not out["created"]
    assert [f.predicate for f in g.facts_for(out["entity"]["id"])] == ["city"]


def test_ambiguous_subject_asks_and_writes_nothing(pool, uids, g, cs):
    a1, a2 = g.create_entity("Anna"), g.create_entity("Anna")
    _, (c1,) = add(pool, cs, uids[0], [row()], closed=True)
    out = review.approve(g, c1)
    assert {o["id"] for o in out["choice"]} == {a1.id, a2.id}
    assert status(pool, c1)[0] == "new"
    assert q(pool, "SELECT count(*) FROM fact WHERE user_id = %s", uids[0]) == [(0,)]
    assert q(pool, "SELECT count(*) FROM source WHERE user_id = %s", uids[0]) == [(0,)]


def test_ambiguous_subject_resolves_by_pick_or_new_person(pool, uids, g, cs):
    a1, a2 = g.create_entity("Anna"), g.create_entity("Anna")
    _, (c1, c2) = add(pool, cs, uids[0], [row(), row(value="Delhi", value_norm="delhi")], closed=True)
    assert review.approve(g, c1, entity_id=a2.id)["entity"]["id"] == a2.id
    out = review.approve(g, c2, new=True)
    assert out["created"] and out["entity"]["id"] not in (a1.id, a2.id)


def test_approve_rejects_foreign_unknown_and_decided(pool, uids, g, cs):
    _, (c1,) = add(pool, cs, uids[0], [row()], closed=True)
    other = GraphStore(pool, uids[1])
    with pytest.raises(LookupError):
        review.approve(other, c1)
    with pytest.raises(LookupError):
        review.approve(g, 9999)
    with pytest.raises(LookupError):
        review.approve(g, c1, entity_id=9999)
    assert status(pool, c1)[0] == "new"
    review.approve(g, c1)
    with pytest.raises(LookupError):
        review.approve(g, c1)
    with pytest.raises(LookupError):
        review.dismiss(g, c1)


def test_approve_is_atomic_when_a_write_fails(pool, uids, g, cs):
    _, (c1,) = add(pool, cs, uids[0], [row()], closed=True)
    with pytest.raises(ValueError):
        review.approve(g, c1, value="   ")
    assert status(pool, c1)[0] == "new"
    assert q(pool, "SELECT count(*) FROM entity WHERE user_id = %s", uids[0]) == [(0,)]


def test_dismiss_is_remembered_by_the_extractors_filter(pool, uids, g, cs):
    _, (c1,) = add(pool, cs, uids[0], [row()], closed=True)
    review.dismiss(g, c1)
    assert status(pool, c1)[0] == "dismissed"
    assert cs.known([("anna", "city", "pune")]) == {("anna", "city", "pune")}
    assert cs.count_new() == 0


def test_bulk_dismiss_and_approve_report_each_id(pool, uids, g, cs):
    g.create_entity("Bob"), g.create_entity("Bob")
    _, ids = add(pool, cs, uids[0], [row(), row(subject_name="Bob", subject_norm="bob"),
                                     row(subject_name="Cy", subject_norm="cy")], closed=True)
    out = review.bulk(g, ids + [9999], "approve")
    assert [a["id"] for a in out["approved"]] == [ids[0], ids[2]]
    assert out["needs_choice"] == [ids[1]] and out["skipped"] == [9999]
    assert review.bulk(g, ids + [9999], "dismiss") == {"dismissed": [ids[1]], "skipped": [ids[0], ids[2], 9999]}


def test_expire_marks_old_undecided_candidates_without_remembering_them(pool, uids, g, cs):
    _, (c1, c2) = add(pool, cs, uids[0], [row(), row(value="Delhi", value_norm="delhi")], closed=True)
    q(pool, "UPDATE fact_candidate SET created_at = %s WHERE id = %s RETURNING 1", NOW - EXPIRE_AFTER - timedelta(days=1), c1)
    assert cs.expire(NOW) == 1
    assert status(pool, c1) == ("expired", None) and status(pool, c2)[0] == "new"
    assert cs.known([("anna", "city", "pune")]) == set()
    assert CandidateStore(pool, uids[1]).expire(NOW) == 0


# ---- link candidates ----

def link_row(**over):
    return row(**{"predicate": "spouse_of", "value": "Raj", "value_norm": "raj", "relation": None,
                  "quote": "Anna is married to Raj", **over})


def test_approving_a_link_candidate_links_the_two_people(pool, uids, g, cs):
    anna, raj = g.create_entity("Anna"), g.create_entity("Raj")
    _, (c1,) = add(pool, cs, uids[0], [link_row()], closed=True)
    out = review.approve(g, c1)
    assert out["other"] == {"id": raj.id, "name": "Raj", "created": False} and out["entity"]["id"] == anna.id
    assert [(l.label, l.other_name) for l in g.links_for(anna.id)] == [("spouse of", "Raj")]
    assert out["fact_ids"] == [out["fact"]["id"]] and status(pool, c1)[0] == "approved"


def test_a_new_other_person_is_created_and_me_means_the_user(pool, uids, g, cs):
    _, (c1, c2) = add(pool, cs, uids[0], [link_row(), link_row(value="me", value_norm="me")], closed=True)
    out = review.approve(g, c1)
    assert out["other"]["created"] and g.find_by_name("Raj")
    me = review.approve(g, c2)
    assert me["other"]["name"] == "Me" and not me["other"]["created"]


def test_an_ambiguous_other_asks_and_writes_nothing_then_resolves_by_pick_or_new(pool, uids, g, cs):
    g.create_entity("Anna")
    r1, r2 = g.create_entity("Raj"), g.create_entity("Raj")
    _, (c1, c2) = add(pool, cs, uids[0], [link_row(), link_row(predicate="friend_of")], closed=True)
    out = review.approve(g, c1)
    assert out["which"] == "other" and {o["id"] for o in out["choice"]} == {r1.id, r2.id}
    assert status(pool, c1)[0] == "new" and q(pool, "SELECT count(*) FROM fact WHERE user_id = %s", uids[0]) == [(0,)]
    assert review.approve(g, c1, other_entity_id=r2.id)["other"]["id"] == r2.id
    made = review.approve(g, c2, new_other=True)["other"]
    assert made["created"] and made["id"] not in (r1.id, r2.id)


def test_a_foreign_other_entity_is_not_found(pool, uids, g, cs):
    other = GraphStore(pool, uids[1]).create_entity("Raj")
    g.create_entity("Anna")
    _, (c1,) = add(pool, cs, uids[0], [link_row()], closed=True)
    with pytest.raises(LookupError):
        review.approve(g, c1, other_entity_id=other.id)
    assert status(pool, c1)[0] == "new"


def test_bulk_approve_sends_an_ambiguous_link_to_the_user_to_choose(pool, uids, g, cs):
    g.create_entity("Anna"), g.create_entity("Raj"), g.create_entity("Raj")
    _, (c1,) = add(pool, cs, uids[0], [link_row()], closed=True)
    out = review.bulk(g, [c1], "approve")
    assert out["needs_choice"] == [c1] and out["approved"] == []
