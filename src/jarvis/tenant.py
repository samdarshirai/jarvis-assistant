"""Per-user runtime: each user's stores, Google clients, tool registry, LLM provider and graph, built on demand
and cached (LRU). Two users never share any of these objects, nor a lock or a secret."""
import asyncio
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from jarvis.agent.graph import build_graph
from jarvis.audit import Audit
from jarvis.google.auth import PgTokenStore, build_service
from jarvis.google.calendar import CalendarClient
from jarvis.google.gmail import GmailClient
from jarvis.google.tasks import TasksClient
from jarvis.llm import LLMProvider, creds_for
from jarvis.kg.block import GraphAwareProvider
from jarvis.kg.store import GraphStore
from jarvis.notes import NoteStore
from jarvis.proactive.store import ProactiveStore
from jarvis.situation import Situation, SituationAwareProvider
from jarvis.tools.calendar_tools import register_calendar_tools
from jarvis.tools.gmail_tools import register_gmail_tools
from jarvis.tools.graph_tools import register_graph_tools
from jarvis.tools.note_tools import register_note_tools
from jarvis.tools.phone_tools import register_phone_tools
from jarvis.tools.registry import Registry
from jarvis.tools.research_tools import register_research_tools
from jarvis.tools.task_tools import register_task_tools
from jarvis.users import MAX_USERS, SecretStore, User, UserStore, thread_id
from jarvis.web import DdgSearch, WebSearch

log = logging.getLogger(__name__)


def thread_config(user_id: int, *, voice: bool = False) -> dict:
    """The user's one conversation thread, shared by every channel. "voice" only selects the fast tier in the graph."""
    return {"configurable": {"thread_id": thread_id(user_id), **({"voice": True} if voice else {})}, "recursion_limit": 40}


def cached_service(build, ttl: float = 1800.0, clock=time.monotonic, epoch=lambda: 0):
    """Reuse the built Google client per thread (httplib2 is not thread-safe); rebuild after ttl so token
    refresh and ReauthRequired still surface, or when epoch() moves (new consent). Tools run in to_thread,
    hence the thread-local."""
    local = threading.local()

    def get():
        hit = getattr(local, "hit", None)
        if hit is None or clock() - hit[0] > ttl or hit[2] != epoch():
            hit = local.hit = (clock(), build(), epoch())
        return hit[1]

    return get


def build_registry(svc, tz: str, pool=None, tavily_key: str = "", user_id: int | None = None,
                   graph_store=None) -> Registry:
    registry = Registry()
    gmail = GmailClient(svc("gmail", "v1"))
    register_calendar_tools(registry, CalendarClient(svc("calendar", "v3"), tz), tz, gmail.sent_to)
    register_task_tools(registry, TasksClient(svc("tasks", "v1")))
    register_gmail_tools(registry, gmail)
    register_phone_tools(registry)
    if pool is not None:
        register_note_tools(registry, NoteStore(pool, user_id))
    if graph_store is not None:
        register_graph_tools(registry, graph_store)
    register_research_tools(registry, WebSearch(tavily_key) if tavily_key else DdgSearch())
    return registry


class UnknownUser(LookupError):
    pass


@dataclass(eq=False)
class UserRuntime:
    user: User
    secrets: dict[str, str] = field(repr=False)
    audit: Audit
    notes: NoteStore
    proactive: ProactiveStore
    calendar: CalendarClient
    tasks: TasksClient
    gmail: GmailClient
    registry: Registry
    llm: GraphAwareProvider | None
    graph: object | None  # the compiled LangGraph; None until the user sets an LLM key
    lock: asyncio.Lock
    tz: str
    google_epoch: list[int] = field(default_factory=lambda: [0], repr=False)  # bumped by invalidate()
    kg: "GraphStore | None" = None  # the user's knowledge graph

    @property
    def llm_ready(self) -> bool:
        return self.graph is not None


