import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.voice import protocol as P
from tests.fakes import FakeSTT, FakeTTS
from tests.test_graph import call, tool
from tests.voice_helpers import (AUTH, FINAL, PARTIAL, build, audio, chat_scripts, ping, read, texts, until,
                                 until_state)

from langchain_core.messages import AIMessage


def test_speech_during_playback_cancels_tts_and_sends_the_partial_first():
    h = build(chat_scripts("One. Two. Three."), stt_script=[FINAL("hi"), PARTIAL("wait")], tts=FakeTTS(stall=True))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = []
        while not audio(seen):
            seen.append(read(ws))
        ping(ws)  # the partial arrives while speaking
        after = until_state(ws, "listening")
        assert h.tts.cancelled and audio(after) == []
        # the user's partial text precedes the state change so the app can flush its player
        partial_at = next(i for i, (k, v) in enumerate(after) if k == "text" and v["type"] == "transcript")
        assert partial_at == 0


def test_cancel_frame_stops_playback():
    h = build(chat_scripts("One. Two."), stt_script=[FINAL("hi")], tts=FakeTTS(stall=True))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = []
        while not audio(seen):
            seen.append(read(ws))
        ws.send_json({"type": "cancel"})
        until_state(ws, "listening")
        assert h.tts.cancelled


def test_partial_while_not_speaking_does_not_cancel_anything():
    h = build(chat_scripts("Hi."), stt_script=[PARTIAL("um")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = until(ws, "transcript")
        assert texts(seen, "transcript")[0]["final"] is False and not h.tts.cancelled


def test_executed_write_stays_audited_when_the_user_barges_in_afterwards():
    calls = []
    h = build({"fast": [AIMessage("calendar"), call("create_event", {"summary": "Gym"}), AIMessage("Created. Done.")]},
              [tool("create_event", "calendar", calls)],
              [FINAL("put gym at seven"), None, FINAL("yes"), PARTIAL("stop")], tts=FakeTTS(stall=True))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        until(ws, "confirm_card")
        ws.send_json({"type": "cancel"})  # leave the card's speech
        until_state(ws, "listening")
        ping(ws)
        ping(ws)
        seen = []
        while "Created." not in h.tts.spoken:
            seen.append(read(ws))
        ping(ws)
        until_state(ws, "listening")
        assert calls == [("create_event", {"summary": "Gym"})]
        rec = [r for r in h.audit.records if r["name"] == "create_event"]
        assert len(rec) == 1 and rec[0]["confirmation"] == "approved"


def test_dropped_socket_mid_card_leaves_the_confirmation_pending_for_other_channels():
    calls = []
    h = build({"fast": [AIMessage("calendar"), call("create_event", {"summary": "Gym"})]},
              [tool("create_event", "calendar", calls)], [FINAL("put gym at seven")])
    with TestClient(h.app) as c:
        with c.websocket_connect("/voice", headers=AUTH) as ws:
            read(ws)
            ping(ws)
            until(ws, "confirm_card")
        from jarvis.voice.ws import VOICE_CFG
        state = c.portal.call(h.graph.aget_state, VOICE_CFG)
        assert state.interrupts and calls == []
        assert h.svc.current is None


def test_oversized_frame_closes_1009():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_bytes(b"\x00" * (P.MAX_AUDIO_FRAME + 1))
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(5):
                read(ws)
        assert e.value.code == 1009


def test_utterance_cap_closes_4408(monkeypatch):
    monkeypatch.setattr(P, "MAX_UTTERANCE_BYTES", 1000)
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws, 640)
        ping(ws, 640)
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(5):
                read(ws)
        assert e.value.code == 4408


@pytest.mark.parametrize("payload", ["not json", "[]", '{"type": 3}', '{"type": "launch_missiles"}'])
def test_garbage_text_frames_get_an_error_and_the_session_survives(payload):
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_text(payload)
        assert until(ws, "error")
        ws.send_json({"type": "speak", "text": "Still alive."})
        until_state(ws, "listening")
        assert h.tts.spoken == ["Still alive."]


def test_speak_voices_text_without_running_the_graph():
    h = build(chat_scripts("never used"))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_json({"type": "speak", "text": "Good morning. Here is your brief."})
        seen = until_state(ws, "listening")
        assert audio(seen) == [b"audio:Good morning.", b"audio:Here is your brief."]
        assert h.audit.records == []


@pytest.mark.parametrize("text", ["", "   ", None, 5, "x" * (P.MAX_SPEAK_CHARS + 1)])
def test_bad_speak_text_is_an_error(text):
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_json({"type": "speak", "text": text})
        assert until(ws, "error") and h.tts.spoken == []


def test_stt_failure_during_session_sends_error_speaks_fallback_and_closes():
    h = build(chat_scripts("Hi."), stt=FakeSTT(fail_events=True))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(20):
                read(ws)
        assert e.value.code == 1011 and h.tts.spoken == ["I can't hear you right now."]


def test_stt_open_failure_sends_error_and_closes_1011():
    h = build(chat_scripts("Hi."), stt=FakeSTT(fail_open=True))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        kind, frame = read(ws)
        assert frame["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as e:
            read(ws)
        assert e.value.code == 1011


def test_tts_failure_keeps_the_transcript_reports_error_and_stays_open():
    h = build(chat_scripts("Hi there."), stt_script=[FINAL("hello")], tts=FakeTTS(fail=True))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = until_state(ws, "listening")
        t = texts(seen)
        assert {"type": "transcript", "role": "assistant", "text": "Hi there.", "final": True} in t
        assert any(x["type"] == "error" for x in t) and audio(seen) == []
        ws.send_json({"type": "speak", "text": "Again."})  # session still usable
        until(ws, "error")
