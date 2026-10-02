import asyncio
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from starlette.websockets import WebSocketDisconnect

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


def test_voice_not_configured_closes_1013():
    h = build(chat_scripts("Hi."), with_voice=False)
    with TestClient(h.app) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/voice", headers=AUTH):
                pass
        assert e.value.code == 1013


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
        assert h.tts.spoken == ["I can't hear you right now."]
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
        assert h.svc.current is None


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
        assert h.svc.current is None


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
