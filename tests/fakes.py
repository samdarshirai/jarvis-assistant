import asyncio
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from jarvis.conversations import IDLE, Conversation
from jarvis.voice.stt import SttEvent  # noqa: F401  (re-exported for tests)


class FakeChat(BaseChatModel):
    script: list  # AIMessage replies, or exceptions to raise
    i: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self.script[self.i]
        self.i += 1
        if isinstance(msg, Exception):  # a scripted provider failure
            raise msg
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):
        return self


class FakeProvider:
    def __init__(self, scripts: dict[str, list[AIMessage]]):
        self._models = {tier: FakeChat(script=s) for tier, s in scripts.items()}
        self.choices: list = []  # the tool_choice each agent step asked for

    def get(self, tier, tools=None, tool_choice=None):
        self.choices.append(tool_choice)
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


class MemoryConversations:
    """In-memory twin of ConversationStore (same methods and rules); list() has no legacy import."""

    def __init__(self, user_id: int, clock=lambda: datetime.now(timezone.utc)):
        self.user_id, self.clock = user_id, clock
        self.rows: dict[int, Conversation] = {}

    def open_for(self, surface):
        return next((c for c in self.rows.values() if c.surface == surface and c.closed_at is None), None)

    def current(self, surface):
        c = self.open_for(surface)
        return c if c and self.clock() - c.last_at <= IDLE else self.start(surface)

    def start(self, surface, off_record=False):
        now = self.clock()
        for c in [c for c in self.rows.values() if c.surface == surface and c.closed_at is None]:
            self.rows[c.id] = replace(c, closed_at=now)
        cid = len(self.rows) + 1
        self.rows[cid] = Conversation(cid, surface, surface, None, f"user:{self.user_id}:c:{uuid.uuid4().hex}", now, now, None)
        return self.rows[cid]

    def touch(self, conv_id, text=None):
        c = self.rows.get(conv_id)
        if c:
            self.rows[conv_id] = replace(c, last_at=self.clock(), title=c.title or (text or "").strip()[:60] or None)

    def close(self, conv_id):
        c = self.rows.get(conv_id)
        if c and c.closed_at is None:
            self.rows[conv_id] = replace(c, closed_at=self.clock())

    def get(self, conv_id):
        return self.rows.get(conv_id)

    def list(self, limit=50):
        return sorted(self.rows.values(), key=lambda c: c.last_at, reverse=True)[:limit]

    def resume(self, conv_id, surface):
        t = self.get(conv_id)
        now = self.clock()
        if t is None or (t.closed_at is None and t.surface == "voice" and now - t.last_at <= IDLE):
            return None
        for c in [c for c in self.rows.values() if c.surface == surface and c.closed_at is None and c.id != conv_id]:
            self.rows[c.id] = replace(c, closed_at=now)
        self.rows[conv_id] = replace(t, surface=surface, closed_at=None, last_at=now)
        return self.rows[conv_id]


class FakeTenants:
    """Tenants twin: get(uid) returns a minimal runtime (user, secrets, graph, lock); lock(uid) is that runtime's lock."""

    def __init__(self, runtimes: dict | None = None):
        self.runtimes: dict[int, SimpleNamespace] = runtimes if runtimes is not None else {}
        self.s = SimpleNamespace(deepgram_api_key="", tavily_api_key="")
        for uid, rt in self.runtimes.items():
            if not hasattr(rt, "conversations"):
                rt.conversations = MemoryConversations(uid)
        self._users = {uid: rt.user for uid, rt in self.runtimes.items()}
        self.users = SimpleNamespace(get=self._users.get)
        self._locks: dict[int, asyncio.Lock] = {
            uid: getattr(rt, "lock", None) or asyncio.Lock() for uid, rt in self.runtimes.items()
        }

    def add(self, uid, graph, secrets=None, disabled=False):
        rt = SimpleNamespace(user=SimpleNamespace(id=uid, disabled=disabled), secrets=dict(secrets or {}),
                             graph=graph, lock=self.lock(uid), conversations=MemoryConversations(uid))
        self.runtimes[uid] = rt
        self._users[uid] = SimpleNamespace(id=uid, disabled=disabled)
        return rt

    def lock(self, uid):
        return self._locks.setdefault(uid, asyncio.Lock())

    async def get(self, uid):
        from jarvis.tenant import UnknownUser
        if uid not in self.runtimes:
            raise UnknownUser(uid)
        return self.runtimes[uid]


class FakeProactiveStore:
    """In-memory twin of ProactiveStore (same methods) for job tests."""

    def __init__(self, user_id=None):
        self.mail: dict[str, tuple] = {}
        self.alerts: set[str] = set()
        self.autos: dict[str, str] = {}
        self.state: dict[str, str] = {}

    def mail_seen(self, message_id):
        return message_id in self.mail

    def record_mail(self, message_id, outcome, event_id=None):
        self.mail.setdefault(message_id, (outcome, event_id))

    def claim_alert(self, key):
        if key in self.alerts:
            return False
        self.alerts.add(key)
        return True

    def add_auto_event(self, event_id, message_id):
        self.autos.setdefault(event_id, message_id)

    def is_auto_event(self, event_id):
        return event_id in self.autos

    def remove_auto_event(self, event_id):
        return self.autos.pop(event_id, None) is not None

    def auto_events_last_day(self):
        return len(self.autos)

    def get_state(self, key):
        return self.state.get(key)

    def set_state(self, key, value):
        self.state[key] = value


class FakeNotifier:
    """Records what the jobs would have sent: (kind, text, undo_event_id)."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def telegram(self, text, undo_event_id=None):
        self.calls.append(("telegram", text, undo_event_id))

    async def push(self, text):
        self.calls.append(("push", text, None))
        return 1

    async def push_data(self, title, body, data):
        self.calls.append(("push_data", title, body, data))
        return 1

    async def both(self, text, undo_event_id=None):
        await self.telegram(text, undo_event_id)
        await self.push(text)


class FakeUsers:
    """UserStore twin: enabled() is re-read on every tick, like the real one."""

    def __init__(self, users):
        self.users = list(users)

    def enabled(self):
        return [u for u in self.users if not u.disabled]
