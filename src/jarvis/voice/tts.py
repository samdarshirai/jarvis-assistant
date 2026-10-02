import base64
import json
import re
import uuid
from typing import AsyncIterator, Literal, Protocol, Sequence

import websockets

CARTESIA_URL = "wss://api.cartesia.ai/tts/websocket?cartesia_version=2024-11-13"


def split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def parse_cartesia(msg: dict) -> bytes | Literal["done"] | None:
    kind = msg.get("type")
    if kind == "chunk":
        return base64.b64decode(msg["data"])
    if kind == "done":
        return "done"
    if kind == "error":
        raise RuntimeError(f"cartesia error: {msg.get('error') or msg}")
    return None


class TTS(Protocol):
    def synth(self, sentences: Sequence[str]) -> AsyncIterator[bytes]: ...


class CartesiaTTS:
    def __init__(self, api_key: str, voice_id: str, model: str = "sonic-2"):
        self.api_key, self.voice_id, self.model = api_key, voice_id, model

    async def synth(self, sentences: Sequence[str]) -> AsyncIterator[bytes]:
        if not sentences:
            return
        base = {"model_id": self.model, "voice": {"mode": "id", "id": self.voice_id}, "language": "en",
                "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 16000},
                "context_id": uuid.uuid4().hex}
        async with websockets.connect(CARTESIA_URL, additional_headers={"X-API-Key": self.api_key}) as ws:
            for i, s in enumerate(sentences):  # one context, continued per sentence, closed by the last one
                await ws.send(json.dumps({**base, "transcript": s + " ", "continue": i < len(sentences) - 1}))
            async for raw in ws:
                out = parse_cartesia(json.loads(raw))
                if out == "done":
                    return
                if out:
                    yield out
