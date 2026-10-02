import wave

import pytest

from jarvis.voice.client import chunks, read_wav


def write(path, rate=16000, channels=1, width=2, frames=1600):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * frames * channels if width == 2 else b"\x01" * frames)


def test_read_wav_returns_raw_pcm(tmp_path):
    write(tmp_path / "a.wav")
    assert len(read_wav(tmp_path / "a.wav")) == 3200


@pytest.mark.parametrize("kw", [{"rate": 44100}, {"channels": 2}, {"width": 1}])
def test_read_wav_rejects_other_formats(tmp_path, kw):
    write(tmp_path / "b.wav", **kw)
    with pytest.raises(ValueError):
        read_wav(tmp_path / "b.wav")


def test_chunks_cover_the_audio_in_20ms_frames():
    pcm = b"\x00" * 1500
    out = list(chunks(pcm))
    assert [len(c) for c in out] == [640, 640, 220] and b"".join(out) == pcm
