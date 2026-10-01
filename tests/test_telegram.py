from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel
from telegram import Update

from jarvis.agent.graph import build_graph
from jarvis.channels.telegram import FAIL_TEXT, TelegramChannel, format_confirmation
from jarvis.tools.registry import Registry, Tool
from tests.fakes import FakeProvider, MemoryAudit

OWNER = 42


class Args(BaseModel):
    summary: str = ""


def chat():
    return SimpleNamespace(send_message=AsyncMock())


def text_update(c, text="book gym"):
    return SimpleNamespace(effective_chat=c, message=SimpleNamespace(text=text))


def button_update(c, data):
    q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_reply_markup=AsyncMock(),
                        message=SimpleNamespace(chat_id=OWNER, chat=c))
    return SimpleNamespace(callback_query=q)


def make_channel(scripts, calls):
    reg = Registry()
    reg.add(Tool(name="create_event", domain="calendar", description="d", args_schema=Args,
                 fn=lambda **kw: calls.append(kw) or {"ok": True}))
    g = build_graph(FakeProvider(scripts), reg, MemoryAudit(), InMemorySaver(), "Europe/Berlin")
    return TelegramChannel(g, OWNER)


def sent(c):
    return [(a[0], k) for a, k in c.send_message.call_args_list]


async def test_plain_reply():
    ch = make_channel({"fast": [AIMessage("chat"), AIMessage("Hello!")]}, [])
    c = chat()
    await ch.on_text(text_update(c, "hi"), None)
    assert sent(c) == [("Hello!", {})]


async def test_confirmation_flow_runs_action_once():
    calls = []
    tool_call = AIMessage("", tool_calls=[{"name": "create_event", "args": {"summary": "Gym"}, "id": "1", "type": "tool_call"}])
    ch = make_channel({"fast": [AIMessage("calendar")], "strong": [tool_call, AIMessage("Created.")]}, calls)
    c = chat()
    await ch.on_text(text_update(c), None)
    (prompt, kw), = sent(c)
    assert "create_event" in prompt and "reply_markup" in kw and calls == []

    # Review focus 1: new text while confirmation pending is refused
    c2 = chat()
    await ch.on_text(text_update(c2, "something else"), None)
    assert sent(c2) == [("Confirm or cancel the pending action first.", {})]

    c3 = chat()
    await ch.on_button(button_update(c3, "yes"), None)
    assert calls == [{"summary": "Gym"}]
    assert sent(c3) == [("Created.", {})]

    # Review focus 2: second tap does nothing
    c4 = chat()
    await ch.on_button(button_update(c4, "yes"), None)
    assert calls == [{"summary": "Gym"}]
    assert sent(c4) == [("Already handled.", {})]


async def test_cancel_button_runs_nothing():
    calls = []
    tool_call = AIMessage("", tool_calls=[{"name": "create_event", "args": {}, "id": "1", "type": "tool_call"}])
    ch = make_channel({"fast": [AIMessage("calendar")], "strong": [tool_call, AIMessage("Cancelled.")]}, calls)
    await ch.on_text(text_update(chat()), None)
    c = chat()
    await ch.on_button(button_update(c, "no"), None)
    assert calls == [] and sent(c) == [("Cancelled.", {})]


async def test_graph_failure_sends_fixed_message():
    graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(interrupts=())),
                            ainvoke=AsyncMock(side_effect=RuntimeError("all models down")))
    c = chat()
    await TelegramChannel(graph, OWNER).on_text(text_update(c), None)
    assert sent(c) == [(FAIL_TEXT, {})]


def test_owner_filter_rejects_strangers():
    ch = TelegramChannel(None, OWNER)

    def upd(chat_id):
        return Update.de_json({"update_id": 1, "message": {"message_id": 1, "date": 0,
                               "chat": {"id": chat_id, "type": "private"}, "text": "hi"}}, None)

    assert ch.owner_filter.check_update(upd(OWNER))
    assert not ch.owner_filter.check_update(upd(99))


def test_format_confirmation_lists_actions():
    s = format_confirmation({"actions": [{"tool": "delete_event", "args": {"event_id": "e1", "scope": "all"}}]})
    assert "delete_event" in s and "e1" in s
