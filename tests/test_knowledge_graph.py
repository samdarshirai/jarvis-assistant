import pytest
from langchain_core.messages import AIMessage

from jarvis.agent.domains import DOMAINS
from jarvis.agent.graph import ROUTER_PROMPT, build_graph, keyword_domain, parse_domains
from jarvis.tools.registry import Registry
from langgraph.checkpoint.memory import InMemorySaver
from tests.fakes import FakeChat, FakeProvider, MemoryAudit

CFG = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}
VOICE = {"configurable": {"thread_id": "t", "voice": True}, "recursion_limit": 40}


class RecordingChat(FakeChat):
    seen: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)


def graph(scripts, memories=None, recording=()):
    provider = FakeProvider(scripts)
    for tier in recording:
        provider._models[tier] = RecordingChat(script=scripts[tier])
    g = build_graph(provider, Registry(), MemoryAudit(), InMemorySaver(), "Europe/Berlin", memories=memories)
    return g, provider


def hello(text="hi"):
    from langchain_core.messages import HumanMessage
    return {"messages": [HumanMessage(text)]}


# --- domains and routing ---
def test_new_domains_exist_with_expected_tiers():
    assert {d: DOMAINS[d].tier for d in ("memory", "notes", "research")} == {
        "memory": "fast", "notes": "fast", "research": "strong"}


def test_prompts_name_the_web_wrapper_and_the_tools_to_use():
    assert "untrusted_web" in DOMAINS["research"].prompt
    assert "untrusted_web" in DOMAINS["chat"].prompt  # shared base prompt
    assert "web_search" in DOMAINS["research"].prompt
    assert "forget" in DOMAINS["memory"].prompt
    assert "append" in DOMAINS["notes"].prompt


def test_router_prompt_lists_the_new_domains():
    for d in ("memory", "notes", "research"):
        assert d in ROUTER_PROMPT
    assert parse_domains("research, notes") == ["research", "notes"]


@pytest.mark.parametrize("text,expected", [
    ("remember that Priya is my manager", ["memory"]),
    ("forget that I like mornings", ["memory"]),
    ("what do you know about me", ["memory"]),
    ("what's in my notes about pricing", ["notes"]),
    ("jot down the plan", ["notes"]),
    ("search the web for robot vacuums", ["research"]),
    ("look that up for me", ["research"]),
    ("research the best standing desks", ["research"]),
    ("what's on my calendar tomorrow", ["calendar"]),
    ("research robot vacuums and save a note", None),  # two domains: the router decides
    ("add this to my Google Calendar", ["calendar"]),
    ("put it on my Google Tasks list", ["tasks"]),
    ("google that for me", ["research"]),
    ("google for the best standing desks", ["research"]),
    ("how are you", None),
])
def test_keyword_routing(text, expected):
    assert keyword_domain(text) == expected


# --- memory block ---
async def test_memory_block_reaches_the_agent_prompt():
    g, provider = graph({"fast": [AIMessage("chat"), AIMessage("hello")]},
                        memories=lambda: [{"id": 7, "text": "Priya is my manager"}], recording=("fast",))
    await g.ainvoke(hello(), CFG)
    system = provider._models["fast"].seen[1][0].content  # call 0 is the router
    assert "<memory>" in system and "(#7) Priya is my manager" in system


async def test_no_memories_means_no_block():
    g, provider = graph({"fast": [AIMessage("chat"), AIMessage("hello")]}, memories=lambda: [], recording=("fast",))
    await g.ainvoke(hello(), CFG)
    assert "<memory>" not in provider._models["fast"].seen[1][0].content


async def test_a_failing_memory_load_does_not_break_the_turn():
    def boom():
        raise RuntimeError("db down")

    g, provider = graph({"fast": [AIMessage("chat"), AIMessage("hello")]}, memories=boom, recording=("fast",))
    out = await g.ainvoke(hello(), CFG)
    assert out["messages"][-1].content == "hello"
    assert "<memory>" not in provider._models["fast"].seen[1][0].content


async def test_graph_without_memories_still_works():
    g, _ = graph({"fast": [AIMessage("chat"), AIMessage("hello")]})
    assert (await g.ainvoke(hello(), CFG))["messages"][-1].content == "hello"


# --- voice ---
async def test_research_stays_on_the_strong_tier_in_voice_and_gets_the_spoken_note():
    g, provider = graph({"fast": [], "strong": [AIMessage("done")]}, recording=("strong",))
    out = await g.ainvoke(hello("search the web for robot vacuums"), VOICE)
    assert out["messages"][-1].content == "done"  # the fast script is empty, so a fast call would have crashed
    assert "spoken aloud" in provider._models["strong"].seen[0][0].content


async def test_text_research_has_no_spoken_note():
    g, provider = graph({"fast": [], "strong": [AIMessage("done")]}, recording=("strong",))
    await g.ainvoke(hello("search the web for robot vacuums"), CFG)
    assert "spoken aloud" not in provider._models["strong"].seen[0][0].content


async def test_notes_and_memory_use_the_fast_tier_in_voice():
    g, _ = graph({"fast": [AIMessage("ok")], "strong": []})
    out = await g.ainvoke(hello("remember that I like tea"), VOICE)
    assert out["messages"][-1].content == "ok"


async def test_remember_goes_through_the_gate_then_shows_up_in_the_next_prompt(pool):
    from langgraph.types import Command

    from jarvis.memory import MemoryStore
    from jarvis.tools.memory_tools import register_memory_tools

    store = MemoryStore(pool)
    reg = Registry()
    register_memory_tools(reg, store)
    call = AIMessage("", tool_calls=[{"name": "remember", "args": {"text": "I like window seats"}, "id": "c1",
                                      "type": "tool_call"}])
    # "remember ..." is routed by keyword (no router call); "how are you" needs one router call, then the agent
    provider = FakeProvider({"fast": [call, AIMessage("chat"), AIMessage("noted")]})
    provider._models["fast"] = RecordingChat(script=provider._models["fast"].script)
    g = build_graph(provider, reg, MemoryAudit(), InMemorySaver(), "Europe/Berlin", memories=store.all)

    out = await g.ainvoke(hello("remember that I like window seats"), CFG)
    assert out["__interrupt__"][0].value["actions"][0]["summary"] == "Remember: I like window seats"
    assert store.all() == []  # nothing saved before the owner confirms
    out = await g.ainvoke(Command(resume=True), CFG)
    assert out["messages"][-1].content == "Got it, I'll remember that."
    assert [m["text"] for m in store.all()] == ["I like window seats"]

    await g.ainvoke(hello("how are you"), CFG)
    last_system = provider._models["fast"].seen[-1][0].content
    assert "(#" in last_system and "I like window seats" in last_system
