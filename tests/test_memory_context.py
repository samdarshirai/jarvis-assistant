"""Memory plumbing tests use scripted models; actual alias reasoning needs device acceptance."""
import traceback

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver

from jarvis.agent.domains import DOMAINS
from jarvis.agent.graph import build_graph
from jarvis.llm import MemoryAwareProvider, MemoryContextError
from jarvis.proactive.scheduler import WatchedLLM
from jarvis.tools.phone_tools import register_phone_tools
from jarvis.tools.registry import Registry
from tests.fakes import FakeChat, FakeProvider, MemoryAudit
from tests.test_graph import CFG, call, say

MEMORIES = [{"id": 1, "text": "Ronali is my wife"},
            {"id": 2, "text": "My wife's contact is saved as Pogo"}]


class RecordingChat(FakeChat):
    seen: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)


def provider(script, memories=lambda: MEMORIES):
    raw = FakeProvider({"fast": script, "strong": script})
    for tier in raw._models:
        raw._models[tier] = RecordingChat(script=script, seen=[])
    return raw, MemoryAwareProvider(raw, memories)


async def test_context_is_fresh_without_mutating_messages_or_crossing_users():
    rows = list(MEMORIES)
    raw, llm = provider([AIMessage("ok")] * 3, lambda: list(rows))
    messages = [SystemMessage("task instructions"), HumanMessage("hi")]
    runnable = llm.get("fast")
    await runnable.ainvoke(messages)
    rows.pop()
    await runnable.ainvoke(messages)
    rows.clear()
    await runnable.ainvoke(messages)
    seen = raw._models["fast"].seen
    assert all(m[0].content.count("<memory>") == 1 for m in seen[:2])
    assert "Pogo" in seen[0][0].content and "Pogo" not in seen[1][0].content
    assert "<memory>" not in seen[2][0].content
    assert messages[0].content == "task instructions"
    other, other_llm = provider([AIMessage("ok")], lambda: [{"id": 3, "text": "Other user's fact"}])
    await other_llm.get("fast").ainvoke(messages)
    assert "Ronali" not in other._models["fast"].seen[0][0].content
    assert "Other user's fact" in other._models["fast"].seen[0][0].content


async def test_load_failure_is_sanitized_and_never_invokes_a_model():
    def fail():
        raise RuntimeError("private database details")

    raw, llm = provider([AIMessage("unused")], fail)
    with pytest.raises(MemoryContextError) as err:
        await llm.get("fast").ainvoke([HumanMessage("hi")])
    assert raw._models["fast"].seen == []
    assert "private database details" not in "".join(traceback.format_exception(err.value))
    assert err.value.__context__ is None


async def test_fallbacks_get_identical_context_from_one_memory_load():
    seen, loads = [], []

    async def first(messages):
        seen.append(messages)
        raise RuntimeError("provider unavailable")

    async def second(messages):
        seen.append(messages)
        return AIMessage("ok")

    class Raw:
        def get(self, *args):
            return RunnableLambda(first).with_fallbacks([RunnableLambda(second)])

    llm = MemoryAwareProvider(Raw(), lambda: loads.append(1) or MEMORIES)
    await llm.get("fast").ainvoke([SystemMessage("task")])
    assert loads == [1] and seen[0] == seen[1]
    assert seen[0][0].content.count("<memory>") == 1


async def test_background_callback_configuration_survives_wrapper():
    class Callback(BaseCallbackHandler):
        starts: int = 0

        def on_chat_model_start(self, *args, **kwargs):
            self.starts += 1

    raw, llm = provider([AIMessage("ok")])
    callback = Callback()
    await WatchedLLM(llm).get("fast").with_config(callbacks=[callback]).ainvoke([SystemMessage("background")])
    assert callback.starts == 1
    assert "Pogo" in raw._models["fast"].seen[0][0].content


@pytest.mark.parametrize("domain", list(DOMAINS))
async def test_router_and_every_domain_receive_memories(domain):
    raw, llm = provider([AIMessage(domain), AIMessage("done")])
    g = build_graph(llm, Registry(), MemoryAudit(), InMemorySaver(), "Europe/Berlin")
    await g.ainvoke(say("help me"), CFG)
    seen = [messages for model in raw._models.values() for messages in model.seen]
    assert len(seen) == 2
    assert all(messages[0].content.count("<memory>") == 1 and "Pogo" in messages[0].content for messages in seen)