class Tenants:
    def __init__(self, pool, settings, saver, max_size: int = max(32, MAX_USERS), *,
                 google_service_factory=build_service, llm_provider_factory=LLMProvider):
        self.pool, self.s, self.saver, self.max_size = pool, settings, saver, max_size
        self.google_service_factory, self.llm_provider_factory = google_service_factory, llm_provider_factory
        self._cache: OrderedDict[int, UserRuntime] = OrderedDict()
        self._locks: dict[int, asyncio.Lock] = {}  # never evicted: a rebuilt runtime keeps the lock a running step holds
        self._gen: dict[int, int] = {}  # bumped by invalidate() so a build that raced it is not cached
        # ponytail: evicted and invalidated runtimes may still be in use by holders that skip the lock (scheduler,
        # owner channels), so their LLM clients close only at shutdown; one client leaks per eviction or key change.
        # With max_size >= the user cap (the default) nothing is ever evicted: runtimes retire only on invalidate().
        self._retired: list[UserRuntime] = []

    def lock(self, user_id: int) -> asyncio.Lock:
        return self._locks.setdefault(user_id, asyncio.Lock())

    async def get(self, user_id: int) -> UserRuntime:
        while True:
            rt = self._cache.get(user_id)
            if rt is not None:
                self._cache.move_to_end(user_id)
                return rt
            gen = self._gen.get(user_id, 0)
            rt = await asyncio.to_thread(self._build, user_id, self.lock(user_id))
            if self._gen.get(user_id, 0) != gen or user_id in self._cache:
                await _close(rt)  # invalidated mid-build, or a concurrent get won: use the cached/fresh one
                continue
            self._cache[user_id] = rt
            while len(self._cache) > self.max_size:
                self._retired.append(self._cache.popitem(last=False)[1])  # same as invalidate: closed at shutdown
            return rt

    def invalidate(self, user_id: int) -> None:
        """Call after the user's secrets or Google credentials change: the next get() rebuilds."""
        self._gen[user_id] = self._gen.get(user_id, 0) + 1
        rt = self._cache.pop(user_id, None)
        if rt is not None:
            rt.google_epoch[0] += 1  # anyone still holding the old runtime gets fresh Google clients
            self._retired.append(rt)

    async def close(self) -> None:
        for rt in [*self._cache.values(), *self._retired]:
            await _close(rt)
        self._cache.clear()
        self._retired.clear()

    def _build(self, user_id: int, lock: asyncio.Lock) -> UserRuntime:
        user = UserStore(self.pool).get(user_id)
        if user is None:
            raise UnknownUser(user_id)
        s, pool, tz = self.s, self.pool, user.tz
        secrets = SecretStore(pool, s.fernet_key).all(user_id)
        audit = Audit(pool, user_id)
        store = PgTokenStore(pool, user_id)
        epoch = [0]

        def svc(name: str, version: str):
            return cached_service(lambda: self.google_service_factory(name, version, store, s.fernet_key),
                                  epoch=lambda: epoch[0])

        kg = GraphStore(pool, user_id, s.fernet_key.encode())
        registry = build_registry(svc, tz, pool, s.tavily_api_key, user_id, graph_store=kg)
        creds = creds_for(secrets, s)
        raw = self.llm_provider_factory(creds, audit) if creds else None
        llm = GraphAwareProvider(raw, kg) if raw else None
        proactive = ProactiveStore(pool, user_id)
        calendar, tasks, gmail = CalendarClient(svc("calendar", "v3"), tz), TasksClient(svc("tasks", "v1")), GmailClient(svc("gmail", "v1"))
        # Only the chat graph sees the situation (proactive jobs and mail extraction use `llm`). Memory wraps it, so the
        # prompt reads: base, graph, situation: the fast-changing part comes last and the rest stays cacheable.
        chat = None
        if raw:
            chat = SituationAwareProvider(raw, Situation(calendar, tasks, gmail, proactive, tz, NoteStore(pool, user_id)))
            chat = GraphAwareProvider(chat, kg)
        graph = build_graph(chat, registry, audit, self.saver, tz) if raw else None
        return UserRuntime(user=user, secrets=secrets, audit=audit, notes=NoteStore(pool, user_id),
                           proactive=proactive, calendar=calendar, tasks=tasks, gmail=gmail,
                           registry=registry, llm=llm, graph=graph, lock=lock, tz=tz, google_epoch=epoch, kg=kg)


async def _close(rt: UserRuntime) -> None:
    if rt.llm is not None:
        try:
            await rt.llm.aclose()
        except Exception:
            log.exception("could not close the llm client for user %s", rt.user.id)


async def auth_runtime(devices, tenants: Tenants, authorization: str) -> UserRuntime | None:
    """The runtime behind a `Bearer <device token>` header; None for a missing or bad token, or a disabled or unknown user."""
    token = authorization[7:].strip() if authorization[:7].lower() == "bearer " else ""
    if not token:
        return None
    auth = await asyncio.to_thread(devices.verify, token)
    if auth is None:
        return None
    # The cached runtime's user row goes stale when the users CLI (another process) disables or re-enables someone.
    user = await asyncio.to_thread(UserStore(tenants.pool).get, auth.user_id)
    if user is None or user.disabled:
        return None
    try:
        return await tenants.get(auth.user_id)
    except UnknownUser:
        return None
