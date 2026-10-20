import asyncio
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from starlette.websockets import WebSocketDisconnect

from jarvis.tenant import thread_config
from jarvis.voice.devices import DeviceAuth
from tests.voice_helpers import AUTH, FINAL, build, chat_scripts, ping, read, until, until_state, texts, audio


def test_missing_or_bad_token_is_closed_before_accept():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c:
        for headers in ({}, {"authorization": "Bearer nope"}, {"authorization": "good"}, {"authorization": "Bearer "}):
            with pytest.raises(WebSocketDisconnect) as e:
                with c.websocket_connect("/voice", headers=headers):
                    pass
            assert e.value.code == 4401
    assert h.stt.streams == []  # no audio pipeline was opened for a stranger


def test_voice_uses_users_deepgram_key():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
    assert h.keys == [("stt", "dg-1"), ("tts", "dg-1")]


def _refused(h, hello=None):
    """Connect, send the app's hello (unless None), and return (frames before the close, close code)."""
    seen = []
    with TestClient(h.app) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/voice", headers=AUTH) as ws:
                if hello is not None:
                    ws.send_json({"type": "hello", **hello})
                for _ in range(5):
                    seen.append(read(ws))
    return seen, e.value.code


def test_voice_without_deepgram_key_closes_4403():
    h = build(chat_scripts("Hi."), with_voice=False)
    seen, code = _refused(h, hello={})
    assert seen == [("text", {"type": "error", "code": "needs_deepgram"})] and code == 4403
    assert h.keys == [] and h.stt.streams == []


def test_refused_session_still_stores_the_fcm_token():
    # a friend who skipped Deepgram must still get proactive push: the hello is read before the 4403
    h = build(chat_scripts("Hi."), with_voice=False)
    seen, code = _refused(h, hello={"fcm_token": "tok-1"})
    assert seen == [("text", {"type": "error", "code": "needs_deepgram"})] and code == 4403
    assert h.devices.fcm == {1: "tok-1"}


def test_refused_session_without_hello_is_not_held_open():
    h = build(chat_scripts("Hi."), with_voice=False)
    h.svc.hello_timeout = 0.1
    seen, code = _refused(h)
    assert seen == [("text", {"type": "error", "code": "needs_deepgram"})] and code == 4403
    assert h.devices.fcm == {}


def test_voice_without_llm_key_closes_4403_needs_llm():
    h = build(chat_scripts("Hi."), with_llm=False)
    seen, code = _refused(h, hello={"fcm_token": "tok-2"})
    assert seen == [("text", {"type": "error", "code": "needs_llm"})] and code == 4403
    assert h.stt.streams == [] and h.devices.fcm == {1: "tok-2"}


