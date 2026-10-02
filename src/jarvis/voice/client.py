import argparse
import asyncio
import json
import wave
from pathlib import Path
from typing import Iterator

import websockets

FRAME = 640  # 20 ms of 16 kHz 16-bit mono


def read_wav(path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise ValueError("need a 16 kHz mono 16-bit WAV (e.g. ffmpeg -i in.m4a -ar 16000 -ac 1 -sample_fmt s16 out.wav)")
        return w.readframes(w.getnframes())


def chunks(pcm: bytes, size: int = FRAME) -> Iterator[bytes]:
    for i in range(0, len(pcm), size):
        yield pcm[i:i + size]


async def run(url: str, token: str, wav: str, confirm: str | None, wait: float, out: str) -> None:
    pcm = read_wav(wav)
    async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {token}"}) as ws:
        sink = open(out, "wb")

        async def reader():
            async for m in ws:
                if isinstance(m, bytes):
                    sink.write(m)
                    continue
                print(m)
                f = json.loads(m)
                if f.get("type") == "confirm_card" and confirm:
                    await ws.send(json.dumps({"type": "confirm", "decision": confirm, "interrupt_id": f["interrupt_id"]}))

        task = asyncio.create_task(reader())
        for c in chunks(pcm):
            await ws.send(c)
            await asyncio.sleep(0.02)  # real-time pacing, so streaming STT endpointing behaves as on the phone
        for _ in range(100):  # 2 s of silence closes the utterance
            await ws.send(b"\x00" * FRAME)
            await asyncio.sleep(0.02)
        await asyncio.sleep(wait)
        await ws.send(json.dumps({"type": "bye"}))
        task.cancel()
        sink.close()
    print(f"Reply audio: {out}  (play: ffplay -f s16le -ar 16000 -ac 1 {out})")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m jarvis.voice.client")
    p.add_argument("url", help="e.g. wss://jarvis.example.com/voice")
    p.add_argument("token")
    p.add_argument("wav")
    p.add_argument("--confirm", choices=["yes", "no"], help="answer any confirm_card with a tap")
    p.add_argument("--wait", type=float, default=15, help="seconds to keep listening for the reply")
    p.add_argument("--out", default=str(Path("reply.pcm")))
    a = p.parse_args()
    asyncio.run(run(a.url, a.token, a.wav, a.confirm, a.wait, a.out))


if __name__ == "__main__":
    main()
