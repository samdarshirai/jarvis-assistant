from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from jarvis.agent.graph import build_graph, parse_domains, repair_tool_gaps, turn_replies, window
from jarvis.tools.registry import Registry, Tool
from tests.fakes import FakeChat, FakeProvider, MemoryAudit

CFG = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}


class Args(BaseModel):
    summary: str = ""


def call(name, args=None, id="c1"):
    return AIMessage("", tool_calls=[{"name": name, "args": args or {}, "id": id, "type": "tool_call"}])


def make(scripts, tools):
    reg = Registry()
    for t in tools:
        reg.add(t)
    audit = MemoryAudit()
    g = build_graph(FakeProvider(scripts), reg, audit, InMemorySaver(), "Europe/Berlin")
    return g, audit


def tool(name, domain, calls, needs_confirm=True, fn=None):
    return Tool(name=name, domain=domain, description="d", args_schema=Args,
                fn=fn or (lambda **kw: calls.append((name, kw)) or {"ok": True}), needs_confirm=needs_confirm)


def say(text="hi"):
    return {"messages": [HumanMessage(text)]}


# --- units ---
def test_parse_domains():
    assert parse_domains("calendar, tasks") == ["calendar", "tasks"]
    assert parse_domains("Tasks") == ["tasks"]
    assert parse_domains("calendar chat") == ["calendar"]
    assert parse_domains("gibberish") == ["chat"]
    assert parse_domains("calendar calendar") == ["calendar"]


def test_window_never_starts_on_orphan_tool_message():
    msgs = [HumanMessage("a"), call("x"), ToolMessage("r", tool_call_id="c1"), AIMessage("ok")]
    assert not isinstance(window(msgs, 3)[0], ToolMessage)
    msgs = [call("x"), ToolMessage("r", tool_call_id="c1"), AIMessage("ok")]
    assert not isinstance(window(msgs, 2)[0], ToolMessage)


def test_turn_replies_only_current_turn_in_order():
    msgs = [HumanMessage("old"), AIMessage("old reply"), HumanMessage("new"), call("x"),
            ToolMessage("r", tool_call_id="c1"), AIMessage("first"), AIMessage("second")]
    assert turn_replies(msgs) == ["first", "second"]


# --- graph ---
async def test_read_tool_runs_without_confirmation():
    calls = []
    g, audit = make({"fast": [AIMessage("calendar")], "strong": [call("list_events"), AIMessage("You have gym.")]},
                    [tool("list_events", "calendar", calls, needs_confirm=False)])
    out = await g.ainvoke(say(), CFG)
    assert "__interrupt__" not in out
    assert calls == [("list_events", {})]
    assert turn_replies(out["messages"]) == ["You have gym."]
    assert audit.records[0]["kind"] == "tool" and audit.records[0]["confirmation"] == "not_required"


async def test_write_tool_pauses_and_does_not_run_until_approved():
    calls = []
    g, audit = make({"fast": [AIMessage("calendar")], "strong": [call("create_event", {"summary": "Gym"}), AIMessage("Created.")]},
                    [tool("create_event", "calendar", calls)])
    out = await g.ainvoke(say(), CFG)
    assert out["__interrupt__"][0].value == {"actions": [{"tool": "create_event", "args": {"summary": "Gym"}}]}
    assert calls == []
    out = await g.ainvoke(Command(resume=True), CFG)
    assert calls == [("create_event", {"summary": "Gym"})]
    assert turn_replies(out["messages"]) == ["Created."]
    assert audit.records[0]["confirmation"] == "approved"


async def test_cancel_runs_nothing_and_model_is_told():
    calls = []
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [call("create_event"), AIMessage("Okay, cancelled.")]},
                [tool("create_event", "calendar", calls)])
    await g.ainvoke(say(), CFG)
    out = await g.ainvoke(Command(resume=False), CFG)
    assert calls == []
    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert "cancelled" in tool_msgs[0].content.lower()


async def test_untagged_new_tool_defaults_to_confirmation():
    calls = []
    t = Tool(name="new_tool", domain="tasks", description="d", args_schema=Args,
             fn=lambda **kw: calls.append(kw) or {})  # needs_confirm not given
    g, _ = make({"fast": [AIMessage("tasks")], "strong": [call("new_tool"), AIMessage("done")]}, [t])
    out = await g.ainvoke(say(), CFG)
    assert "__interrupt__" in out and calls == []


async def test_domain_cannot_call_another_domains_tool():
    calls = []
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [call("list_tasks"), AIMessage("sorry")]},
                [tool("list_tasks", "tasks", calls, needs_confirm=False)])
    out = await g.ainvoke(say(), CFG)
    assert calls == []
    assert "unknown tool" in [m for m in out["messages"] if isinstance(m, ToolMessage)][0].content.lower()


async def test_unknown_tool_is_not_executed():
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [call("rm_rf"), AIMessage("sorry")]}, [])
    out = await g.ainvoke(say(), CFG)
    assert "__interrupt__" in out  # unknown names are treated as needing confirmation
    out = await g.ainvoke(Command(resume=True), CFG)
    assert "unknown tool" in [m for m in out["messages"] if isinstance(m, ToolMessage)][0].content.lower()


