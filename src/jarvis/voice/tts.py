import re
from typing import AsyncIterator, Protocol, Sequence

import httpx

DEEPGRAM_SPEAK_URL = "https://api.deepgram.com/v1/speak"


def split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


class TTS(Protocol):
    def synth(self, sentences: Sequence[str]) -> AsyncIterator[bytes]: ...


class DeepgramTTS:
    def __init__(self, api_key: str, model: str = "aura-2-thalia-en"):
        self.api_key, self.model = api_key, model

    async def synth(self, sentences: Sequence[str]) -> AsyncIterator[bytes]:
        if not sentences:
            return
        params = {"model": self.model, "encoding": "linear16", "sample_rate": 16000, "container": "none"}
        async with httpx.AsyncClient(timeout=30) as http:
            async with http.stream("POST", DEEPGRAM_SPEAK_URL, params=params, json={"text": " ".join(sentences)},
                                   headers={"Authorization": f"Token {self.api_key}"}) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"deepgram tts error {r.status_code}: {(await r.aread())[:200]!r}")
                carry = b""
                async for chunk in r.aiter_bytes():
                    chunk = carry + chunk
                    cut = len(chunk) & ~1  # keep s16 samples whole across chunk boundaries
                    carry = chunk[cut:]
                    if cut:
                        yield chunk[:cut]
