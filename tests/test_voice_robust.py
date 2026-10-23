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
        from jarvis.tenant import thread_config
        state = c.portal.call(h.graph.aget_state, thread_config(1, voice=True))
        assert state.interrupts and calls == []
        assert h.svc.sessions == {}


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
        assert e.value.code == 1011 and h.tts.spoken == ["Sorry, I can't hear you right now."]


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


def test_stt_send_failure_reports_once_speaks_fallback_and_closes():
    class DeadSend(FakeSTT):
        async def open(self):
            s = await super().open()

            async def boom(pcm):
                raise RuntimeError("stt socket died")

            s.send = boom
            return s

    h = build(chat_scripts("Hi."), stt=DeadSend())
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = []
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(20):
                seen.append(read(ws))
        assert e.value.code == 1011
        errors = [t for t in texts(seen) if t["type"] == "error"]
        assert len(errors) == 1 and h.tts.spoken == ["Sorry, I can't hear you right now."]  # failure path ran once


def test_oversized_text_frame_is_a_bad_frame_and_the_session_survives():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_text('{"type": "speak", "text": "' + "x" * P.MAX_TEXT_FRAME + '"}')
        assert until(ws, "error")
        ws.send_json({"type": "speak", "text": "Still alive."})
        until_state(ws, "listening")
        assert h.tts.spoken == ["Still alive."]


@pytest.mark.parametrize("failure", ["stt_open", "stt_events", "stt_send", "tts", "graph", "abandoned_graph"])
def test_voice_errors_never_log_upstream_exception_text(caplog, failure):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    secret, chained = "dg-voice-synthetic-secret", "synthetic-llm-key"
    try:
        raise RuntimeError(chained)
    except RuntimeError as cause:
        error = RuntimeError(secret)
        error.__cause__ = cause

    class BrokenSTT(FakeSTT):
        async def open(self):
            if failure == "stt_open":
                raise error
            stream = await super().open()
            if failure == "stt_send":
                stream.send = AsyncMock(side_effect=error)
            elif failure == "stt_events":
                async def events():
                    raise error
                    yield  # an async iterator, like the upstream stream
                stream.events = events
            return stream

    class BrokenTTS(FakeTTS):
        async def synth(self, sentences):
            raise error
            yield

    entered, release = asyncio.Event(), asyncio.Event()
    h = build(chat_scripts("Hi."), stt_script=[FINAL("hello")],
              stt=BrokenSTT([FINAL("hello")]), tts=BrokenTTS() if failure == "tts" else None)
    if failure in ("graph", "abandoned_graph"):
        async def invoke(inp, cfg):
            entered.set()
            if failure == "abandoned_graph":
                await release.wait()
            raise error
        h.rt.graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(interrupts=())), ainvoke=invoke)

    async def finish_abandoned():
        while h.svc.sessions:
            await asyncio.sleep(0.01)
        release.set()
        await asyncio.gather(*h.svc.steps, return_exceptions=True)
        await asyncio.sleep(0)  # run the completed-step logging callback

    seen = []
    with caplog.at_level("ERROR", logger="jarvis.voice.ws"), TestClient(h.app) as c:
        with c.websocket_connect("/voice", headers=AUTH) as ws:
            if failure in ("stt_open", "stt_events", "stt_send"):
                if failure == "stt_send":
                    seen.append(read(ws))
                    ping(ws)
                with pytest.raises(WebSocketDisconnect) as e:
                    for _ in range(20):
                        seen.append(read(ws))
                assert e.value.code == 1011
            else:
                seen.append(read(ws))
                ping(ws)
                if failure == "abandoned_graph":
                    c.portal.call(asyncio.wait_for, entered.wait(), 5)
                else:
                    seen.extend(until_state(ws, "listening"))
        if failure == "abandoned_graph":
            c.portal.call(asyncio.wait_for, finish_abandoned(), 5)
    records = [r for r in caplog.records if r.name == "jarvis.voice.ws"]
    assert records
    assert all(r.exc_info is None and r.stack_info is None for r in records)
    for value in (secret, chained):
        assert value not in caplog.text
        assert value not in str(seen)
        assert value not in str(h.audit.records)


def test_rejected_deepgram_tts_response_keeps_key_out_of_logs_and_socket(caplog, monkeypatch):
    import httpx
    from jarvis.voice.tts import DeepgramTTS

    secret = "dg-upstream-echo-synthetic-secret"
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": f"Rejected token {secret}"})))
    monkeypatch.setattr("jarvis.voice.tts.httpx.AsyncClient", lambda **kw: client)
    h = build(chat_scripts("Hi."), stt_script=[FINAL("hello")])
    h.tenants.s.deepgram_api_key = secret
    h.svc.tts_factory = DeepgramTTS
    with caplog.at_level("ERROR", logger="jarvis.voice.ws"), TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = until_state(ws, "listening")
    assert texts(seen, "error") == [{"type": "error", "message": "Speech output failed; the reply is in the transcript."}]
    records = [r for r in caplog.records if r.name == "jarvis.voice.ws"]
    assert records and all(r.exc_info is None for r in records)
    assert secret not in caplog.text
    assert secret not in str(seen)
    assert secret not in str(h.audit.records)