async def test_mixed_safe_and_write_in_one_step_asks_once_and_cancel_blocks_both():
    calls = []
    both = AIMessage("", tool_calls=[
        {"name": "list_events", "args": {}, "id": "a", "type": "tool_call"},
        {"name": "create_event", "args": {}, "id": "b", "type": "tool_call"}])
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [both, AIMessage("ok")]},
                [tool("list_events", "calendar", calls, needs_confirm=False), tool("create_event", "calendar", calls)])
    out = await g.ainvoke(say(), CFG)
    assert len(out["__interrupt__"][0].value["actions"]) == 1
    await g.ainvoke(Command(resume=False), CFG)
    assert calls == []


async def test_multi_domain_runs_in_order_and_replies_in_order():
    calls = []
    g, _ = make({"fast": [AIMessage("calendar, tasks")],
                 "strong": [call("list_events"), AIMessage("Calendar done."), call("list_tasks"), AIMessage("Tasks done.")]},
                [tool("list_events", "calendar", calls, False), tool("list_tasks", "tasks", calls, False)])
    out = await g.ainvoke(say(), CFG)
    assert [c[0] for c in calls] == ["list_events", "list_tasks"]
    assert turn_replies(out["messages"]) == ["Calendar done.", "Tasks done."]


async def test_tool_value_error_goes_back_to_model_and_loop_continues():
    def bad(**kw):
        raise ValueError("end must be after start")
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [call("list_events"), AIMessage("Let me fix that.")]},
                [tool("list_events", "calendar", [], False, fn=bad)])
    out = await g.ainvoke(say(), CFG)
    tm = [m for m in out["messages"] if isinstance(m, ToolMessage)][0]
    assert "end must be after start" in tm.content
    assert turn_replies(out["messages"]) == ["Let me fix that."]


async def test_client_actions_collected_from_tool_results():
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [call("list_events"), AIMessage("ok")]},
                [tool("list_events", "calendar", [], False, fn=lambda **kw: {"client_action": {"type": "set_alarm", "time": "06:00"}})])
    out = await g.ainvoke(say(), CFG)
    assert out["client_actions"] == [{"type": "set_alarm", "time": "06:00"}]


async def test_tool_key_error_is_invalid_arguments_and_loop_continues():
    def bad(**kw):
        raise KeyError("Nowhere/Land")
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [call("list_events"), AIMessage("Let me fix that.")]},
                [tool("list_events", "calendar", [], False, fn=bad)])
    out = await g.ainvoke(say(), CFG)
    tm = [m for m in out["messages"] if isinstance(m, ToolMessage)][0]
    assert "Invalid arguments" in tm.content
    assert turn_replies(out["messages"]) == ["Let me fix that."]


async def test_audit_failure_does_not_break_turn_or_hide_result():
    class BrokenAudit:
        def record(self, *a, **kw):
            raise RuntimeError("db down")
    calls = []
    reg = Registry()
    reg.add(tool("list_events", "calendar", calls, needs_confirm=False))
    g = build_graph(FakeProvider({"fast": [AIMessage("calendar")],
                                  "strong": [call("list_events"), AIMessage("You have gym.")]}),
                    reg, BrokenAudit(), InMemorySaver(), "Europe/Berlin")
    out = await g.ainvoke(say(), CFG)
    assert calls == [("list_events", {})]
    tm = [m for m in out["messages"] if isinstance(m, ToolMessage)][0]
    assert "ok" in tm.content
    assert turn_replies(out["messages"]) == ["You have gym."]


# --- repair_tool_gaps ---
def test_repair_inserts_one_synthetic_after_ai():
    ai = call("x")
    out = repair_tool_gaps([HumanMessage("a"), ai, HumanMessage("b")])
    assert [type(m) for m in out] == [HumanMessage, AIMessage, ToolMessage, HumanMessage]
    assert out[2].tool_call_id == "c1" and "Not executed" in out[2].content


def test_repair_leaves_complete_history_unchanged():
    msgs = [HumanMessage("a"), call("x"), ToolMessage("r", tool_call_id="c1"), AIMessage("ok")]
    assert repair_tool_gaps(msgs) == msgs


def test_repair_inserts_only_missing_id():
    ai = AIMessage("", tool_calls=[{"name": "x", "args": {}, "id": "a", "type": "tool_call"},
                                   {"name": "x", "args": {}, "id": "b", "type": "tool_call"}])
    done = ToolMessage("r", tool_call_id="a")
    out = repair_tool_gaps([HumanMessage("h"), ai, done, HumanMessage("n")])
    assert out[2] is done and out[3].tool_call_id == "b" and len(out) == 5


SEEN: list = []


class RecChat(FakeChat):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        SEEN.append(list(messages))
        return super()._generate(messages, stop, run_manager, **kwargs)


async def test_abandoned_interrupt_history_is_repaired_for_model():
    calls = []
    reg = Registry()
    reg.add(tool("create_event", "calendar", calls))
    prov = FakeProvider({"fast": [AIMessage("calendar"), AIMessage("calendar")], "strong": []})
    SEEN.clear()
    prov._models["strong"] = RecChat(script=[call("create_event", id="old"), AIMessage("fine")])
    g = build_graph(prov, reg, MemoryAudit(), InMemorySaver(), "Europe/Berlin")
    out = await g.ainvoke(say("make event"), CFG)
    assert "__interrupt__" in out
    await g.ainvoke(say("never mind, hello"), CFG)
    assert calls == []
    last = SEEN[-1]
    ids = {m.tool_call_id for m in last if isinstance(m, ToolMessage)}
    assert "old" in ids
    assert all(tc["id"] in ids for m in last if isinstance(m, AIMessage) for tc in m.tool_calls)