@pytest.mark.parametrize("phrase", ["call my wife", "call Ronali"])
async def test_resolved_alias_reaches_phone_and_memories_reach_each_step(phrase):
    raw, llm = provider([call("call_contact", {"contact": "Pogo"}), AIMessage("I asked the phone to call Pogo.")])
    reg = Registry()
    register_phone_tools(reg)
    g = build_graph(llm, reg, MemoryAudit(), InMemorySaver(), "Europe/Berlin")
    out = await g.ainvoke(say(phrase), CFG)
    assert out["client_actions"] == [{"type": "call_contact", "contact": "Pogo"}]
    assert raw.choices == ["required", None]
    assert len(raw._models["fast"].seen) == 2
    assert all("Ronali is my wife" in m[0].content and "saved as Pogo" in m[0].content
               for m in raw._models["fast"].seen)


@pytest.mark.parametrize("mixed", [False, True])
async def test_clarification_dispatches_no_action_and_followup_can_call(mixed):
    question = "Do you mean Pogo or Other?"
    reply = call("clarify_contact", {"question": question})
    if mixed:
        reply.tool_calls += call("call_contact", {"contact": "Pogo"}, id="c2").tool_calls
    rows = [*MEMORIES, {"id": 3, "text": "My wife's contact is saved as Other"}]
    raw, llm = provider([reply, AIMessage("phone"), call("call_contact", {"contact": "Pogo"}), AIMessage("Asked the phone.")],
                        lambda: rows)
    reg = Registry()
    register_phone_tools(reg)
    g = build_graph(llm, reg, MemoryAudit(), InMemorySaver(), "Europe/Berlin")
    out = await g.ainvoke(say("call my wife"), CFG)
    assert out["messages"][-1].content == question
    assert out["client_actions"] == []
    out = await g.ainvoke(say("Pogo"), CFG)
    assert out["client_actions"] == [{"type": "call_contact", "contact": "Pogo"}]


async def test_memory_failure_on_direct_call_cannot_queue_action():
    def fail():
        raise RuntimeError("db down")

    raw, llm = provider([call("call_contact", {"contact": "Pogo"})], fail)
    reg = Registry()
    register_phone_tools(reg)
    g = build_graph(llm, reg, MemoryAudit(), InMemorySaver(), "Europe/Berlin")
    with pytest.raises(MemoryContextError):
        await g.ainvoke(say("call Ronali"), CFG)
    assert raw._models["fast"].seen == []


async def test_direct_call_offers_only_call_or_clarification():
    raw, llm = provider([call("clarify_contact", {"question": "Which contact?"})])
    original = raw.get
    offered = []

    def get(tier, tools=None, tool_choice=None):
        offered.append(({t.name for t in tools}, tool_choice))
        return original(tier, tools, tool_choice)

    raw.get = get
    reg = Registry()
    register_phone_tools(reg)
    g = build_graph(llm, reg, MemoryAudit(), InMemorySaver(), "Europe/Berlin")
    await g.ainvoke(say("call my wife"), CFG)
    assert offered == [({"call_contact", "clarify_contact"}, "required")]


async def test_telegram_alias_call_still_cannot_dispatch():
    raw, llm = provider([call("call_contact", {"contact": "Pogo"}), AIMessage("Ask by voice in the app.")])
    reg = Registry()
    register_phone_tools(reg)
    g = build_graph(llm, reg, MemoryAudit(), InMemorySaver(), "Europe/Berlin")
    out = await g.ainvoke(say("call Ronali"), {**CFG, "configurable": {"thread_id": "t", "channel": "telegram"}})
    assert out["client_actions"] == []
    assert raw.choices == [None, None]


async def test_missing_alias_keeps_the_supplied_name():
    raw, llm = provider([call("call_contact", {"contact": "Ronali"}), AIMessage("Asked the phone.")], lambda: [])
    reg = Registry()
    register_phone_tools(reg)
    g = build_graph(llm, reg, MemoryAudit(), InMemorySaver(), "Europe/Berlin")
    out = await g.ainvoke(say("call Ronali"), CFG)
    assert out["client_actions"] == [{"type": "call_contact", "contact": "Ronali"}]


