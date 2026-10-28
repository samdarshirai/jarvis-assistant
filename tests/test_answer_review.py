import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from jarvis.agent.graph import build_graph
from jarvis.tools.registry import Registry
from jarvis.tools.research_tools import register_research_tools
from jarvis.web import SearchError
from tests.fakes import FakeProvider, MemoryAudit


CFG = {"configurable": {"thread_id": "review"}, "recursion_limit": 40}


def route(mode, domains=None):
    return AIMessage(json.dumps({"domains": domains or ["chat"], "review_mode": mode}))


def call(name, args, ident="c1"):
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": ident, "type": "tool_call"}])


class RecordingProvider(FakeProvider):
    def __init__(self, scripts):
        super().__init__(scripts)
        self.requests = []

    def get(self, tier, tools=None, tool_choice=None):
        self.requests.append((tier, [t.name for t in tools or []], tool_choice))
        return super().get(tier, tools, tool_choice)


def graph(provider, registry=None):
    return build_graph(provider, registry or Registry(), MemoryAudit(), InMemorySaver(), "Europe/Berlin")


async def test_repeated_corrections_use_strong_reasoning_and_reset_next_turn():
    p = RecordingProvider({"fast": [route("reason"), route("reason"), route("normal"), AIMessage("Okay.")],
                           "strong": [AIMessage("17 × 19 is 323."), AIMessage("It is still 323.")]})
    g = graph(p)
    history = [HumanMessage("What is 17 × 19?"), AIMessage("323."), HumanMessage("No, it is 333.")]
    out = await g.ainvoke({"messages": history}, CFG)
    assert out["review_mode"] == "reason"
    await g.ainvoke({"messages": [HumanMessage("Actually, it is 343.")]}, CFG)
    out = await g.ainvoke({"messages": [HumanMessage("I prefer short replies.")]}, CFG)
    assert out["review_mode"] == "normal"
    assert [r[0] for r in p.requests] == ["fast", "strong", "fast", "strong", "fast", "fast"]


async def test_followup_keywords_do_not_skip_contextual_router():
    p = RecordingProvider({"fast": [route("reason")], "strong": [AIMessage("Let me reconsider that claim.")]})
    out = await graph(p).ainvoke({"messages": [HumanMessage("A public factual question"),
        AIMessage("An answer"), HumanMessage("No, that historical meeting was in 1910.")]}, CFG)
    assert out["domains"] == ["chat"]
    assert p.requests[-1][0] == "strong"


@pytest.mark.parametrize("bad", ["nonsense", '{"domains":["calendar"],"review_mode":"bogus"}',
                                  '{"domains":["not_a_domain"],"review_mode":"normal"}'])
async def test_malformed_router_output_falls_back_to_strong_without_tools(bad):
    p = RecordingProvider({"fast": [AIMessage(bad)], "strong": [AIMessage("Could you clarify?")]})
    out = await graph(p).ainvoke({"messages": [HumanMessage("hi")]}, CFG)
    assert out["domains"] == ["chat"]
    assert p.requests[-1] == ("strong", [], None)


class Search:
    def __init__(self, fail=False):
        self.queries = []
        self.fail = fail

    def search(self, query):
        self.queries.append(query)
        if self.fail:
            raise SearchError("Web search timed out.")
        return {"results": [{"title": "Reference", "url": "https://reference.example/fact",
                             "snippet": "A short factual reference."}]}


@pytest.mark.parametrize("voice", [False, True])
async def test_lookup_forces_search_and_delivers_evidence_on_strong_tier(voice, monkeypatch):
    search, registry = Search(), Registry()
    register_research_tools(registry, search)
    monkeypatch.setattr("jarvis.tools.research_tools.fetch_page", lambda url: {"url": url, "text": "Full evidence"})
    p = RecordingProvider({"fast": [route("lookup")], "strong": [
        call("web_search", {"query": "compare the competing claims"}),
        call("fetch_page", {"url": "https://reference.example/fact"}, "c2"), AIMessage("Evidence-based answer.")]})
    cfg = {"configurable": {"thread_id": "lookup", "voice": voice}}
    out = await graph(p, registry).ainvoke({"messages": [HumanMessage("A factual question"),
        AIMessage("An answer"), HumanMessage("No, actually something else happened.")]}, cfg)
    assert out["domains"] == ["research"]
    assert search.queries == ["compare the competing claims"]
    assert p.requests[1] == ("strong", ["web_search", "fetch_page"], "web_search")
    assert all(r[0] == "strong" for r in p.requests[1:])
    assert out["research_calls"] == {"web_search": 1, "fetch_page": 1}
    assert all(m.content.startswith("<untrusted_web>") for m in out["messages"] if isinstance(m, ToolMessage))


