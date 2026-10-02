import json

import pytest

from jarvis.voice import protocol as P
from jarvis.voice.confirm import match_confirmation


def test_frame_roundtrip_and_unicode():
    assert json.loads(P.frame("state", state="listening")) == {"type": "state", "state": "listening"}
    assert "ü" in P.frame("transcript", text="Grüße")  # ensure_ascii=False


@pytest.mark.parametrize("bad", ["", "nope", "[]", "42", '{"no_type": 1}', '{"type": 3}', "{"])
def test_parse_rejects_non_frames(bad):
    assert P.parse(bad) is None


def test_parse_accepts_object_with_string_type():
    assert P.parse('{"type": "cancel"}') == {"type": "cancel"}


def test_caps_are_the_spec_values():
    assert (P.SAMPLE_RATE, P.MAX_AUDIO_FRAME, P.MAX_SPEAK_CHARS) == (16000, 65536, 2000)
    assert P.MAX_UTTERANCE_BYTES == 60 * 16000 * 2


@pytest.mark.parametrize("text", ["yes", "Yes.", " YEAH! ", "confirm", "Do it!", "do   it"])
def test_yes_variants(text):
    assert match_confirmation(text) is True


@pytest.mark.parametrize("text", ["no", "No.", "cancel", "Stop!"])
def test_no_variants(text):
    assert match_confirmation(text) is False


@pytest.mark.parametrize("text", ["", "   ", "yes and also delete everything", "yes please", "yes yes",
                                  "no wait", "not now", "maybe", "okay", "send it", "yes, send it to bob"])
def test_anything_else_is_not_a_decision(text):
    assert match_confirmation(text) is None


def test_oversized_and_deeply_nested_text_frames_are_not_frames():
    assert P.parse('{"type": "speak", "text": "' + "x" * P.MAX_TEXT_FRAME + '"}') is None
    assert P.parse("[" * 60000 + '{"type": "x"}') is None  # RecursionError must not escape
