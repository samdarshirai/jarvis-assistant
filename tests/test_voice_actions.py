from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from jarvis.tools.phone_tools import register_phone_tools
from jarvis.tools.registry import Registry
from tests.fakes import FakeSTT, FakeTTS
from tests.test_graph import call, tool
from tests.voice_helpers import AUTH, FINAL, build, audio, ping, read, texts, until_state

ALARM = {"fast": [AIMessage("phone"), call("set_alarm", {"hour": 6, "minute": 0}),
                  AIMessage("Asking your phone to set a 06:00 alarm.")]}


def phone_tools():
    r = Registry()
    register_phone_tools(r)
    return r._tools.values()


def test_client_actions_frame_arrives_before_the_reply_is_spoken():
    h = build(ALARM, phone_tools(), [FINAL("set an alarm for six")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = until_state(ws, "listening")
        frames = texts(seen, "client_actions")
        assert frames == [{"type": "client_actions", "actions": [{"type": "set_alarm", "hour": 6, "minute": 0}]}]
        order = [("a" if k == "bytes" else v["type"]) for k, v in seen]
        assert order.index("client_actions") < order.index("a")


def test_turn_without_actions_sends_no_frame():
    h = build({"fast": [AIMessage("chat"), AIMessage("Hi.")]}, (), [FINAL("hello")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        assert texts(until_state(ws, "listening"), "client_actions") == []


def test_deliver_reaches_the_connected_session_and_reports_false_without_one():
    h = build({"fast": [AIMessage("chat"), AIMessage("Hi.")]})
    with TestClient(h.app) as c:
        assert c.portal.call(h.svc.deliver, [{"type": "set_timer", "seconds": 60}]) is False
        with c.websocket_connect("/voice", headers=AUTH) as ws:
            read(ws)
            assert c.portal.call(h.svc.deliver, [{"type": "set_timer", "seconds": 60}]) is True
            assert read(ws) == ("text", {"type": "client_actions", "actions": [{"type": "set_timer", "seconds": 60}]})
