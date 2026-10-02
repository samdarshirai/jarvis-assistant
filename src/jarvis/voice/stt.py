import json
from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol

import websockets

DEEPGRAM_URL = ("wss://api.deepgram.com/v1/listen?encoding=linear16&sample_rate=16000&channels=1"
                "&interim_results=true&endpointing=300&utterance_end_ms=1000&smart_format=true")


@dataclass(frozen=True)
class SttEvent:
    kind: Literal["partial", "final"]
    text: str


class STTStream(Protocol):
    async def send(self, pcm: bytes) -> None: ...
    def events(self) -> AsyncIterator[SttEvent]: ...
    async def close(self) -> None: ...


class STT(Protocol):
    async def open(self) -> STTStream: ...


def parse_deepgram(msg: dict, buf: list[str]) -> SttEvent | None:
    """Fold Deepgram live messages into partial/final events; buf holds finalized segments of the open utterance."""
    kind = msg.get("type")
    if kind == "Results":
        alts = msg.get("channel", {}).get("alternatives") or [{}]
        text = (alts[0].get("transcript") or "").strip()
        if msg.get("is_final"):
            if text:
                buf.append(text)
            if msg.get("speech_final") and buf:
                out = " ".join(buf)
                buf.clear()
                return SttEvent("final", out)
            return None
        return SttEvent("partial", text) if text else None
    if kind == "UtteranceEnd" and buf:
        out = " ".join(buf)
        buf.clear()
        return SttEvent("final", out)
    return None


class _DeepgramStream:
    def __init__(self, ws):
        self.ws = ws
        self.buf: list[str] = []

    async def send(self, pcm: bytes) -> None:
        await self.ws.send(pcm)

    async def events(self) -> AsyncIterator[SttEvent]:
        async for raw in self.ws:
            if isinstance(raw, bytes):
                continue
            ev = parse_deepgram(json.loads(raw), self.buf)
            if ev:
                yield ev

    async def close(self) -> None:
        try:
            await self.ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass
        await self.ws.close()


class DeepgramSTT:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def open(self) -> STTStream:
        ws = await websockets.connect(DEEPGRAM_URL, additional_headers={"Authorization": f"Token {self.api_key}"})
        return _DeepgramStream(ws)
