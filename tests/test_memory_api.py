"""The app's memory endpoints: the "Right now" list, Undo, edit, keep-forever, summaries, off-the-record chats."""
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

from langchain_core.messages import AIMessage

from jarvis.conversations import ConversationStore
from jarvis.kg.store import GraphStore
from jarvis.memory.store import MemoryStore
from tests.fakes import MemoryAudit, MemoryConversations
from tests.test_appapi import Graph, rt, svc


def mem_rt(pool, uid):
    r = rt(uid=uid, audit=MemoryAudit())
    r.memory, r.kg = MemoryStore(pool, uid), GraphStore(pool, uid)
    return r


async def test_list_edit_delete_and_keep_forever(pool, uids):
    r = mem_rt(pool, uids[0])
    item = r.memory.add_item("In Pune till Friday")[0]
    code, body = await svc().memory_items(r)
    assert code == 200 and [i["text"] for i in body["items"]] == ["In Pune till Friday"]
    code, body = await svc().memory_item_update(r, item.id, "In Pune till Saturday", date(2026, 11, 7))
    assert code == 200 and body["item"]["text"] == "In Pune till Saturday"
    assert r.memory.get(item.id).expires_at == datetime(2026, 11, 7, 22, 59, tzinfo=timezone.utc)  # 23:59 Berlin
    code, body = await svc().memory_item_promote(r, item.id)
    assert code == 200 and body["fact_ids"] and r.memory.active() == []
    assert [f.value for f in r.kg.facts_for(r.kg.self_entity().id)] == ["In Pune till Saturday"]
    other = r.memory.add_item("Waiting for the plumber")[0]
    assert (await svc().memory_item_delete(r, other.id))[0] == 200 and r.memory.get(other.id) is None
    assert [x["name"] for x in r.audit.records] == ["memory_item_update", "memory_item_promote", "memory_item_delete"]


async def test_unknown_or_foreign_items_are_404_and_bad_input_400(pool, uids):
    mine, theirs = mem_rt(pool, uids[0]), mem_rt(pool, uids[1])
    item = mine.memory.add_item("In Pune till Friday")[0]
    for call in (svc().memory_item_delete(theirs, item.id), svc().memory_item_promote(theirs, item.id),
                 svc().memory_item_update(theirs, item.id, "x", None)):
        assert (await call)[0] == 404
    assert (await svc().memory_item_update(mine, item.id, "   ", None))[0] == 400
    assert (await svc().memory_items(theirs))[1] == {"items": []}
    assert (await svc().memory_items(rt()))[0] == 503  # a runtime without a memory store


async def test_chat_returns_memory_events_only_when_there_are_some():
    events = [{"type": "memory_saved", "id": 1, "text": "In Pune till Friday", "until": "Fri 6 Nov"}]
    out = await svc().chat(rt(graph=Graph(result={"messages": [AIMessage("Noted.")], "memory_events": events})), "x")
    assert out["memory_events"] == events
    out = await svc().chat(rt(graph=Graph(result={"messages": [AIMessage("Hi.")]})), "hi")
    assert "memory_events" not in out


async def test_conversations_carry_their_summary_and_a_new_one_can_be_off_the_record(pool, uids):
    r = mem_rt(pool, uids[0])
    convs = r.conversations = ConversationStore(pool, uids[0])
    out = await svc().new_conversation(r, off_record=True)
    assert out["conversation"]["off_record"] is True
    assert r.memory.conversation_for(convs.get(out["conversation"]["id"]).thread_id)[1] is True
    plain = (await svc().new_conversation(r))["conversation"]
    assert plain["off_record"] is False
    with pool.connection() as c:
        r.memory.save_digest(c, plain["id"], "Planned the weekend.", [], False)
    listed = {c["id"]: c["summary"] for c in (await svc().conversations(r))["conversations"]}
    assert listed == {out["conversation"]["id"]: None, plain["id"]: "Planned the weekend."}
    fake = NS(**{**vars(rt()), "conversations": MemoryConversations(1)})
    assert (await svc().new_conversation(fake))["conversation"]["off_record"] is False  # no memory store: still works
