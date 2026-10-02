import json

SAMPLE_RATE = 16000
MAX_AUDIO_FRAME = 64 * 1024
MAX_UTTERANCE_BYTES = 60 * SAMPLE_RATE * 2  # 60 s of 16-bit mono
MAX_SPEAK_CHARS = 2000
MAX_TEXT_FRAME = 64 * 1024  # control frames are tiny; the audio cap alone left text frames at the server's 16 MiB limit

CLOSE_REPLACED = 4000
CLOSE_UNAUTHORIZED = 4401
CLOSE_TOO_LONG = 4408
CLOSE_TOO_BIG = 1009
CLOSE_UPSTREAM = 1011
CLOSE_UNAVAILABLE = 1013


def frame(type_: str, **fields) -> str:
    return json.dumps({"type": type_, **fields}, ensure_ascii=False)


def parse(text: str) -> dict | None:
    if len(text) > MAX_TEXT_FRAME:
        return None
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError):  # RecursionError: deeply nested JSON must not kill the session
        return None
    return obj if isinstance(obj, dict) and isinstance(obj.get("type"), str) else None