def test_audio_turn_streams_transcript_reply_and_audio_in_order():
    h = build(chat_scripts("Hi. How can I help?"), stt_script=[FINAL("hello jarvis")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        assert read(ws) == ("text", {"type": "state", "state": "listening"})
        ping(ws)
        seen = until_state(ws, "listening")  # the turn ends back in listening
        t = texts(seen)
        assert t[0] == {"type": "transcript", "role": "user", "text": "hello jarvis", "final": True}
        assert {"type": "state", "state": "thinking"} in t
        assert {"type": "transcript", "role": "assistant", "text": "Hi. How can I help?", "final": True} in t
        assert audio(seen) == [b"audio:Hi.", b"audio:How can I help?"]
        # first audio arrives while still "speaking", before the turn is over
        first_audio = next(i for i, (k, _) in enumerate(seen) if k == "bytes")
        speaking = next(i for i, (k, v) in enumerate(seen) if k == "text" and v.get("state") == "speaking")
        assert speaking < first_audio < len(seen) - 1
    assert h.stt.streams[0].closed


def test_empty_reply_is_still_spoken():
    h = build({"fast": [AIMessage("chat"), AIMessage("")]}, stt_script=[FINAL("hi")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        until_state(ws, "listening")
        ping(ws)
        seen = until_state(ws, "listening")
        assert audio(seen) and h.tts.spoken[0].startswith("Finished, but I have no summary")


def test_second_connection_replaces_the_first():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c:
        with c.websocket_connect("/voice", headers=AUTH) as w1:
            read(w1)
            with c.websocket_connect("/voice", headers=AUTH) as w2:
                read(w2)
                with pytest.raises(WebSocketDisconnect) as e:
                    for _ in range(10):
                        read(w1)
                assert e.value.code == 4000
                assert len(h.stt.streams) == 2
    assert all(s.closed for s in h.stt.streams)  # both pipelines are released once the sockets are gone


def test_hello_stores_the_fcm_token():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_json({"type": "hello", "fcm_token": "tok-1"})
        ws.send_json({"type": "nonsense"})  # answered with an error frame: proves the hello was processed first
        until(ws, "error")
    assert h.devices.fcm == {1: "tok-1"}


def test_bye_closes_the_socket():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_json({"type": "bye"})
        with pytest.raises(WebSocketDisconnect):
            for _ in range(5):
                read(ws)
    assert h.stt.streams[0].closed


def test_stt_stream_ending_cleanly_is_a_failure():
    from tests.fakes import FakeSTT, FakeSTTStream

    class EndedStream(FakeSTTStream):
        async def events(self):
            return
            yield  # pragma: no cover

    class EndedSTT(FakeSTT):
        async def open(self):
            s = EndedStream(self.script)
            self.streams.append(s)
            return s

    h = build(chat_scripts("Hi."), stt=EndedSTT())
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        seen = until(ws, "error")
        assert texts(seen, "error")
        seen = until_state(ws, "listening")
        assert h.tts.spoken == ["Sorry, I can't hear you right now."]
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(10):
                read(ws)
        assert e.value.code == 1011


def _stalled_turn(h, ws):
    read(ws)
    ping(ws)
    until(ws, "transcript")
    while read(ws)[0] != "bytes":  # first audio chunk: the turn is mid-speech
        pass


def test_disconnect_cancels_turn_in_flight():
    from tests.fakes import FakeTTS
    h = build(chat_scripts("Hi."), stt_script=[FINAL("hi")], tts=FakeTTS(stall=True))
    with TestClient(h.app) as c:
        with c.websocket_connect("/voice", headers=AUTH) as ws:
            _stalled_turn(h, ws)
        c.portal.call(asyncio.sleep, 0.2)
        assert h.tts.cancelled is True
        assert h.svc.sessions == {}


def test_replacement_cancels_turn_in_flight():
    from tests.fakes import FakeTTS
    h = build(chat_scripts("Hi."), stt_script=[FINAL("hi")], tts=FakeTTS(stall=True))
    with TestClient(h.app) as c:
        with c.websocket_connect("/voice", headers=AUTH) as w1:
            _stalled_turn(h, w1)
            with c.websocket_connect("/voice", headers=AUTH) as w2:
                read(w2)
                with pytest.raises(WebSocketDisconnect) as e:
                    for _ in range(10):
                        read(w1)
                assert e.value.code == 4000
        c.portal.call(asyncio.sleep, 0.2)  # w1's handler only unwinds once its client side has left
        assert h.tts.cancelled is True
        assert h.svc.sessions == {}


def test_bye_emits_no_error_frame():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_json({"type": "bye"})
        seen = []
        with pytest.raises(WebSocketDisconnect):
            for _ in range(5):
                seen.append(read(ws))
        assert texts(seen, "error") == []


def test_device_of_an_unknown_user_is_closed_before_accept():
    h = build(chat_scripts("Hi."))
    h.devices.tokens["good"] = DeviceAuth(1, 99)
    with TestClient(h.app) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/voice", headers=AUTH):
                pass
        assert e.value.code == 4401
    assert h.stt.streams == []


AUTH2 = {"authorization": "Bearer good2"}


def two_users(scripts, stt_script=()):
    """User 1 (token "good") and user 2 (token "good2") on one graph and saver: only the thread tells them apart."""
    h = build(scripts, stt_script=stt_script)
    h.tenants.add(2, h.graph, {"deepgram_key": "dg-2"})
    h.devices.tokens["good2"] = DeviceAuth(2, 2)
    return h


def test_voice_sessions_of_two_users_use_own_threads():
    h = two_users(chat_scripts("Hi one.", "chat", "Hi two."), [FINAL("i am one"), FINAL("i am two")])
    with TestClient(h.app) as c:
        with c.websocket_connect("/voice", headers=AUTH) as w1, c.websocket_connect("/voice", headers=AUTH2) as w2:
            read(w1)
            read(w2)
            ping(w1)
            until_state(w1, "listening")
            ping(w2)
            until_state(w2, "listening")
            assert sorted(h.svc.sessions) == [1, 2]  # different devices: neither replaced the other

        def said(uid):
            msgs = c.portal.call(h.graph.aget_state, thread_config(uid, voice=True)).values["messages"]
            return [m.content for m in msgs]
        assert said(1) == ["i am one", "Hi one."] and said(2) == ["i am two", "Hi two."]
    assert ("stt", "dg-2") in h.keys and h.tenants.lock(1) is not h.tenants.lock(2)


def test_deliver_reaches_only_target_user():
    h = two_users(chat_scripts("Hi."))
    act = [{"type": "set_timer", "seconds": 60}]
    with TestClient(h.app) as c:
        with c.websocket_connect("/voice", headers=AUTH) as w1, c.websocket_connect("/voice", headers=AUTH2) as w2:
            read(w1)
            read(w2)
            assert c.portal.call(h.svc.deliver, 2, act) is True
            assert read(w2) == ("text", {"type": "client_actions", "actions": act})
            w1.send_json({"type": "nonsense"})
            assert read(w1)[1]["type"] == "error"  # user 1 got nothing before its own reply
        c.portal.call(asyncio.sleep, 0.2)
        assert c.portal.call(h.svc.deliver, 2, act) is False  # nobody connected any more
        assert c.portal.call(h.svc.deliver, 3, act) is False


def test_disabled_user_session_closed():
    h = build(chat_scripts("Hi."), stt_script=[FINAL("hello")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        del h.devices.tokens["good"]  # the user was disabled (or the device revoked) by the CLI mid-session
        ping(ws)
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(10):
                read(ws)
        assert e.value.code == 4401
        assert c.portal.call(h.graph.aget_state, thread_config(1, voice=True)).values == {}  # the graph never ran
