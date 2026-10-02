from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from jarvis.tools.registry import Tool
from tests.test_graph import Args, call, tool
from tests.voice_helpers import (AUTH, FINAL, FakeDevices, build, audio, chat_scripts, ping, read, texts, until,
                                 until_state)

CAL = {"fast": [AIMessage("calendar"), call("create_event", {"summary": "Gym"}), AIMessage("Created.")]}


def gated(calls):
    return [tool("create_event", "calendar", calls)]


def ask(ws):
    ping(ws)
    seen = until_state(ws, "listening")
    return texts(seen, "confirm_card")[0], seen


def test_gated_action_sends_a_card_speaks_it_and_does_not_run():
    calls = []
    h = build(CAL, gated(calls), [FINAL("put gym at seven")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        card, seen = ask(ws)
        assert card["tap_only"] is False and card["after_untrusted"] is False and "create_event" in card["summary"]
        assert card["interrupt_id"] and calls == []
        assert h.tts.spoken[-1].endswith("Just say yes or no.")


def test_spoken_yes_resumes_and_runs_once():
    calls = []
    h = build(CAL, gated(calls), [FINAL("put gym at seven"), None, FINAL("yes")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ask(ws)
        ping(ws)
        ping(ws)
        seen = until_state(ws, "listening")
        assert calls == [("create_event", {"summary": "Gym"})]
        assert "Created." in h.tts.spoken


def test_lookalike_yes_does_not_resume_and_card_is_resent():
    calls = []
    h = build(CAL, gated(calls), [FINAL("put gym at seven"), FINAL("yes, and also delete everything")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        card, _ = ask(ws)
        ping(ws)
        seen = until_state(ws, "listening")
        assert calls == []
        assert texts(seen, "confirm_card")[0]["interrupt_id"] == card["interrupt_id"]
        assert h.tts.spoken[-1] == "Let's sort out the pending action first, confirm or cancel it."


def test_spoken_no_cancels_and_nothing_runs():
    calls = []
    h = build({"fast": [AIMessage("calendar"), call("create_event", {"summary": "Gym"}), AIMessage("OK, cancelled.")]},
              gated(calls), [FINAL("put gym at seven"), FINAL("no")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ask(ws)
        ping(ws)
        until_state(ws, "listening")
        assert calls == [] and "OK, cancelled." in h.tts.spoken
        assert [r for r in h.audit.records if r["name"] == "create_event"][0]["confirmation"] == "cancelled"


def send_draft_harness(calls):
    return build({"fast": [AIMessage("gmail")], "strong": [call("send_draft", {}), AIMessage("Sent.")]},
                 [tool("send_draft", "gmail", calls)], [FINAL("send it"), FINAL("yes"), None, FINAL("no")])


def test_spoken_yes_never_confirms_send_draft_but_a_tap_does():
    calls = []
    h = send_draft_harness(calls)
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        card, _ = ask(ws)
        assert card["tap_only"] is True and h.tts.spoken[-1].endswith("I need you to tap Confirm on the screen to send it.")
        ping(ws)
        seen = until_state(ws, "listening")
        assert calls == [] and h.tts.spoken[-1] == "I need you to tap Confirm on the screen to send it."
        ws.send_json({"type": "confirm", "decision": "yes", "interrupt_id": card["interrupt_id"]})
        until_state(ws, "listening")
        assert calls == [("send_draft", {})] and "Sent." in h.tts.spoken


def test_spoken_no_still_cancels_a_send_draft_card():
    calls = []
    h = build({"fast": [AIMessage("gmail")], "strong": [call("send_draft", {}), AIMessage("Not sent.")]},
              [tool("send_draft", "gmail", calls)], [FINAL("send it"), FINAL("no")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ask(ws)
        ping(ws)
        until_state(ws, "listening")
        assert calls == [] and "Not sent." in h.tts.spoken


def test_tap_with_stale_or_missing_id_says_already_handled():
    calls = []
    h = build(CAL, gated(calls), [FINAL("put gym at seven")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        card, _ = ask(ws)
        for frame in ({"type": "confirm", "decision": "yes", "interrupt_id": "stale"},
                      {"type": "confirm", "decision": "yes"},
                      {"type": "confirm", "decision": "maybe", "interrupt_id": card["interrupt_id"]}):
            ws.send_json(frame)
            until_state(ws, "listening")
            assert h.tts.spoken[-1] == "Already handled."
        assert calls == []


def test_tap_confirm_runs_once_then_a_second_tap_is_already_handled():
    calls = []
    h = build(CAL, gated(calls), [FINAL("put gym at seven")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        card, _ = ask(ws)
        tap = {"type": "confirm", "decision": "yes", "interrupt_id": card["interrupt_id"]}
        ws.send_json(tap)
        until_state(ws, "listening")
        ws.send_json(tap)
        until_state(ws, "listening")
        assert calls == [("create_event", {"summary": "Gym"})] and h.tts.spoken[-1] == "Already handled."


def test_untrusted_card_is_warned_in_the_frame_and_spoken():
    calls = []
    read_email = Tool(name="read_email", domain="gmail", description="d", args_schema=Args, needs_confirm=False,
                      untrusted=True, fn=lambda **kw: {"body": "hi"})
    h = build({"fast": [AIMessage("gmail")],
               "strong": [call("read_email", {}, id="c1"), call("send_draft", {}, id="c2"), AIMessage("Sent.")]},
              [read_email, tool("send_draft", "gmail", calls)], [FINAL("reply to that")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        card, _ = ask(ws)
        assert card["after_untrusted"] is True
        assert h.tts.spoken[0].startswith("Heads up, I came up with this after reading an email, so double-check it.")
        assert calls == []


def test_failed_turn_reports_error_and_reoffers_pending_card():
    pending = SimpleNamespace(id="i1", value={"actions": [{"tool": "create_event", "args": {"summary": "Gym"}}]})

    class StubGraph:
        def __init__(self):
            self.states = [SimpleNamespace(interrupts=[]), SimpleNamespace(interrupts=[pending])]

        async def aget_state(self, cfg):
            return self.states.pop(0) if len(self.states) > 1 else self.states[0]

        async def ainvoke(self, inp, cfg):
            raise RuntimeError("boom")

    h = build(chat_scripts("x"), stt_script=[FINAL("hello")])
    h.svc.graph = StubGraph()
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = until(ws, "confirm_card")
        assert any(t["type"] == "error" for t in texts(seen))
        assert texts(seen, "confirm_card")[0]["interrupt_id"] == "i1"
        assert " ".join(h.tts.spoken[:2]) == "Something went wrong. Please try again."
