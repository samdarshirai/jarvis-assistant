import base64

import pytest

from jarvis.voice.stt import SttEvent, parse_deepgram
from jarvis.voice.tts import parse_cartesia, split_sentences


def results(text, is_final=False, speech_final=False):
    return {"type": "Results", "is_final": is_final, "speech_final": speech_final,
            "channel": {"alternatives": [{"transcript": text}]}}


def test_interim_result_is_a_partial():
    assert parse_deepgram(results("what's my"), []) == SttEvent("partial", "what's my")


def test_empty_transcripts_are_ignored():
    assert parse_deepgram(results(""), []) is None
    assert parse_deepgram(results("", is_final=True, speech_final=True), []) is None


def test_finals_accumulate_until_speech_final():
    buf: list[str] = []
    assert parse_deepgram(results("set an alarm", is_final=True), buf) is None
    assert parse_deepgram(results("for six", is_final=True, speech_final=True), buf) == SttEvent("final", "set an alarm for six")
    assert buf == []


def test_utterance_end_flushes_buffer():
    buf = ["what's my day"]
    assert parse_deepgram({"type": "UtteranceEnd"}, buf) == SttEvent("final", "what's my day")
    assert parse_deepgram({"type": "UtteranceEnd"}, buf) is None


def test_unknown_messages_are_ignored():
    assert parse_deepgram({"type": "Metadata"}, []) is None


def test_split_sentences():
    assert split_sentences("Hi. How can I help?  Fine!") == ["Hi.", "How can I help?", "Fine!"]
    assert split_sentences("no punctuation") == ["no punctuation"]
    assert split_sentences("   ") == []


def test_parse_cartesia():
    assert parse_cartesia({"type": "chunk", "data": base64.b64encode(b"\x01\x02").decode()}) == b"\x01\x02"
    assert parse_cartesia({"type": "done"}) == "done"
    assert parse_cartesia({"type": "timestamps"}) is None
    with pytest.raises(RuntimeError):
        parse_cartesia({"type": "error", "error": "bad voice"})
