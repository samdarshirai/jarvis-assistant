import json
from types import SimpleNamespace

from fastapi import FastAPI, WebSocket
from langchain_core.messages import AIMessage
from starlette.websockets import WebSocketDisconnect

from jarvis.voice.stt import SttEvent
from jarvis.voice.ws import VoiceService
from tests.fakes import FakeSTT, FakeTTS
from tests.test_graph import make

AUTH = {"authorization": "Bearer good"}
FINAL = lambda t: SttEvent("final", t)  # noqa: E731
PARTIAL = lambda t: SttEvent("partial", t)  # noqa: E731


class FakeDevices:
    def __init__(self):
        self.fcm: dict[int, str] = {}

    def verify(self, token):
        return 1 if token == "good" else None

    def set_fcm(self, device_id, token):
        self.fcm[device_id] = token


def build(scripts, tools=(), stt_script=(), tts=None, stt=None, with_voice=True):
    g, audit = make(scripts, list(tools))
    tts = tts or FakeTTS()
    stt = stt or FakeSTT(stt_script)
    svc = VoiceService(g, FakeDevices(), stt if with_voice else None, tts if with_voice else None)
    app = FastAPI()

    @app.websocket("/voice")
    async def voice(ws: WebSocket):
        await svc.handle(ws)

    return SimpleNamespace(app=app, svc=svc, graph=g, audit=audit, tts=tts, stt=stt, devices=svc.devices)


def chat_scripts(*replies):
    return {"fast": [AIMessage("chat"), *[AIMessage(r) for r in replies]]}


def ping(ws, n=320):
    ws.send_bytes(b"\x00" * n)


def read(ws):
    m = ws.receive()
    if m["type"] == "websocket.close":
        raise WebSocketDisconnect(m.get("code", 1000))
    if m.get("bytes") is not None:
        return ("bytes", m["bytes"])
    return ("text", json.loads(m["text"]))


def until(ws, type_, limit=80):
    """Read frames until a text frame of this type; return everything seen (bytes frames as ('bytes', b))."""
    seen = []
    for _ in range(limit):
        item = read(ws)
        seen.append(item)
        if item[0] == "text" and item[1]["type"] == type_:
            return seen
    raise AssertionError(f"no {type_!r} frame in {seen}")


def until_state(ws, state, limit=80):
    seen = []
    for _ in range(limit):
        item = read(ws)
        seen.append(item)
        if item[0] == "text" and item[1]["type"] == "state" and item[1]["state"] == state:
            return seen
    raise AssertionError(f"no state {state!r} in {seen}")


def texts(seen, type_=None):
    return [v for k, v in seen if k == "text" and (type_ is None or v["type"] == type_)]


def audio(seen):
    return [v for k, v in seen if k == "bytes"]
