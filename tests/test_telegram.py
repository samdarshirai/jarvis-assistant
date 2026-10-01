from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel
from telegram import Update
from telegram.error import TelegramError

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


def button_update(c, data, chat_id=OWNER, edit=None):
    q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_reply_markup=edit or AsyncMock(),
                        message=SimpleNamespace(chat_id=chat_id, chat=c))
    return SimpleNamespace(callback_query=q)


def make_channel(scripts, calls, extra=()):
    reg = Registry()
    reg.add(Tool(name="create_event", domain="calendar", description="d", args_schema=Args,
                 fn=lambda **kw: calls.append(kw) or {"ok": True}))
    for t in extra:
        reg.add(t)
    g = build_graph(FakeProvider(scripts), reg, MemoryAudit(), InMemorySaver(), "Europe/Berlin")
    return TelegramChannel(g, OWNER)


def ids(c, n=-1):
    """(yes_data, no_data) from the keyboard of the n-th message sent to chat c."""
    kb = c.send_message.call_args_list[n].kwargs["reply_markup"].inline_keyboard[0]
    return kb[0].callback_data, kb[1].callback_data


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

    yes, _ = ids(c)
    c3 = chat()
    await ch.on_button(button_update(c3, yes), None)
    assert calls == [{"summary": "Gym"}]
    assert sent(c3) == [("Created.", {})]

    # Review focus 2: second tap does nothing
    c4 = chat()
    await ch.on_button(button_update(c4, yes), None)
    assert calls == [{"summary": "Gym"}]
    assert sent(c4) == [("Already handled.", {})]


async def test_cancel_button_runs_nothing():
    calls = []
    tool_call = AIMessage("", tool_calls=[{"name": "create_event", "args": {}, "id": "1", "type": "tool_call"}])
    ch = make_channel({"fast": [AIMessage("calendar")], "strong": [tool_call, AIMessage("Cancelled.")]}, calls)
    c0 = chat()
    await ch.on_text(text_update(c0), None)
    c = chat()
    await ch.on_button(button_update(c, ids(c0)[1]), None)
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


TOOL_CALL = AIMessage("", tool_calls=[{"name": "create_event", "args": {"summary": "Gym"}, "id": "1", "type": "tool_call"}])


def pending_channel(calls):
    return make_channel({"fast": [AIMessage("calendar")], "strong": [TOOL_CALL, AIMessage("Created.")]}, calls)


async def test_non_owner_button_dropped_and_logged(caplog):
    calls = []
    ch = pending_channel(calls)
    c0 = chat()
    await ch.on_text(text_update(c0), None)
    real = ch.graph
    spy = SimpleNamespace(ainvoke=AsyncMock(), aget_state=real.aget_state)
    ch.graph = spy
    c = chat()
    with caplog.at_level("WARNING"):
        await ch.on_button(button_update(c, ids(c0)[0], chat_id=99), None)
    spy.ainvoke.assert_not_called()
    assert sent(c) == [] and calls == []
    assert "non-owner" in caplog.text


async def test_failed_resume_reprompts_and_retry_works():
    calls = []
    ch = pending_channel(calls)
    c0 = chat()
    await ch.on_text(text_update(c0), None)
    real = ch.graph
    boom = {"on": True}

    async def ainvoke(inp, cfg):
        if boom["on"] and isinstance(inp, Command):
            boom["on"] = False
            raise RuntimeError("down")
        return await real.ainvoke(inp, cfg)

    ch.graph = SimpleNamespace(ainvoke=ainvoke, aget_state=real.aget_state)
    c = chat()
    yes, _ = ids(c0)
    await ch.on_button(button_update(c, yes), None)
    out = sent(c)
    assert out[0] == (FAIL_TEXT, {}) and "create_event" in out[1][0] and "reply_markup" in out[1][1]
    assert calls == []
    assert ids(c) == ids(c0)  # same interrupt, same id on the re-offered keyboard
    c2 = chat()
    await ch.on_button(button_update(c2, ids(c)[0]), None)
    assert calls == [{"summary": "Gym"}] and sent(c2) == [("Created.", {})]


async def test_edit_markup_error_does_not_block_action():
    calls = []
    ch = pending_channel(calls)
    c0 = chat()
    await ch.on_text(text_update(c0), None)
    c = chat()
    await ch.on_button(button_update(c, ids(c0)[0], edit=AsyncMock(side_effect=TelegramError("x"))), None)
    assert calls == [{"summary": "Gym"}] and sent(c) == [("Created.", {})]


def test_build_registers_handlers_without_network():
    app = TelegramChannel(None, OWNER).build("123:abc")
    assert len(app.handlers[0]) == 2 and len(app.handlers[1]) == 1


def test_format_confirmation_with_summary_keeps_raw_call():
    s = format_confirmation({"actions": [{"tool": "delete_event", "args": {"event_id": "e1", "scope": "all"},
                                          "summary": "Delete 'Gym' (Tue 2026-10-06 07:00-08:00)"}]})
    assert "Delete 'Gym' (Tue" in s and "delete_event" in s and "e1" in s