async def test_tenant_runtime_uses_fresh_scoped_memories_for_chat_and_background(pool, uids):
    from jarvis.tenant import Tenants
    from jarvis.users import SecretStore
    from tests.test_tenant import KEY, FakeLLM, settings

    class Raw(FakeLLM):
        def __init__(self, creds, audit):
            super().__init__(creds, audit)
            self.chat = RecordingChat(script=[AIMessage("chat"), AIMessage("done"), AIMessage("background"), AIMessage("fresh")], seen=[])

        def get(self, tier, tools=None, tool_choice=None):
            return self.chat

    for uid in uids:
        SecretStore(pool, KEY).put(uid, "llm_key", f"key-{uid}")
    tenants = Tenants(pool, settings(), InMemorySaver(), llm_provider_factory=Raw,
                      google_service_factory=lambda *args: object())
    try:
        a, b = [await tenants.get(uid) for uid in uids]
        a.memories.add("Ronali is my wife")
        b.memories.add("Other user's fact")
        for rt in (a, b):
            await rt.graph.ainvoke(say("help me"), {**CFG, "configurable": {"thread_id": str(rt.user.id)}})
        alias = a.memories.add("My wife's contact is saved as Pogo")
        await WatchedLLM(a.llm).get("fast").ainvoke([SystemMessage("background")])
        assert "Pogo" in a.llm.chat.seen[-1][0].content
        assert all("Other user's fact" not in m[0].content for m in a.llm.chat.seen)
        assert all("(#1) Ronali is my wife" not in m[0].content for m in b.llm.chat.seen)
        assert a.memories.remove(alias["id"])
        await a.llm.get("fast").ainvoke([SystemMessage("background")])
        assert "Pogo" not in a.llm.chat.seen[-1][0].content
    finally:
        await tenants.close()
    assert a.llm.closed and b.llm.closed


async def test_background_brief_receives_memories_and_falls_back_on_memory_failure():
    from jarvis.proactive.brief import compose

    raw, llm = provider([AIMessage("Good morning. All clear.")])
    facts = {"events": [], "overdue": [], "mail": []}
    assert (await compose(WatchedLLM(llm), facts, "Europe/Berlin"))[1] is True
    assert "Pogo" in raw._models["fast"].seen[0][0].content

    def fail():
        raise RuntimeError("db down")

    raw, llm = provider([AIMessage("unused")], fail)
    text, used = await compose(llm, facts, "Europe/Berlin")
    assert not used and text.startswith("Good morning.")
    assert raw._models["fast"].seen == []


async def test_background_email_receives_memories_and_cannot_write_on_load_failure():
    from tests.test_proactive_mailwatch import NOW, make

    raw, llm = provider([AIMessage('{"found": false}')])
    w = make(llm=WatchedLLM(llm))
    await w.job.run_once(NOW)
    assert "Pogo" in raw._models["fast"].seen[0][0].content
    assert w.cal.created == []

    def fail():
        raise RuntimeError("db down")

    raw, llm = provider([AIMessage("unused")], fail)
    w = make(llm=llm)
    await w.job.run_once(NOW)
    assert w.cal.created == [] and raw._models["fast"].seen == []


async def test_app_reports_memory_unavailability_without_actions():
    from tests.test_appapi import Graph, rt, svc

    out = await svc().chat(rt(graph=Graph(exc=MemoryContextError())), "call Ronali")
    assert out["reply"] == str(MemoryContextError())
    assert out["client_actions"] == []


async def test_telegram_reports_memory_unavailability():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from tests.test_telegram import chat, owner_channel, sent, text_update

    graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(interrupts=())),
                            ainvoke=AsyncMock(side_effect=MemoryContextError()))
    c = chat()
    await owner_channel(graph).on_text(text_update(c), None)
    assert sent(c) == [(str(MemoryContextError()), {})]


async def test_voice_reports_and_speaks_memory_unavailability():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from jarvis.voice.ws import VoiceSession

    graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(interrupts=())))
    session = VoiceSession(None, graph, None, 1, None, None, asyncio.Lock(), set(), CFG,
                           user_id=1, token="test")
    session.send, session._say = AsyncMock(), AsyncMock()

    async def fail():
        raise MemoryContextError()

    await session._guarded(fail())
    session.send.assert_awaited_once_with("error", message=str(MemoryContextError()))
    session._say.assert_awaited_once_with([str(MemoryContextError())])
