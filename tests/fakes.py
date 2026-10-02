import asyncio

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from jarvis.voice.stt import SttEvent  # noqa: F401  (re-exported for tests)


class FakeChat(BaseChatModel):
    script: list[AIMessage]
    i: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self.script[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):
        return self


class FakeProvider:
    def __init__(self, scripts: dict[str, list[AIMessage]]):
        self._models = {tier: FakeChat(script=s) for tier, s in scripts.items()}

    def get(self, tier, tools=None):
        return self._models[tier]


class MemoryAudit:
    def __init__(self):
        self.records: list[dict] = []

    def record(self, kind, name, **kw):
        self.records.append({"kind": kind, "name": name, **kw})


class FakeSTTStream:
    def __init__(self, script, fail_events=False):
        self.script, self.fail_events = script, fail_events
        self.q: asyncio.Queue = asyncio.Queue()
        self.received: list[bytes] = []
        self.closed = False

    async def send(self, pcm):
        self.received.append(pcm)
        ev = self.script.pop(0) if self.script else None  # one scripted event (or None) per audio frame
        if ev is not None:
            self.q.put_nowait(ev)

    async def events(self):
        if self.fail_events:
            raise RuntimeError("stt down")
        while True:
            ev = await self.q.get()
            if ev is None:
                return
            yield ev

    async def close(self):
        self.closed = True
        self.q.put_nowait(None)


class FakeSTT:
    def __init__(self, script=(), fail_events=False, fail_open=False):
        self.script, self.fail_events, self.fail_open = list(script), fail_events, fail_open
        self.streams: list[FakeSTTStream] = []

    async def open(self):
        if self.fail_open:
            raise RuntimeError("cannot open stt")
        s = FakeSTTStream(self.script, self.fail_events)
        self.streams.append(s)
        return s


class FakeTTS:
    def __init__(self, stall=False, fail=False):
        self.stall, self.fail = stall, fail
        self.spoken: list[str] = []
        self.cancelled = False

    async def synth(self, sentences):
        try:
            for s in sentences:
                if self.fail:
                    raise RuntimeError("tts down")
                self.spoken.append(s)
                yield f"audio:{s}".encode()
                if self.stall:
                    await asyncio.sleep(3600)  # barge-in tests: hold the stream open after the first chunk
        except asyncio.CancelledError:
            self.cancelled = True
            raise