async def test_failed_search_is_not_evidence_and_is_counted():
    search, registry = Search(fail=True), Registry()
    register_research_tools(registry, search)
    p = RecordingProvider({"fast": [route("lookup")], "strong": [
        call("web_search", {"query": "claim"}), AIMessage("I could not verify that.")]})
    out = await graph(p, registry).ainvoke({"messages": [HumanMessage("Verify a claim")]}, CFG)
    result = next(m.content for m in out["messages"] if isinstance(m, ToolMessage))
    assert json.loads(result) == {"error": "Web search timed out."}
    assert out["research_calls"] == {"web_search": 1}
    assert out["read_untrusted"] is False


async def test_research_limits_enforced_within_batches_and_reset_between_turns(monkeypatch):
    search, registry, fetched = Search(), Registry(), []
    register_research_tools(registry, search)
    monkeypatch.setattr("jarvis.tools.research_tools.fetch_page", lambda url: fetched.append(url) or {"text": "evidence"})
    searches = [call("web_search", {"query": str(i)}, f"s{i}").tool_calls[0] for i in range(3)]
    fetches = [call("fetch_page", {"url": "https://reference.example/fact"}, f"f{i}").tool_calls[0] for i in range(3)]
    p = RecordingProvider({"fast": [route("lookup"), route("lookup")], "strong": [
        AIMessage("", tool_calls=searches), AIMessage("", tool_calls=fetches), AIMessage("Limited conclusion."),
        call("web_search", {"query": "new claim"}), AIMessage("New conclusion.")]})
    g = graph(p, registry)
    out = await g.ainvoke({"messages": [HumanMessage("Verify a claim")]}, CFG)
    assert search.queries == ["0", "1"] and len(fetched) == 2
    assert out["research_calls"] == {"web_search": 2, "fetch_page": 2}
    assert p.requests[2][1] == ["fetch_page"]
    assert p.requests[3] == ("strong", [], None)
    errors = [m.content for m in out["messages"] if isinstance(m, ToolMessage) and "limit reached" in m.content]
    assert len(errors) == 2
    out = await g.ainvoke({"messages": [HumanMessage("But what about another claim?")]}, CFG)
    assert out["research_calls"] == {"web_search": 1}
    assert search.queries[-1] == "new claim"


async def test_personal_dispute_stays_in_its_domain_and_keeps_confirmation():
    from pydantic import BaseModel
    from jarvis.tools.registry import Tool

    calls, registry = [], Registry()
    registry.add(Tool(name="change_event", domain="calendar", description="Change an event",
                      args_schema=BaseModel, fn=lambda: calls.append(True)))
    p = RecordingProvider({"fast": [route("reason", ["calendar"])], "strong": [call("change_event", {})]})
    out = await graph(p, registry).ainvoke({"messages": [HumanMessage("Check my meeting"),
        AIMessage("It is at 3."), HumanMessage("No, change that meeting to 4.")]}, CFG)
    assert out["domains"] == ["calendar"] and "__interrupt__" in out
    assert calls == []


async def test_reason_review_keeps_voice_on_strong_tier():
    p = RecordingProvider({"fast": [route("reason")], "strong": [AIMessage("323, because 17 × 20 − 17 = 323.")]})
    await graph(p).ainvoke({"messages": [HumanMessage("Isn't 17 times 19 actually 333?")]},
                          {"configurable": {"thread_id": "voice", "voice": True}})
    assert p.requests[-1][0] == "strong"