TASK_CALL = AIMessage("", tool_calls=[{"name": "create_task", "args": {"summary": "Mum"}, "id": "2", "type": "tool_call"}])


def two_write_channel(calls):
    t = Tool(name="create_task", domain="tasks", description="d", args_schema=Args,
             fn=lambda **kw: calls.append(("task", kw)) or {"ok": True})
    return make_channel({"fast": [AIMessage("calendar, tasks")],
                         "strong": [TOOL_CALL, AIMessage("cal done"), TASK_CALL, AIMessage("all done")]},
                        calls, extra=[t])


async def test_double_tap_does_not_approve_the_second_write():
    calls = []
    ch = two_write_channel(calls)
    c0 = chat()
    await ch.on_text(text_update(c0), None)
    yes1, _ = ids(c0)
    c1 = chat()
    await ch.on_button(button_update(c1, yes1), None)
    assert calls == [{"summary": "Gym"}]
    assert "create_task" in sent(c1)[-1][0]
    yes2, _ = ids(c1)
    assert yes2 != yes1
    c2 = chat()  # the double tap on the old button
    await ch.on_button(button_update(c2, yes1), None)
    assert sent(c2) == [("Already handled.", {})] and calls == [{"summary": "Gym"}]
    c3 = chat()
    await ch.on_button(button_update(c3, yes2), None)
    assert calls == [{"summary": "Gym"}, ("task", {"summary": "Mum"})]


async def test_stale_no_after_resolution_is_already_handled():
    calls = []
    ch = pending_channel(calls)
    c0 = chat()
    await ch.on_text(text_update(c0), None)
    yes, no = ids(c0)
    await ch.on_button(button_update(chat(), yes), None)
    c = chat()
    await ch.on_button(button_update(c, no), None)
    assert sent(c) == [("Already handled.", {})] and calls == [{"summary": "Gym"}]


async def test_legacy_bare_callback_data_is_already_handled():
    calls = []
    ch = pending_channel(calls)
    await ch.on_text(text_update(chat()), None)
    for data in ("yes", "no", "yes:wrong", None):
        c = chat()
        await ch.on_button(button_update(c, data), None)
        assert sent(c) == [("Already handled.", {})]
    assert calls == []
    assert await ch._pending()  # still waiting for a real answer


async def test_empty_reply_sends_fallback():
    ch = make_channel({"fast": [AIMessage("chat"), AIMessage("calendar")], "strong": []}, [])
    ch.graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(interrupts=())),
                               ainvoke=AsyncMock(return_value={"messages": []}))
    c = chat()
    await ch.on_text(text_update(c), None)
    assert sent(c) == [("Finished, but I have no summary to show. Ask me to check if you are unsure.", {})]


def test_text_handler_ignores_edited_messages():
    app = TelegramChannel(None, OWNER).build("123:abc")
    h = app.handlers[0][0]

    def upd(key):
        return Update.de_json({"update_id": 1, key: {"message_id": 1, "date": 0, "edit_date": 1,
                              "chat": {"id": OWNER, "type": "private"}, "text": "hi"}}, None)

    assert h.check_update(upd("message"))
    assert not h.check_update(upd("edited_message"))


from jarvis.channels.telegram import WARN_UNTRUSTED


class ReadArgs(BaseModel):
    message_id: str = ""


class SendArgs(BaseModel):
    draft_id: str = ""


def test_format_confirmation_warns_only_when_flagged():
    base = {"actions": [{"tool": "send_draft", "args": {"draft_id": "d1"}}]}
    assert format_confirmation(base).startswith("Confirm this action?")
    flagged = format_confirmation({**base, "after_untrusted": True})
    assert flagged.startswith(WARN_UNTRUSTED) and "Confirm this action?" in flagged


async def test_confirmation_card_warns_after_reading_an_email():
    sent_calls = []
    read = Tool(name="read_email", domain="gmail", description="d", args_schema=ReadArgs,
                fn=lambda **kw: {"body": "hi"}, needs_confirm=False, untrusted=True)
    send = Tool(name="send_draft", domain="gmail", description="d", args_schema=SendArgs,
                fn=lambda **kw: sent_calls.append(kw) or {"sent": True})
    call = lambda name, args, id: AIMessage("", tool_calls=[{"name": name, "args": args, "id": id, "type": "tool_call"}])
    ch = make_channel({"fast": [AIMessage("gmail")],
                       "strong": [call("read_email", {"message_id": "m1"}, "1"),
                                  call("send_draft", {"draft_id": "d1"}, "2"), AIMessage("sent")]},
                      [], extra=[read, send])
    c = chat()
    await ch.on_text(text_update(c, "reply to Raj"), None)
    (prompt, kw), = sent(c)
    assert prompt.startswith(WARN_UNTRUSTED) and "send_draft" in prompt and "reply_markup" in kw
    assert sent_calls == []
