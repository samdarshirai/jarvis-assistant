# Jarvis Agent Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Telegram-driven assistant that manages Google Calendar and Google Tasks through a router plus per-domain agents, where every side-effect action waits for an explicit Confirm tap.

**Architecture:** One LangGraph parent graph: `router -> agent -> gate -> tools -> agent ... -> advance`. The router picks an ordered list of domains (`calendar`, `tasks`, `chat`). A domain agent is a prompt plus a tool subset. The gate is a graph node that calls `interrupt()` for any tool tagged `needs_confirm`; tools run only after it clears. State is checkpointed to Postgres, keyed by user. Telegram long-polling runs inside the FastAPI process.

**Tech Stack:** Python 3.11+, FastAPI, LangGraph (+ `langgraph-checkpoint-postgres`), `langchain-openai` against OpenRouter, `python-telegram-bot` 21+, Postgres 16 (psycopg 3), Google API client + `google-auth-oauthlib`, `cryptography` (Fernet), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-01-jarvis-agent-core-design.md` (PRD: https://claude.ai/artifact/RV9NmxXNr4FJJGE9KZM5Vj)

## Deviations and gaps from the spec (read first)

1. **Domain agents are nodes, not compiled subgraphs.** One `agent` node picks the current domain's prompt and tool subset from `state["domains"][state["idx"]]`. Prompt and tool isolation are the same as the spec. It avoids interrupt propagation through nested graphs. The gate stays in the parent graph.
2. **30-day transcript retention is not implemented.** The LangGraph Postgres checkpointer has no TTL, and one thread per user means deleting by age would wipe the live conversation. This plan bounds model context to the last 40 messages and purges `audit_log` at 90 days. A real transcript purge needs its own design (follow-up).
3. **PRD scenario 7 is internally inconsistent.** "Evening in India" (IST 18:00-21:00) is 14:30-17:30 in Berlin in summer, so it can never be "before 09:00 CET". The slot finder takes arbitrary windows and returns `[]` when they do not intersect (tested). Ask the PRD owner what was meant before the manual acceptance pass.
4. **Client actions are plumbing only.** The state carries `client_actions` and the tools node collects any `client_action` key from a tool result. No tool in this sub-project produces one (the alarm tool arrives with the Pixel app).

## Global Constraints

- Stack: Python, FastAPI, LangGraph. Telegram via long-polling for now.
- LLM via OpenRouter through `ChatOpenAI`, base URL `https://openrouter.ai/api/v1`, two tiers (fast, strong), fallback list.
- Every OpenRouter request sets `data_collection: "deny"` and `require_parameters: true`.
- Default time zone `Europe/Berlin`. Relative dates are resolved against the user's local time injected into the prompt.
- Google scopes: Calendar and Tasks only. OAuth app set to "In production". Refresh token encrypted at rest.
- Confirmation gate is enforced in the graph (`interrupt()`), never in the prompt. Untagged tools default to `needs_confirm`.
- Telegram accepts messages only from the owner chat ID; others are dropped silently and logged.
- Every tool call and LLM call is logged (latency, model, tokens, cost). Audit log kept 90 days.
- LLM budget under 20 EUR per month; total running cost under 40 EUR per month.
- No secrets in the repo. API keys in environment variables.

## Review Focus

Failure modes the spec implies but a first draft would miss. Each has a pinned test in the owning task.

1. A text message arrives while a confirmation is pending: reply "Confirm or cancel the pending action first", never start a new run (Task 9).
2. Confirm button tapped twice: the action runs once, the second tap gets "Already handled." (Task 9).
3. Model sends a naive, unparsable, or end-before-start datetime: the tool returns an error to the model and no Google call is made (Task 5).
4. Free-slot search where the time windows do not intersect (scenario 7) or the range is empty: return `[]`, not an error or a bad slot (Task 5).
5. History window cuts between an assistant tool call and its tool result: the window must never start on an orphan tool message (Task 8).

## File Structure

```
pyproject.toml, docker-compose.yml, .env.example, .gitignore, ACCEPTANCE.md
src/jarvis/
  config.py            Settings from env (JARVIS_ prefix)
  timeutil.py          now_local, parse_dt
  db.py                schema, pool
  audit.py             Audit: record(), purge()
  llm.py               LLMProvider (tiers, fallbacks, OpenRouter params), AuditCallback
  main.py              FastAPI app, wiring, lifespan
  google/auth.py       encrypted token store, load_credentials, consent CLI
  google/calendar.py   CalendarClient (+ free_slots in slots.py)
  google/slots.py      pure free-slot computation
  google/tasks.py      TasksClient
  tools/registry.py    Tool, Registry
  tools/calendar_tools.py, tools/task_tools.py   arg schemas + registration
  agent/domains.py     Domain prompts and tiers
  agent/graph.py       State, router, agent, gate, tools, build_graph, turn_replies
  channels/telegram.py TelegramChannel
tests/                 mirrors src; tests/fakes.py has FakeChat, FakeProvider, MemoryAudit
```

---

### Task 1: Scaffold, config, time helpers

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `docker-compose.yml`, `src/jarvis/__init__.py`, `src/jarvis/config.py`, `src/jarvis/timeutil.py`, `tests/__init__.py`, `tests/test_config.py`, `tests/test_timeutil.py`

**Interfaces:**
- Produces: `Settings` (fields below), `get_settings() -> Settings`, `Settings.models(tier: str) -> list[str]`, `now_local(tz: str) -> datetime`, `parse_dt(value: str, tz: str) -> datetime` (raises `ValueError`).

- [ ] **Step 1: Create project files**

`pyproject.toml`:
```toml
[project]
name = "jarvis"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115", "uvicorn>=0.30",
  "langgraph>=0.6", "langgraph-checkpoint-postgres>=2.0",
  "langchain-core>=0.3", "langchain-openai>=0.3",
  "python-telegram-bot>=21",
  "psycopg[binary,pool]>=3.2",
  "google-api-python-client>=2.140", "google-auth-oauthlib>=1.2",
  "cryptography>=43", "pydantic>=2.8", "pydantic-settings>=2.4", "tzdata",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`.gitignore`:
```
.venv/
__pycache__/
.env
client_secret.json
*.egg-info/
```

`.env.example`:
```
JARVIS_OPENROUTER_API_KEY=
# comma-separated OpenRouter model ids, first is primary, rest are fallbacks; choose after scenario evaluation
JARVIS_MODELS_FAST=
JARVIS_MODELS_STRONG=
JARVIS_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis
JARVIS_TELEGRAM_BOT_TOKEN=
JARVIS_TELEGRAM_OWNER_CHAT_ID=
# generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
JARVIS_FERNET_KEY=
JARVIS_GOOGLE_CLIENT_SECRETS=client_secret.json
JARVIS_TIMEZONE=Europe/Berlin
```

`docker-compose.yml`:
```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: jarvis
      POSTGRES_PASSWORD: jarvis
      POSTGRES_DB: jarvis
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
volumes:
  pgdata:
```

`src/jarvis/__init__.py`: empty. `tests/__init__.py`: empty.

- [ ] **Step 2: Write failing tests**

`tests/test_config.py`:
```python
from jarvis.config import Settings


def make(**over):
    base = dict(
        openrouter_api_key="k", models_fast="a/x, b/y", models_strong="c/z",
        database_url="postgresql://x", telegram_bot_token="t",
        telegram_owner_chat_id=42, fernet_key="f",
    )
    return Settings(_env_file=None, **{**base, **over})


def test_models_split_and_trim():
    s = make()
    assert s.models("fast") == ["a/x", "b/y"]
    assert s.models("strong") == ["c/z"]


def test_defaults():
    s = make()
    assert s.timezone == "Europe/Berlin"
    assert s.google_client_secrets == "client_secret.json"
```

`tests/test_timeutil.py`:
```python
import pytest
from jarvis.timeutil import parse_dt, now_local


def test_naive_gets_local_zone_with_dst():
    assert parse_dt("2026-07-01T09:00", "Europe/Berlin").isoformat() == "2026-07-01T09:00:00+02:00"
    assert parse_dt("2026-01-15T09:00", "Europe/Berlin").isoformat() == "2026-01-15T09:00:00+01:00"


def test_explicit_offset_preserved():
    assert parse_dt("2026-07-01T09:00:00Z", "Europe/Berlin").utcoffset().total_seconds() == 0


def test_garbage_raises_value_error():
    with pytest.raises(ValueError):
        parse_dt("next friday", "Europe/Berlin")


def test_now_local_is_aware():
    assert now_local("Europe/Berlin").tzinfo is not None
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && pytest tests/test_config.py tests/test_timeutil.py -v`
Expected: FAIL with `ModuleNotFoundError: jarvis.config`

- [ ] **Step 4: Implement**

`src/jarvis/config.py`:
```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="JARVIS_", extra="ignore")

    openrouter_api_key: str
    models_fast: str
    models_strong: str
    database_url: str
    telegram_bot_token: str
    telegram_owner_chat_id: int
    fernet_key: str
    google_client_secrets: str = "client_secret.json"
    timezone: str = "Europe/Berlin"

    def models(self, tier: str) -> list[str]:
        raw = {"fast": self.models_fast, "strong": self.models_strong}[tier]
        return [m.strip() for m in raw.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`src/jarvis/timeutil.py`:
```python
from datetime import datetime
from zoneinfo import ZoneInfo


def now_local(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def parse_dt(value: str, tz: str) -> datetime:
    """ISO 8601 -> aware datetime. Naive input is read as local time in `tz`."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt
```

- [ ] **Step 5: Run tests, commit**

Run: `pytest tests/test_config.py tests/test_timeutil.py -v` — Expected: 6 PASS

```bash
git init
git add -A
git commit -m "chore: scaffold project, config, time helpers"
```

---

### Task 2: Postgres schema and audit log

**Files:**
- Create: `src/jarvis/db.py`, `src/jarvis/audit.py`, `tests/conftest.py`, `tests/test_audit.py`

**Interfaces:**
- Produces: `make_pool(url: str) -> ConnectionPool`, `init_schema(pool) -> None`, `Audit(pool)` with `record(kind: str, name: str, *, args=None, result=None, confirmation=None, latency_ms=None, model=None, tokens_in=None, tokens_out=None, cost_usd=None) -> None` and `purge(days: int = 90) -> int`.

Tests need Postgres: `docker compose up -d db`, then `export TEST_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis_test`. Without it these tests skip.

- [ ] **Step 1: Write failing tests**

`tests/conftest.py`:
```python
import os

import pytest


@pytest.fixture
def pool():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    from jarvis.db import init_schema, make_pool

    p = make_pool(url)
    init_schema(p)
    with p.connection() as c:
        c.execute("TRUNCATE audit_log, oauth_tokens")
    yield p
    p.close()
```

`tests/test_audit.py`:
```python
from datetime import date

from jarvis.audit import Audit


def test_record_roundtrip(pool):
    a = Audit(pool)
    a.record("tool", "create_event", args={"d": date(2026, 1, 1)}, result={"ok": True},
             confirmation="approved", latency_ms=12)
    with pool.connection() as c:
        row = c.execute("SELECT kind, name, args, confirmation, latency_ms FROM audit_log").fetchone()
    assert row == ("tool", "create_event", {"d": "2026-01-01"}, "approved", 12)


def test_purge_removes_only_old_rows(pool):
    a = Audit(pool)
    a.record("tool", "new")
    a.record("tool", "old")
    with pool.connection() as c:
        c.execute("UPDATE audit_log SET ts = now() - interval '91 days' WHERE name = 'old'")
    assert a.purge(90) == 1
    with pool.connection() as c:
        assert c.execute("SELECT name FROM audit_log").fetchall() == [("new",)]
```

- [ ] **Step 2: Run to verify failure**

Run: `TEST_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis_test pytest tests/test_audit.py -v`
Expected: FAIL with `ModuleNotFoundError: jarvis.db`

- [ ] **Step 3: Implement**

`src/jarvis/db.py`:
```python
from psycopg_pool import ConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  args JSONB,
  result JSONB,
  confirmation TEXT,
  latency_ms INTEGER,
  model TEXT,
  tokens_in INTEGER,
  tokens_out INTEGER,
  cost_usd NUMERIC
);
CREATE TABLE IF NOT EXISTS oauth_tokens (
  provider TEXT PRIMARY KEY,
  blob BYTEA NOT NULL
);
"""


def make_pool(url: str) -> ConnectionPool:
    return ConnectionPool(url, min_size=1, max_size=5, open=True)


def init_schema(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute(SCHEMA)
```

`src/jarvis/audit.py`:
```python
import json

from psycopg.types.json import Jsonb


def _dumps(o) -> str:
    return json.dumps(o, default=str)


def _j(v):
    return None if v is None else Jsonb(v, dumps=_dumps)


class Audit:
    def __init__(self, pool):
        self.pool = pool

    def record(self, kind, name, *, args=None, result=None, confirmation=None, latency_ms=None,
               model=None, tokens_in=None, tokens_out=None, cost_usd=None) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO audit_log (kind, name, args, result, confirmation, latency_ms, model,"
                " tokens_in, tokens_out, cost_usd) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (kind, name, _j(args), _j(result), confirmation, latency_ms, model,
                 tokens_in, tokens_out, cost_usd),
            )

    def purge(self, days: int = 90) -> int:
        with self.pool.connection() as conn:
            cur = conn.execute("DELETE FROM audit_log WHERE ts < now() - make_interval(days => %s)", (days,))
            return cur.rowcount
```

- [ ] **Step 4: Run tests, commit**

Run: `TEST_DATABASE_URL=... pytest tests/test_audit.py -v` — Expected: 2 PASS

```bash
git add -A && git commit -m "feat: postgres schema and audit log"
```

---

### Task 3: Tool registry with confirmation tags

**Files:**
- Create: `src/jarvis/tools/__init__.py`, `src/jarvis/tools/registry.py`, `tests/test_registry.py`

**Interfaces:**
- Produces: `Tool(name, domain, description, args_schema: type[BaseModel], fn: Callable[..., Any], needs_confirm: bool = True)` (frozen dataclass); `Registry` with `add(tool)`, `get(name) -> Tool | None`, `needs_confirm(name) -> bool` (True for unknown names), `for_domain(domain) -> list[Tool]`, `lc_tools(domain) -> list[StructuredTool]`.

- [ ] **Step 1: Write failing tests**

`tests/test_registry.py`:
```python
import pytest
from pydantic import BaseModel

from jarvis.tools.registry import Registry, Tool


class Args(BaseModel):
    x: int = 0


def tool(name, domain="calendar", **kw):
    return Tool(name=name, domain=domain, description="d", args_schema=Args, fn=lambda **k: k, **kw)


def test_needs_confirm_defaults_true():
    r = Registry()
    r.add(tool("write_something"))
    assert r.needs_confirm("write_something") is True


def test_explicit_read_tool_skips_confirm():
    r = Registry()
    r.add(tool("list_things", needs_confirm=False))
    assert r.needs_confirm("list_things") is False


def test_unknown_tool_needs_confirm():
    assert Registry().needs_confirm("nope") is True


def test_duplicate_name_rejected():
    r = Registry()
    r.add(tool("a"))
    with pytest.raises(ValueError):
        r.add(tool("a"))


def test_for_domain_and_lc_tools():
    r = Registry()
    r.add(tool("c1", "calendar"))
    r.add(tool("t1", "tasks"))
    assert [t.name for t in r.for_domain("tasks")] == ["t1"]
    assert [t.name for t in r.lc_tools("calendar")] == ["c1"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_registry.py -v` — Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/jarvis/tools/__init__.py`: empty.

`src/jarvis/tools/registry.py`:
```python
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel


@dataclass(frozen=True)
class Tool:
    name: str
    domain: str
    description: str
    args_schema: type[BaseModel]
    fn: Callable[..., Any]
    needs_confirm: bool = True  # safe default: a new tool confirms unless marked read-only


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def needs_confirm(self, name: str) -> bool:
        t = self.get(name)
        return True if t is None else t.needs_confirm

    def for_domain(self, domain: str) -> list[Tool]:
        return [t for t in self._tools.values() if t.domain == domain]

    def lc_tools(self, domain: str) -> list[StructuredTool]:
        # Only used to describe tools to the model; execution goes through Tool.fn in the graph.
        return [
            StructuredTool.from_function(func=t.fn, name=t.name, description=t.description,
                                         args_schema=t.args_schema)
            for t in self.for_domain(domain)
        ]
```

- [ ] **Step 4: Run tests, commit**

Run: `pytest tests/test_registry.py -v` — Expected: 5 PASS

```bash
git add -A && git commit -m "feat: tool registry with confirm-by-default"
```

---

### Task 4: Google auth with encrypted token store

**Files:**
- Create: `src/jarvis/google/__init__.py`, `src/jarvis/google/auth.py`, `tests/test_google_auth.py`

**Interfaces:**
- Produces: `SCOPES`, `ReauthRequired(Exception)`, `TokenStore` protocol (`get(provider) -> bytes | None`, `put(provider, blob)`), `PgTokenStore(pool)`, `save_credentials(store, key, creds)`, `load_credentials(store, key) -> Credentials` (raises `ReauthRequired`), `build_service(name, version, store, key)`, CLI `python -m jarvis.google.auth`.

- [ ] **Step 1: Write failing tests**

`tests/test_google_auth.py`:
```python
import json

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError

from jarvis.google import auth


class MemStore:
    def __init__(self):
        self.d = {}

    def get(self, p):
        return self.d.get(p)

    def put(self, p, b):
        self.d[p] = b


class StubCreds:
    valid = True
    fail = False

    def __init__(self, info):
        self.info = info
        if info.get("expired"):
            self.valid = False

    @classmethod
    def from_authorized_user_info(cls, info, scopes):
        return cls(info)

    def refresh(self, request):
        if self.info.get("revoked"):
            raise RefreshError("revoked")
        self.valid = True

    def to_json(self):
        return json.dumps(self.info)


@pytest.fixture
def key():
    return Fernet.generate_key().decode()


def test_missing_token_requires_reauth(key):
    with pytest.raises(auth.ReauthRequired):
        auth.load_credentials(MemStore(), key)


def test_token_encrypted_at_rest_and_roundtrips(key, monkeypatch):
    monkeypatch.setattr(auth, "Credentials", StubCreds)
    store = MemStore()
    auth.save_credentials(store, key, StubCreds({"refresh_token": "secret-rt"}))
    assert b"secret-rt" not in store.d["google"]
    assert auth.load_credentials(store, key).info["refresh_token"] == "secret-rt"


def test_revoked_refresh_requires_reauth(key, monkeypatch):
    monkeypatch.setattr(auth, "Credentials", StubCreds)
    store = MemStore()
    auth.save_credentials(store, key, StubCreds({"expired": True, "revoked": True}))
    with pytest.raises(auth.ReauthRequired):
        auth.load_credentials(store, key)


def test_scopes_are_calendar_and_tasks_only():
    assert len(auth.SCOPES) == 2
    assert all(s.endswith(("/calendar", "/tasks")) for s in auth.SCOPES)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_google_auth.py -v` — Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/jarvis/google/__init__.py`: empty.

`src/jarvis/google/auth.py`:
```python
import json
from typing import Protocol

from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]
PROVIDER = "google"


class ReauthRequired(Exception):
    """Stored Google authorization is missing, expired or revoked."""


class TokenStore(Protocol):
    def get(self, provider: str) -> bytes | None: ...
    def put(self, provider: str, blob: bytes) -> None: ...


class PgTokenStore:
    def __init__(self, pool):
        self.pool = pool

    def get(self, provider):
        with self.pool.connection() as c:
            row = c.execute("SELECT blob FROM oauth_tokens WHERE provider = %s", (provider,)).fetchone()
        return bytes(row[0]) if row else None

    def put(self, provider, blob):
        with self.pool.connection() as c:
            c.execute(
                "INSERT INTO oauth_tokens (provider, blob) VALUES (%s, %s)"
                " ON CONFLICT (provider) DO UPDATE SET blob = EXCLUDED.blob",
                (provider, blob),
            )


def save_credentials(store: TokenStore, key: str, creds) -> None:
    store.put(PROVIDER, Fernet(key).encrypt(creds.to_json().encode()))


def load_credentials(store: TokenStore, key: str):
    blob = store.get(PROVIDER)
    if blob is None:
        raise ReauthRequired("No Google authorization stored.")
    info = json.loads(Fernet(key).decrypt(blob))
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise ReauthRequired("Google authorization expired or revoked.") from e
        save_credentials(store, key, creds)
    return creds


def build_service(name: str, version: str, store: TokenStore, key: str):
    return build(name, version, credentials=load_credentials(store, key), cache_discovery=False)


def main() -> None:
    """One-time consent on localhost: python -m jarvis.google.auth"""
    from google_auth_oauthlib.flow import InstalledAppFlow

    from jarvis.config import get_settings
    from jarvis.db import init_schema, make_pool

    s = get_settings()
    pool = make_pool(s.database_url)
    init_schema(pool)
    flow = InstalledAppFlow.from_client_secrets_file(s.google_client_secrets, SCOPES)
    creds = flow.run_local_server(port=0)
    save_credentials(PgTokenStore(pool), s.fernet_key, creds)
    print("Google authorization stored.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests, commit**

Run: `pytest tests/test_google_auth.py -v` — Expected: 4 PASS

```bash
git add -A && git commit -m "feat: google oauth with encrypted token store"
```

---

### Task 5: Calendar slots, client and tools

**Files:**
- Create: `src/jarvis/google/slots.py`, `src/jarvis/google/calendar.py`, `src/jarvis/tools/calendar_tools.py`, `tests/test_slots.py`, `tests/test_calendar.py`

**Interfaces:**
- Consumes: `Tool`, `Registry` (Task 3), `parse_dt` (Task 1).
- Produces:
  - `Window(tz: str, start: time, end: time)`; `free_slots(busy, range_start, range_end, duration: timedelta, windows=(), step=timedelta(minutes=30), limit=3) -> list[tuple[datetime, datetime]]`.
  - `CalendarClient(service_factory: Callable[[], Any], tz: str)` with `list_events(start, end, query=None, limit=50) -> list[dict]`, `create_event(summary, start, end, recurrence=None) -> dict`, `update_event(event_id, scope, summary=None, start=None, end=None) -> dict`, `delete_event(event_id, scope) -> dict`, `busy(start, end) -> list[tuple[datetime, datetime]]`. `scope` is `"this"` or `"all"`.
  - `register_calendar_tools(registry, client, tz)` registers `list_events`, `find_free_slots` (read-only) and `create_event`, `update_event`, `delete_event` (confirm).

- [ ] **Step 1: Write failing slot tests**

`tests/test_slots.py`:
```python
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from jarvis.google.slots import Window, free_slots

B = ZoneInfo("Europe/Berlin")


def dt(h, m=0, day=6):
    return datetime(2026, 10, day, h, m, tzinfo=B)


def test_skips_busy_and_returns_up_to_limit():
    out = free_slots([(dt(9), dt(11))], dt(9), dt(18), timedelta(hours=1))
    assert out[0] == (dt(11), dt(12))
    assert len(out) == 3


def test_windows_must_all_hold():
    # 15:00-16:00 Berlin = 18:30-19:30 IST
    w = [Window("Asia/Kolkata", time(18), time(21)), Window("Europe/Berlin", time(12), time(18))]
    out = free_slots([], dt(8), dt(20), timedelta(hours=1), w)
    assert out and all(s.hour >= 14 for s, _ in out)
    assert out[0][0] == dt(14, 30)


def test_empty_when_windows_do_not_intersect():
    # scenario 7 as written: IST evening and before 09:00 Berlin never overlap
    w = [Window("Asia/Kolkata", time(18), time(21)), Window("Europe/Berlin", time(0), time(9))]
    assert free_slots([], dt(0), dt(23), timedelta(hours=1), w) == []


def test_empty_range_returns_empty():
    assert free_slots([], dt(10), dt(10), timedelta(hours=1)) == []


def test_dst_change_day_is_handled():
    # 2026-10-25 Berlin falls back at 03:00; slots stay aligned to local half hours
    out = free_slots([], datetime(2026, 10, 25, 1, tzinfo=B), datetime(2026, 10, 25, 6, tzinfo=B),
                     timedelta(hours=1))
    assert out and all(s.minute in (0, 30) for s, _ in out)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_slots.py -v` — Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement slots**

`src/jarvis/google/slots.py`:
```python
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Window:
    """Slot must lie wholly inside [start, end] local time in `tz`, on one calendar day there."""
    tz: str
    start: time
    end: time


def _ceil(t: datetime, step: timedelta) -> datetime:
    base = t.replace(minute=0, second=0, microsecond=0)
    n = -(-(t - base) // step)  # ceiling division on timedeltas
    return base + n * step


def _in_window(start: datetime, end: datetime, w: Window) -> bool:
    z = ZoneInfo(w.tz)
    s, e = start.astimezone(z), end.astimezone(z)
    return s.date() == e.date() and w.start <= s.time() and e.time() <= w.end


def free_slots(busy, range_start, range_end, duration, windows=(), step=timedelta(minutes=30), limit=3):
    out = []
    t = _ceil(range_start, step)
    while t + duration <= range_end and len(out) < limit:
        end = t + duration
        clash = any(b0 < end and t < b1 for b0, b1 in busy)
        if not clash and all(_in_window(t, end, w) for w in windows):
            out.append((t, end))
            t = end  # next suggestion must not overlap this one
        else:
            t += step
    return out
```

- [ ] **Step 4: Run slot tests**

Run: `pytest tests/test_slots.py -v` — Expected: 5 PASS. If `test_dst_change_day_is_handled` fails on an ambiguous 02:30, fix `_ceil` to round in UTC offset-aware arithmetic, not by weakening the test.

- [ ] **Step 5: Write failing client and tool tests**

`tests/test_calendar.py`:
```python
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from jarvis.google.calendar import CalendarClient
from jarvis.tools.calendar_tools import register_calendar_tools
from jarvis.tools.registry import Registry

TZ = "Europe/Berlin"
B = ZoneInfo(TZ)


def client(svc=None):
    svc = svc or MagicMock()
    return CalendarClient(lambda: svc, TZ), svc


def test_list_events_slims_results():
    c, svc = client()
    svc.events.return_value.list.return_value.execute.return_value = {"items": [
        {"id": "e1", "summary": "Gym", "start": {"dateTime": "2026-10-06T07:00:00+02:00"},
         "end": {"dateTime": "2026-10-06T08:00:00+02:00"}, "recurringEventId": "r1", "etag": "junk"}]}
    out = c.list_events(datetime(2026, 10, 6, tzinfo=B), datetime(2026, 10, 7, tzinfo=B))
    assert out == [{"id": "e1", "recurring_event_id": "r1", "summary": "Gym",
                    "start": "2026-10-06T07:00:00+02:00", "end": "2026-10-06T08:00:00+02:00",
                    "location": None}]
    kw = svc.events.return_value.list.call_args.kwargs
    assert kw["singleEvents"] is True and kw["calendarId"] == "primary"


def test_update_scope_this_targets_instance_all_targets_series():
    c, svc = client()
    svc.events.return_value.get.return_value.execute.return_value = {"id": "i1", "recurringEventId": "r1"}
    svc.events.return_value.patch.return_value.execute.return_value = {"id": "x"}
    c.update_event("i1", "this", summary="A")
    assert svc.events.return_value.patch.call_args.kwargs["eventId"] == "i1"
    c.update_event("i1", "all", summary="A")
    assert svc.events.return_value.patch.call_args.kwargs["eventId"] == "r1"


def test_delete_scope_all_on_non_recurring_uses_own_id():
    c, svc = client()
    svc.events.return_value.get.return_value.execute.return_value = {"id": "e9"}
    c.delete_event("e9", "all")
    assert svc.events.return_value.delete.call_args.kwargs["eventId"] == "e9"


def test_busy_parses_freebusy():
    c, svc = client()
    svc.freebusy.return_value.query.return_value.execute.return_value = {"calendars": {"primary": {
        "busy": [{"start": "2026-10-06T08:00:00Z", "end": "2026-10-06T09:00:00Z"}]}}}
    out = c.busy(datetime(2026, 10, 6, tzinfo=B), datetime(2026, 10, 7, tzinfo=B))
    assert out[0][0].hour == 8 and out[0][0].utcoffset().total_seconds() == 0


def make_tools():
    c, svc = client()
    r = Registry()
    register_calendar_tools(r, c, TZ)
    return r, svc


def test_confirm_tags():
    r, _ = make_tools()
    assert {t.name for t in r.for_domain("calendar") if not t.needs_confirm} == {"list_events", "find_free_slots"}
    for n in ("create_event", "update_event", "delete_event"):
        assert r.needs_confirm(n)


def test_create_event_rejects_end_before_start_without_calling_google():
    r, svc = make_tools()
    with pytest.raises(ValueError):
        r.get("create_event").fn(summary="x", start="2026-10-06T10:00", end="2026-10-06T09:00")
    svc.events.return_value.insert.assert_not_called()


def test_create_event_rejects_unparsable_datetime():
    r, svc = make_tools()
    with pytest.raises(ValueError):
        r.get("create_event").fn(summary="x", start="tomorrow", end="2026-10-06T09:00")
    svc.events.assert_not_called()


def test_find_free_slots_no_intersection_returns_empty_list():
    r, svc = make_tools()
    svc.freebusy.return_value.query.return_value.execute.return_value = {"calendars": {"primary": {"busy": []}}}
    out = r.get("find_free_slots").fn(
        duration_minutes=60, range_start="2026-10-06T00:00", range_end="2026-10-06T23:00",
        windows=[{"tz": "Asia/Kolkata", "start": "18:00", "end": "21:00"},
                 {"tz": "Europe/Berlin", "start": "00:00", "end": "09:00"}])
    assert out == []
```

- [ ] **Step 6: Run to verify failure**

Run: `pytest tests/test_calendar.py -v` — Expected: FAIL `ModuleNotFoundError: jarvis.google.calendar`

- [ ] **Step 7: Implement client and tools**

`src/jarvis/google/calendar.py`:
```python
from datetime import datetime
from typing import Any, Callable


def _slim(e: dict) -> dict:
    s, en = e.get("start", {}), e.get("end", {})
    return {
        "id": e["id"],
        "recurring_event_id": e.get("recurringEventId"),
        "summary": e.get("summary"),
        "start": s.get("dateTime") or s.get("date"),
        "end": en.get("dateTime") or en.get("date"),
        "location": e.get("location"),
    }


class CalendarClient:
    def __init__(self, service_factory: Callable[[], Any], tz: str):
        self._svc = service_factory
        self.tz = tz

    def _when(self, dt: datetime) -> dict:
        return {"dateTime": dt.isoformat(), "timeZone": self.tz}

    def _target(self, event_id: str, scope: str) -> str:
        if scope == "this":
            return event_id
        ev = self._svc().events().get(calendarId="primary", eventId=event_id).execute()
        return ev.get("recurringEventId", event_id)

    def list_events(self, start: datetime, end: datetime, query: str | None = None, limit: int = 50) -> list[dict]:
        resp = self._svc().events().list(
            calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(), q=query,
            singleEvents=True, orderBy="startTime", maxResults=limit).execute()
        return [_slim(e) for e in resp.get("items", [])]

    def create_event(self, summary, start, end, recurrence: list[str] | None = None) -> dict:
        body = {"summary": summary, "start": self._when(start), "end": self._when(end)}
        if recurrence:
            body["recurrence"] = recurrence
        return _slim(self._svc().events().insert(calendarId="primary", body=body).execute())

    def update_event(self, event_id, scope, summary=None, start=None, end=None) -> dict:
        body: dict = {}
        if summary is not None:
            body["summary"] = summary
        if start is not None:
            body["start"] = self._when(start)
        if end is not None:
            body["end"] = self._when(end)
        target = self._target(event_id, scope)
        return _slim(self._svc().events().patch(calendarId="primary", eventId=target, body=body).execute())

    def delete_event(self, event_id, scope) -> dict:
        target = self._target(event_id, scope)
        self._svc().events().delete(calendarId="primary", eventId=target).execute()
        return {"deleted": target}

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        resp = self._svc().freebusy().query(body={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": "primary"}]}).execute()
        return [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
                for b in resp["calendars"]["primary"]["busy"]]
```

`src/jarvis/tools/calendar_tools.py`:
```python
from datetime import time, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from jarvis.google.slots import Window, free_slots
from jarvis.timeutil import parse_dt
from jarvis.tools.registry import Registry, Tool

DT = "ISO 8601 date-time. No offset means the user's local time."
Scope = Literal["this", "all"]


class ListEventsArgs(BaseModel):
    start: str = Field(description=DT)
    end: str = Field(description=DT)
    query: str | None = None


class CreateEventArgs(BaseModel):
    summary: str
    start: str = Field(description=DT)
    end: str = Field(description=DT)
    recurrence: list[str] | None = Field(default=None, description="RRULE strings, e.g. ['RRULE:FREQ=WEEKLY']")


class UpdateEventArgs(BaseModel):
    event_id: str
    scope: Scope = Field(description="'this' = one occurrence, 'all' = whole series")
    summary: str | None = None
    start: str | None = Field(default=None, description=DT)
    end: str | None = Field(default=None, description=DT)


class DeleteEventArgs(BaseModel):
    event_id: str
    scope: Scope


class WindowArg(BaseModel):
    tz: str = Field(description="IANA zone, e.g. Asia/Kolkata")
    start: str = Field(description="HH:MM")
    end: str = Field(description="HH:MM")


class FreeSlotsArgs(BaseModel):
    duration_minutes: int = Field(gt=0)
    range_start: str = Field(description=DT)
    range_end: str = Field(description=DT)
    windows: list[WindowArg] = Field(default_factory=list,
                                     description="Slot must fit inside every window (local time in its zone)")


def register_calendar_tools(registry: Registry, client, tz: str) -> None:
    def span(start: str, end: str):
        s, e = parse_dt(start, tz), parse_dt(end, tz)
        if e <= s:
            raise ValueError("end must be after start")
        return s, e

    def list_events(start, end, query=None):
        return client.list_events(*span(start, end), query=query)

    def create_event(summary, start, end, recurrence=None):
        return client.create_event(summary, *span(start, end), recurrence=recurrence)

    def update_event(event_id, scope, summary=None, start=None, end=None):
        s = parse_dt(start, tz) if start else None
        e = parse_dt(end, tz) if end else None
        if s and e and e <= s:
            raise ValueError("end must be after start")
        return client.update_event(event_id, scope, summary=summary, start=s, end=e)

    def delete_event(event_id, scope):
        return client.delete_event(event_id, scope)

    def find_free_slots(duration_minutes, range_start, range_end, windows=()):
        s, e = span(range_start, range_end)
        ws = [Window(w["tz"], time.fromisoformat(w["start"]), time.fromisoformat(w["end"]))
              for w in (x if isinstance(x, dict) else x.model_dump() for x in windows)]
        slots = free_slots(client.busy(s, e), s, e, timedelta(minutes=duration_minutes), ws)
        return [{"start": a.astimezone(s.tzinfo).isoformat(), "end": b.astimezone(s.tzinfo).isoformat()}
                for a, b in slots]

    for name, desc, schema, fn, confirm in [
        ("list_events", "List or search calendar events in a date range.", ListEventsArgs, list_events, False),
        ("find_free_slots", "Find free meeting slots, optionally constrained by time windows in several zones.",
         FreeSlotsArgs, find_free_slots, False),
        ("create_event", "Create a calendar event (optionally recurring).", CreateEventArgs, create_event, True),
        ("update_event", "Change or move an event or one occurrence of a recurring event.",
         UpdateEventArgs, update_event, True),
        ("delete_event", "Delete an event or a whole recurring series.", DeleteEventArgs, delete_event, True),
    ]:
        registry.add(Tool(name=name, domain="calendar", description=desc, args_schema=schema,
                          fn=fn, needs_confirm=confirm))
```

- [ ] **Step 8: Run tests, commit**

Run: `pytest tests/test_slots.py tests/test_calendar.py -v` — Expected: all PASS

```bash
git add -A && git commit -m "feat: calendar client, free-slot search, calendar tools"
```

---

### Task 6: Tasks client and tools

**Files:**
- Create: `src/jarvis/google/tasks.py`, `src/jarvis/tools/task_tools.py`, `tests/test_tasks.py`

**Interfaces:**
- Consumes: `Tool`, `Registry`.
- Produces: `TasksClient(service_factory)` with `list_tasks(include_completed=False) -> list[dict]`, `create_task(title, due: date | None=None) -> dict`, `complete_task(task_id) -> dict`, `reschedule_task(task_id, due: date) -> dict`; `register_task_tools(registry, client)` registers `list_tasks` (read-only) and `create_task`, `complete_task`, `reschedule_task` (confirm). Due dates are `YYYY-MM-DD` strings in tool args.

- [ ] **Step 1: Write failing tests**

`tests/test_tasks.py`:
```python
from datetime import date
from unittest.mock import MagicMock

import pytest

from jarvis.google.tasks import TasksClient
from jarvis.tools.registry import Registry
from jarvis.tools.task_tools import register_task_tools


def setup():
    svc = MagicMock()
    c = TasksClient(lambda: svc)
    r = Registry()
    register_task_tools(r, c)
    return r, svc


def test_list_tasks_slims():
    r, svc = setup()
    svc.tasks.return_value.list.return_value.execute.return_value = {"items": [
        {"id": "t1", "title": "Call mum", "due": "2026-10-02T00:00:00.000Z", "status": "needsAction", "etag": "x"}]}
    out = r.get("list_tasks").fn()
    assert out == [{"id": "t1", "title": "Call mum", "due": "2026-10-02", "status": "needsAction"}]
    assert svc.tasks.return_value.list.call_args.kwargs["showCompleted"] is False


def test_create_with_due():
    r, svc = setup()
    svc.tasks.return_value.insert.return_value.execute.return_value = {"id": "t2", "title": "X", "status": "needsAction"}
    r.get("create_task").fn(title="X", due="2026-10-05")
    body = svc.tasks.return_value.insert.call_args.kwargs["body"]
    assert body == {"title": "X", "due": "2026-10-05T00:00:00.000Z"}


def test_complete_and_reschedule_patch():
    r, svc = setup()
    svc.tasks.return_value.patch.return_value.execute.return_value = {"id": "t1", "title": "X", "status": "completed"}
    r.get("complete_task").fn(task_id="t1")
    assert svc.tasks.return_value.patch.call_args.kwargs["body"] == {"status": "completed"}
    r.get("reschedule_task").fn(task_id="t1", due="2026-10-09")
    assert svc.tasks.return_value.patch.call_args.kwargs["body"] == {"due": "2026-10-09T00:00:00.000Z"}


def test_bad_due_date_rejected_before_google_call():
    r, svc = setup()
    with pytest.raises(ValueError):
        r.get("create_task").fn(title="X", due="friday")
    svc.tasks.assert_not_called()


def test_confirm_tags():
    r, _ = setup()
    assert r.needs_confirm("list_tasks") is False
    for n in ("create_task", "complete_task", "reschedule_task"):
        assert r.needs_confirm(n)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_tasks.py -v` — Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/jarvis/google/tasks.py`:
```python
from datetime import date
from typing import Any, Callable

LIST = "@default"


def _due(d: date) -> str:
    return f"{d.isoformat()}T00:00:00.000Z"


def _slim(t: dict) -> dict:
    due = t.get("due")
    return {"id": t["id"], "title": t.get("title"), "due": due[:10] if due else None, "status": t.get("status")}


class TasksClient:
    def __init__(self, service_factory: Callable[[], Any]):
        self._svc = service_factory

    def list_tasks(self, include_completed: bool = False) -> list[dict]:
        resp = self._svc().tasks().list(tasklist=LIST, showCompleted=include_completed, maxResults=100).execute()
        return [_slim(t) for t in resp.get("items", [])]

    def create_task(self, title: str, due: date | None = None) -> dict:
        body = {"title": title}
        if due:
            body["due"] = _due(due)
        return _slim(self._svc().tasks().insert(tasklist=LIST, body=body).execute())

    def complete_task(self, task_id: str) -> dict:
        return _slim(self._svc().tasks().patch(tasklist=LIST, task=task_id, body={"status": "completed"}).execute())

    def reschedule_task(self, task_id: str, due: date) -> dict:
        return _slim(self._svc().tasks().patch(tasklist=LIST, task=task_id, body={"due": _due(due)}).execute())
```

`src/jarvis/tools/task_tools.py`:
```python
from datetime import date

from pydantic import BaseModel, Field

from jarvis.tools.registry import Registry, Tool

D = "YYYY-MM-DD"


class ListTasksArgs(BaseModel):
    include_completed: bool = False


class CreateTaskArgs(BaseModel):
    title: str
    due: str | None = Field(default=None, description=D)


class TaskIdArgs(BaseModel):
    task_id: str


class RescheduleArgs(BaseModel):
    task_id: str
    due: str = Field(description=D)


def register_task_tools(registry: Registry, client) -> None:
    def list_tasks(include_completed=False):
        return client.list_tasks(include_completed)

    def create_task(title, due=None):
        return client.create_task(title, date.fromisoformat(due) if due else None)

    def complete_task(task_id):
        return client.complete_task(task_id)

    def reschedule_task(task_id, due):
        return client.reschedule_task(task_id, date.fromisoformat(due))

    for name, desc, schema, fn, confirm in [
        ("list_tasks", "List open tasks with due dates (overdue = due before today).", ListTasksArgs, list_tasks, False),
        ("create_task", "Create a task, optionally with a due date.", CreateTaskArgs, create_task, True),
        ("complete_task", "Mark a task completed.", TaskIdArgs, complete_task, True),
        ("reschedule_task", "Change a task's due date.", RescheduleArgs, reschedule_task, True),
    ]:
        registry.add(Tool(name=name, domain="tasks", description=desc, args_schema=schema,
                          fn=fn, needs_confirm=confirm))
```

- [ ] **Step 4: Run tests, commit**

Run: `pytest tests/test_tasks.py -v` — Expected: 5 PASS

```bash
git add -A && git commit -m "feat: google tasks client and tools"
```

---

### Task 7: LLM provider and audit callback

**Files:**
- Create: `src/jarvis/llm.py`, `tests/fakes.py`, `tests/test_llm.py`

**Interfaces:**
- Produces:
  - `AuditCallback(audit)`: LangChain callback recording each LLM call (`kind="llm"`) with latency, model, tokens, cost, and failures.
  - `LLMProvider(settings, audit)` with `get(tier: str, tools: list | None = None) -> Runnable`; returns the primary model with the rest as `with_fallbacks`, tools bound to each model.
  - Test helpers in `tests/fakes.py`: `FakeChat(script: list[AIMessage])`, `FakeProvider(scripts: dict[str, list[AIMessage]])` with `.get(tier, tools=None)` returning the same `FakeChat` per tier, `MemoryAudit` with `.records: list[dict]` and `record(...)`.

- [ ] **Step 1: Write the test helpers**

`tests/fakes.py`:
```python
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


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
```

- [ ] **Step 2: Write failing tests**

`tests/test_llm.py`:
```python
from uuid import uuid4

from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.messages import AIMessage

from jarvis.config import Settings
from jarvis.llm import AuditCallback, LLMProvider
from tests.fakes import MemoryAudit


def settings():
    return Settings(_env_file=None, openrouter_api_key="k", models_fast="a/x,b/y", models_strong="c/z",
                    database_url="postgresql://x", telegram_bot_token="t", telegram_owner_chat_id=1, fernet_key="f")


def test_openrouter_privacy_params_on_every_model():
    p = LLMProvider(settings(), MemoryAudit())
    for tier, n in (("fast", 2), ("strong", 1)):
        models = p._models(tier)
        assert len(models) == n
        for m in models:
            prov = m.extra_body["provider"]
            assert prov["data_collection"] == "deny" and prov["require_parameters"] is True
            assert str(m.openai_api_base) == "https://openrouter.ai/api/v1"


def test_fast_tier_sorts_by_latency_strong_does_not():
    p = LLMProvider(settings(), MemoryAudit())
    assert p._models("fast")[0].extra_body["provider"]["sort"] == "latency"
    assert "sort" not in p._models("strong")[0].extra_body["provider"]


def test_get_builds_fallback_chain():
    p = LLMProvider(settings(), MemoryAudit())
    assert type(p.get("fast")).__name__ == "RunnableWithFallbacks"


def test_callback_records_llm_call():
    audit = MemoryAudit()
    cb = AuditCallback(audit)
    rid = uuid4()
    cb.on_chat_model_start({}, [[]], run_id=rid)
    res = LLMResult(generations=[[ChatGeneration(message=AIMessage("hi"))]],
                    llm_output={"model_name": "a/x", "token_usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.001}})
    cb.on_llm_end(res, run_id=rid)
    r = audit.records[0]
    assert (r["kind"], r["model"], r["tokens_in"], r["tokens_out"], r["cost_usd"]) == ("llm", "a/x", 10, 3, 0.001)
    assert r["latency_ms"] >= 0


def test_callback_records_failure():
    audit = MemoryAudit()
    cb = AuditCallback(audit)
    cb.on_llm_error(RuntimeError("boom"), run_id=uuid4())
    assert audit.records[0]["result"] == {"error": "boom"}
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_llm.py -v` — Expected: FAIL `ModuleNotFoundError: jarvis.llm`

- [ ] **Step 4: Implement**

`src/jarvis/llm.py`:
```python
import time

from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class AuditCallback(BaseCallbackHandler):
    def __init__(self, audit):
        self.audit = audit
        self._t0: dict = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self._t0[run_id] = time.monotonic()

    def _latency(self, run_id):
        t0 = self._t0.pop(run_id, None)
        return None if t0 is None else int((time.monotonic() - t0) * 1000)

    def on_llm_end(self, response, *, run_id, **kwargs):
        out = response.llm_output or {}
        usage = out.get("token_usage") or {}
        model = out.get("model_name", "unknown")
        self.audit.record("llm", model, model=model, latency_ms=self._latency(run_id),
                          tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"),
                          cost_usd=usage.get("cost"))

    def on_llm_error(self, error, *, run_id, **kwargs):
        self.audit.record("llm", "error", result={"error": str(error)}, latency_ms=self._latency(run_id))


class LLMProvider:
    def __init__(self, settings, audit):
        self.s = settings
        self.cb = AuditCallback(audit)

    def _models(self, tier: str) -> list[ChatOpenAI]:
        provider = {"data_collection": "deny", "require_parameters": True}
        if tier == "fast":
            provider["sort"] = "latency"
        return [
            ChatOpenAI(model=m, api_key=self.s.openrouter_api_key, base_url=OPENROUTER_BASE, temperature=0,
                       callbacks=[self.cb], extra_body={"provider": provider, "usage": {"include": True}})
            for m in self.s.models(tier)
        ]

    def get(self, tier: str, tools: list | None = None):
        models = [m.bind_tools(tools) if tools else m for m in self._models(tier)]
        return models[0].with_fallbacks(models[1:]) if len(models) > 1 else models[0]
```

Note: with one model `get` returns the bare model, so `test_get_builds_fallback_chain` uses the two-model `fast` tier.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_llm.py -v` — Expected: 5 PASS. If `m.openai_api_base` differs in the installed version, assert on `m.openai_api_base` as printed and keep the URL check; do not drop it.

- [ ] **Step 6: Check real usage cost reporting (needs a key; skip if absent)**

Run: `python -c "from jarvis.config import get_settings; from jarvis.llm import LLMProvider; r=LLMProvider(get_settings(), type('A',(),{'record':lambda s,*a,**k:print(a,k)})()).get('fast').invoke('say hi'); print(r.response_metadata)"`
Expected: an audit line printed with tokens; `cost_usd` may be `None`. If `None`, note it in the commit message; cost then derives from tokens times model price in a later task.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: openrouter llm provider with fallbacks and audit callback"
```

---

### Task 8: Router, domain agents and confirmation gate (the graph)

**Files:**
- Create: `src/jarvis/agent/__init__.py`, `src/jarvis/agent/domains.py`, `src/jarvis/agent/graph.py`, `tests/test_graph.py`

**Interfaces:**
- Consumes: `Registry`/`Tool` (Task 3), `ReauthRequired` (Task 4), `now_local` (Task 1), provider with `.get(tier, tools=None)` (Task 7), audit with `.record(...)`.
- Produces:
  - `DOMAINS: dict[str, Domain]`, `Domain(name, tier, prompt)`.
  - `State` TypedDict: `messages`, `domains: list[str]`, `idx: int`, `approved: bool`, `client_actions: list[dict]`.
  - `parse_domains(text: str) -> list[str]`, `window(messages, n=40) -> list`, `turn_replies(messages) -> list[str]`.
  - `build_graph(provider, registry, audit, checkpointer, tz) -> CompiledStateGraph`. Invoke with `{"messages": [HumanMessage(...)]}` and `config={"configurable": {"thread_id": ...}}`. A side-effect proposal returns a result containing `"__interrupt__"` (list of `Interrupt`, `.value == {"actions": [{"tool": str, "args": dict}]}`); resume with `Command(resume=True|False)`.

- [ ] **Step 1: Verify the LangGraph API**

Run: `python -c "from langgraph.types import interrupt, Command; from langgraph.checkpoint.memory import InMemorySaver; print('ok')"`
Expected: `ok`. If a name differs in the installed version, adjust imports below and in Task 9; keep the behavior.

- [ ] **Step 2: Write failing tests**

`tests/test_graph.py`:
```python
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from jarvis.agent.graph import build_graph, parse_domains, turn_replies, window
from jarvis.tools.registry import Registry, Tool
from tests.fakes import FakeProvider, MemoryAudit

CFG = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}


class Args(BaseModel):
    summary: str = ""


def call(name, args=None, id="c1"):
    return AIMessage("", tool_calls=[{"name": name, "args": args or {}, "id": id, "type": "tool_call"}])


def make(scripts, tools):
    reg = Registry()
    for t in tools:
        reg.add(t)
    audit = MemoryAudit()
    g = build_graph(FakeProvider(scripts), reg, audit, InMemorySaver(), "Europe/Berlin")
    return g, audit


def tool(name, domain, calls, needs_confirm=True, fn=None):
    return Tool(name=name, domain=domain, description="d", args_schema=Args,
                fn=fn or (lambda **kw: calls.append((name, kw)) or {"ok": True}), needs_confirm=needs_confirm)


def say(text="hi"):
    return {"messages": [HumanMessage(text)]}


# --- units ---
def test_parse_domains():
    assert parse_domains("calendar, tasks") == ["calendar", "tasks"]
    assert parse_domains("Tasks") == ["tasks"]
    assert parse_domains("calendar chat") == ["calendar"]
    assert parse_domains("gibberish") == ["chat"]
    assert parse_domains("calendar calendar") == ["calendar"]


def test_window_never_starts_on_orphan_tool_message():
    msgs = [HumanMessage("a"), call("x"), ToolMessage("r", tool_call_id="c1"), AIMessage("ok")]
    assert isinstance(window(msgs, 3)[0], HumanMessage) or not isinstance(window(msgs, 3)[0], ToolMessage)
    msgs = [call("x"), ToolMessage("r", tool_call_id="c1"), AIMessage("ok")]
    assert not isinstance(window(msgs, 2)[0], ToolMessage)


def test_turn_replies_only_current_turn_in_order():
    msgs = [HumanMessage("old"), AIMessage("old reply"), HumanMessage("new"), call("x"),
            ToolMessage("r", tool_call_id="c1"), AIMessage("first"), AIMessage("second")]
    assert turn_replies(msgs) == ["first", "second"]


# --- graph ---
async def test_read_tool_runs_without_confirmation():
    calls = []
    g, audit = make({"fast": [AIMessage("calendar")], "strong": [call("list_events"), AIMessage("You have gym.")]},
                    [tool("list_events", "calendar", calls, needs_confirm=False)])
    out = await g.ainvoke(say(), CFG)
    assert "__interrupt__" not in out
    assert calls == [("list_events", {"summary": ""})]
    assert turn_replies(out["messages"]) == ["You have gym."]
    assert audit.records[0]["kind"] == "tool" and audit.records[0]["confirmation"] == "not_required"


async def test_write_tool_pauses_and_does_not_run_until_approved():
    calls = []
    g, audit = make({"fast": [AIMessage("calendar")], "strong": [call("create_event", {"summary": "Gym"}), AIMessage("Created.")]},
                    [tool("create_event", "calendar", calls)])
    out = await g.ainvoke(say(), CFG)
    assert out["__interrupt__"][0].value == {"actions": [{"tool": "create_event", "args": {"summary": "Gym"}}]}
    assert calls == []
    out = await g.ainvoke(Command(resume=True), CFG)
    assert calls == [("create_event", {"summary": "Gym"})]
    assert turn_replies(out["messages"]) == ["Created."]
    assert audit.records[0]["confirmation"] == "approved"


async def test_cancel_runs_nothing_and_model_is_told():
    calls = []
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [call("create_event"), AIMessage("Okay, cancelled.")]},
                [tool("create_event", "calendar", calls)])
    await g.ainvoke(say(), CFG)
    out = await g.ainvoke(Command(resume=False), CFG)
    assert calls == []
    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert "cancelled" in tool_msgs[0].content.lower()


async def test_untagged_new_tool_defaults_to_confirmation():
    calls = []
    t = Tool(name="new_tool", domain="tasks", description="d", args_schema=Args,
             fn=lambda **kw: calls.append(kw) or {})  # needs_confirm not given
    g, _ = make({"fast": [AIMessage("tasks")], "strong": [call("new_tool"), AIMessage("done")]}, [t])
    out = await g.ainvoke(say(), CFG)
    assert "__interrupt__" in out and calls == []


async def test_domain_cannot_call_another_domains_tool():
    calls = []
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [call("list_tasks"), AIMessage("sorry")]},
                [tool("list_tasks", "tasks", calls, needs_confirm=False)])
    out = await g.ainvoke(say(), CFG)
    assert calls == []
    assert "unknown tool" in [m for m in out["messages"] if isinstance(m, ToolMessage)][0].content.lower()


async def test_unknown_tool_is_not_executed():
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [call("rm_rf"), AIMessage("sorry")]}, [])
    out = await g.ainvoke(say(), CFG)
    assert "__interrupt__" in out  # unknown names are treated as needing confirmation
    out = await g.ainvoke(Command(resume=True), CFG)
    assert "unknown tool" in [m for m in out["messages"] if isinstance(m, ToolMessage)][0].content.lower()


async def test_mixed_safe_and_write_in_one_step_asks_once_and_cancel_blocks_both():
    calls = []
    both = AIMessage("", tool_calls=[
        {"name": "list_events", "args": {}, "id": "a", "type": "tool_call"},
        {"name": "create_event", "args": {}, "id": "b", "type": "tool_call"}])
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [both, AIMessage("ok")]},
                [tool("list_events", "calendar", calls, needs_confirm=False), tool("create_event", "calendar", calls)])
    out = await g.ainvoke(say(), CFG)
    assert len(out["__interrupt__"][0].value["actions"]) == 1
    await g.ainvoke(Command(resume=False), CFG)
    assert calls == []


async def test_multi_domain_runs_in_order_and_replies_in_order():
    calls = []
    g, _ = make({"fast": [AIMessage("calendar, tasks")],
                 "strong": [call("list_events"), AIMessage("Calendar done."), call("list_tasks"), AIMessage("Tasks done.")]},
                [tool("list_events", "calendar", calls, False), tool("list_tasks", "tasks", calls, False)])
    out = await g.ainvoke(say(), CFG)
    assert [c[0] for c in calls] == ["list_events", "list_tasks"]
    assert turn_replies(out["messages"]) == ["Calendar done.", "Tasks done."]


async def test_tool_value_error_goes_back_to_model_and_loop_continues():
    def bad(**kw):
        raise ValueError("end must be after start")
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [call("list_events"), AIMessage("Let me fix that.")]},
                [tool("list_events", "calendar", [], False, fn=bad)])
    out = await g.ainvoke(say(), CFG)
    tm = [m for m in out["messages"] if isinstance(m, ToolMessage)][0]
    assert "end must be after start" in tm.content
    assert turn_replies(out["messages"]) == ["Let me fix that."]


async def test_client_actions_collected_from_tool_results():
    g, _ = make({"fast": [AIMessage("calendar")], "strong": [call("list_events"), AIMessage("ok")]},
                [tool("list_events", "calendar", [], False, fn=lambda **kw: {"client_action": {"type": "set_alarm", "time": "06:00"}})])
    out = await g.ainvoke(say(), CFG)
    assert out["client_actions"] == [{"type": "set_alarm", "time": "06:00"}]
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_graph.py -v` — Expected: FAIL `ModuleNotFoundError: jarvis.agent`

- [ ] **Step 4: Implement domains**

`src/jarvis/agent/__init__.py`: empty.

`src/jarvis/agent/domains.py`:
```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Domain:
    name: str
    tier: str
    prompt: str


_BASE = (
    "You are Jarvis, a concise personal assistant for one user. Use tools to look things up and act; "
    "never claim an action succeeded before its tool result says so. Write actions only propose; the user "
    "confirms them. If a tool returns an error, fix the arguments or tell the user plainly. "
    "Resolve relative dates against the current local time given below. Keep replies short."
)

DOMAINS = {
    "calendar": Domain("calendar", "strong", _BASE + " You handle Google Calendar: find, create, move, delete "
                       "events and find free slots. For recurring events, ask whether the user means one "
                       "occurrence or the whole series if it is unclear."),
    "tasks": Domain("tasks", "strong", _BASE + " You handle Google Tasks: list, create, complete and reschedule tasks. "
                    "Overdue means due before today."),
    "chat": Domain("chat", "fast", _BASE + " You have no tools; answer conversationally."),
}
```

- [ ] **Step 5: Implement the graph**

`src/jarvis/agent/graph.py`:
```python
import asyncio
import json
import logging
import re
import time
from typing import Annotated, Literal, TypedDict

from googleapiclient.errors import HttpError
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

from jarvis.agent.domains import DOMAINS
from jarvis.google.auth import ReauthRequired
from jarvis.timeutil import now_local

log = logging.getLogger(__name__)

ROUTER_PROMPT = (
    "Classify the user's latest request. Reply with ONLY a comma-separated list, in the order the work "
    "must happen, chosen from: calendar, tasks, chat. Use 'chat' alone when no calendar or task work is needed."
)
HISTORY = 40


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    domains: list[str]
    idx: int
    approved: bool
    client_actions: list[dict]


def parse_domains(text: str) -> list[str]:
    found: list[str] = []
    for tok in re.split(r"[,\s]+", text.lower()):
        if tok in DOMAINS and tok not in found:
            found.append(tok)
    if len(found) > 1 and "chat" in found:
        found.remove("chat")
    return found or ["chat"]


def window(messages: list, n: int = HISTORY) -> list:
    """Last n messages, trimmed so the slice never starts on an orphan tool result."""
    tail = messages[-n:]
    for i, m in enumerate(tail):
        if isinstance(m, HumanMessage):
            return tail[i:]
    while tail and isinstance(tail[0], ToolMessage):
        tail = tail[1:]
    return tail


def turn_replies(messages: list) -> list[str]:
    out: list[str] = []
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            break
        if isinstance(m, AIMessage) and not m.tool_calls and isinstance(m.content, str) and m.content:
            out.append(m.content)
    return out[::-1]


def build_graph(provider, registry, audit, checkpointer, tz: str):
    async def router(state: State) -> dict:
        resp = await provider.get("fast").ainvoke([SystemMessage(ROUTER_PROMPT), *window(state["messages"], 6)])
        return {"domains": parse_domains(str(resp.content)), "idx": 0, "approved": False, "client_actions": []}

    async def agent(state: State) -> dict:
        dom = DOMAINS[state["domains"][state["idx"]]]
        llm = provider.get(dom.tier, registry.lc_tools(dom.name) or None)
        system = SystemMessage(f"{dom.prompt}\nCurrent local time: {now_local(tz).isoformat()} ({tz}).")
        reply = await llm.ainvoke([system, *window(state["messages"])])
        return {"messages": [reply], "approved": False}

    def after_agent(state: State) -> str:
        return "gate" if state["messages"][-1].tool_calls else "advance"

    async def gate(state: State) -> Command[Literal["tools", "reject"]]:
        calls = state["messages"][-1].tool_calls
        pending = [c for c in calls if registry.needs_confirm(c["name"])]
        if not pending:
            return Command(goto="tools", update={"approved": True})
        decision = interrupt({"actions": [{"tool": c["name"], "args": c["args"]} for c in pending]})
        if decision is True:
            return Command(goto="tools", update={"approved": True})
        return Command(goto="reject")

    async def reject(state: State) -> dict:
        out = []
        for c in state["messages"][-1].tool_calls:
            await asyncio.to_thread(audit.record, "tool", c["name"], args=c["args"], confirmation="cancelled")
            out.append(ToolMessage("Cancelled by the user. Do not retry; ask what they want instead.",
                                   tool_call_id=c["id"]))
        return {"messages": out}

    async def tools(state: State) -> dict:
        allowed = {t.name for t in registry.for_domain(state["domains"][state["idx"]])}
        actions = list(state.get("client_actions", []))
        out = []
        for c in state["messages"][-1].tool_calls:
            tool = registry.get(c["name"])
            confirm = registry.needs_confirm(c["name"])
            result: object
            t0 = time.monotonic()
            if tool is None or c["name"] not in allowed:
                result = {"error": f"Unknown tool: {c['name']}"}
            elif confirm and not state["approved"]:
                result = {"error": "Action was not approved."}
            else:
                try:
                    kwargs = tool.args_schema(**c["args"]).model_dump(exclude_unset=True)
                    result = await asyncio.to_thread(tool.fn, **kwargs)
                except ReauthRequired:
                    result = {"error": "Google authorization expired. Tell the user to run: python -m jarvis.google.auth"}
                except HttpError as e:
                    result = {"error": f"Google API error {e.resp.status}. Tell the user; do not retry writes."}
                except ValueError as e:  # bad arguments or dates; includes pydantic ValidationError
                    result = {"error": f"Invalid arguments: {e}"}
                except Exception:
                    log.exception("tool %s failed", c["name"])
                    result = {"error": "Unexpected error while running the tool."}
            await asyncio.to_thread(
                audit.record, "tool", c["name"], args=c["args"], result=result,
                confirmation="approved" if confirm else "not_required",
                latency_ms=int((time.monotonic() - t0) * 1000))
            if isinstance(result, dict) and "client_action" in result:
                actions.append(result["client_action"])
            out.append(ToolMessage(json.dumps(result, default=str), tool_call_id=c["id"]))
        return {"messages": out, "client_actions": actions}

    async def advance(state: State) -> dict:
        return {"idx": state["idx"] + 1}

    def after_advance(state: State) -> str:
        return "agent" if state["idx"] < len(state["domains"]) else END

    g = StateGraph(State)
    for name, fn in [("router", router), ("agent", agent), ("gate", gate), ("reject", reject),
                     ("tools", tools), ("advance", advance)]:
        g.add_node(name, fn)
    g.add_edge(START, "router")
    g.add_edge("router", "agent")
    g.add_conditional_edges("agent", after_agent, ["gate", "advance"])
    g.add_edge("reject", "agent")
    g.add_edge("tools", "agent")
    g.add_conditional_edges("advance", after_advance, ["agent", END])
    return g.compile(checkpointer=checkpointer)
```

Note: `registry.lc_tools(dom.name) or None` gives the chat domain no tools. `kwargs` uses `exclude_unset=True` so optional args the model omitted are not passed as `None`, which matches the tool functions' defaults.

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_graph.py -v` — Expected: all PASS.
If `test_window_never_starts_on_orphan_tool_message` fails, fix `window`, not the test. If the first assertion in that test is too weak to fail, replace it with `assert not isinstance(window(msgs, 3)[0], ToolMessage)`.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: router, domain agents and confirmation gate"
```

---

### Task 9: Telegram channel

**Files:**
- Create: `src/jarvis/channels/__init__.py`, `src/jarvis/channels/telegram.py`, `tests/test_telegram.py`

**Interfaces:**
- Consumes: compiled graph (Task 8), `turn_replies`.
- Produces: `TelegramChannel(graph, owner_chat_id: int)` with `.owner_filter`, `.build(token) -> Application`, handlers `on_text(update, context)`, `on_button(update, context)`; `format_confirmation(payload: dict) -> str`; `FAIL_TEXT`.

- [ ] **Step 1: Write failing tests**

`tests/test_telegram.py`:
```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel
from telegram import Update

from jarvis.agent.graph import build_graph
from jarvis.channels.telegram import FAIL_TEXT, TelegramChannel, format_confirmation
from jarvis.tools.registry import Registry, Tool
from tests.fakes import FakeProvider, MemoryAudit

OWNER = 42


class Args(BaseModel):
    summary: str = ""


def chat():
    return SimpleNamespace(send_message=AsyncMock())


def text_update(c, text="book gym"):
    return SimpleNamespace(effective_chat=c, message=SimpleNamespace(text=text))


def button_update(c, data):
    q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_reply_markup=AsyncMock(),
                        message=SimpleNamespace(chat_id=OWNER, chat=c))
    return SimpleNamespace(callback_query=q)


def make_channel(scripts, calls):
    reg = Registry()
    reg.add(Tool(name="create_event", domain="calendar", description="d", args_schema=Args,
                 fn=lambda **kw: calls.append(kw) or {"ok": True}))
    g = build_graph(FakeProvider(scripts), reg, MemoryAudit(), InMemorySaver(), "Europe/Berlin")
    return TelegramChannel(g, OWNER)


def sent(c):
    return [(a[0], k) for a, k in c.send_message.call_args_list]


async def test_plain_reply():
    ch = make_channel({"fast": [AIMessage("chat"), AIMessage("Hello!")]}, [])
    c = chat()
    await ch.on_text(text_update(c, "hi"), None)
    assert sent(c) == [("Hello!", {})]


async def test_confirmation_flow_runs_action_once():
    calls = []
    tool_call = AIMessage("", tool_calls=[{"name": "create_event", "args": {"summary": "Gym"}, "id": "1", "type": "tool_call"}])
    ch = make_channel({"fast": [AIMessage("calendar")], "strong": [tool_call, AIMessage("Created.")]}, calls)
    c = chat()
    await ch.on_text(text_update(c), None)
    (prompt, kw), = sent(c)
    assert "create_event" in prompt and "reply_markup" in kw and calls == []

    # Review focus 1: new text while confirmation pending is refused
    c2 = chat()
    await ch.on_text(text_update(c2, "something else"), None)
    assert sent(c2) == [("Confirm or cancel the pending action first.", {})]

    c3 = chat()
    await ch.on_button(button_update(c3, "yes"), None)
    assert calls == [{"summary": "Gym"}]
    assert sent(c3) == [("Created.", {})]

    # Review focus 2: second tap does nothing
    c4 = chat()
    await ch.on_button(button_update(c4, "yes"), None)
    assert calls == [{"summary": "Gym"}]
    assert sent(c4) == [("Already handled.", {})]


async def test_cancel_button_runs_nothing():
    calls = []
    tool_call = AIMessage("", tool_calls=[{"name": "create_event", "args": {}, "id": "1", "type": "tool_call"}])
    ch = make_channel({"fast": [AIMessage("calendar")], "strong": [tool_call, AIMessage("Cancelled.")]}, calls)
    await ch.on_text(text_update(chat()), None)
    c = chat()
    await ch.on_button(button_update(c, "no"), None)
    assert calls == [] and sent(c) == [("Cancelled.", {})]


async def test_graph_failure_sends_fixed_message():
    graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(interrupts=())),
                            ainvoke=AsyncMock(side_effect=RuntimeError("all models down")))
    c = chat()
    await TelegramChannel(graph, OWNER).on_text(text_update(c), None)
    assert sent(c) == [(FAIL_TEXT, {})]


def test_owner_filter_rejects_strangers():
    ch = TelegramChannel(None, OWNER)

    def upd(chat_id):
        return Update.de_json({"update_id": 1, "message": {"message_id": 1, "date": 0,
                               "chat": {"id": chat_id, "type": "private"}, "text": "hi"}}, None)

    assert ch.owner_filter.check_update(upd(OWNER))
    assert not ch.owner_filter.check_update(upd(99))


def test_format_confirmation_lists_actions():
    s = format_confirmation({"actions": [{"tool": "delete_event", "args": {"event_id": "e1", "scope": "all"}}]})
    assert "delete_event" in s and "e1" in s
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_telegram.py -v` — Expected: FAIL `ModuleNotFoundError: jarvis.channels`

- [ ] **Step 3: Implement**

`src/jarvis/channels/__init__.py`: empty.

`src/jarvis/channels/telegram.py`:
```python
import json
import logging

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

from jarvis.agent.graph import turn_replies

log = logging.getLogger(__name__)

THREAD = {"configurable": {"thread_id": "owner"}, "recursion_limit": 40}
FAIL_TEXT = "Something went wrong. Please try again."
KEYBOARD = InlineKeyboardMarkup([[InlineKeyboardButton("Confirm", callback_data="yes"),
                                  InlineKeyboardButton("Cancel", callback_data="no")]])


def format_confirmation(payload: dict) -> str:
    lines = [f"• {a['tool']}: {json.dumps(a['args'], ensure_ascii=False)}" for a in payload["actions"]]
    return "Confirm this action?\n" + "\n".join(lines)


class TelegramChannel:
    def __init__(self, graph, owner_chat_id: int):
        self.graph = graph
        self.owner = owner_chat_id
        self.owner_filter = filters.Chat(chat_id=owner_chat_id)

    def build(self, token: str) -> Application:
        app = Application.builder().token(token).build()
        app.add_handler(MessageHandler(self.owner_filter & filters.TEXT & ~filters.COMMAND, self.on_text))
        app.add_handler(CallbackQueryHandler(self.on_button))
        app.add_handler(MessageHandler(~self.owner_filter, self.on_stranger), group=1)
        return app

    async def on_stranger(self, update, context):
        chat = update.effective_chat
        log.warning("dropped message from non-owner chat %s", chat.id if chat else None)

    async def _pending(self) -> bool:
        return bool((await self.graph.aget_state(THREAD)).interrupts)

    async def _run(self, chat, graph_input) -> None:
        try:
            result = await self.graph.ainvoke(graph_input, THREAD)
        except Exception:
            log.exception("graph run failed")
            await chat.send_message(FAIL_TEXT)
            return
        interrupts = result.get("__interrupt__")
        if interrupts:
            await chat.send_message(format_confirmation(interrupts[0].value), reply_markup=KEYBOARD)
            return
        for text in turn_replies(result["messages"]):
            await chat.send_message(text)

    async def on_text(self, update, context):
        chat = update.effective_chat
        if await self._pending():
            await chat.send_message("Confirm or cancel the pending action first.")
            return
        await self._run(chat, {"messages": [HumanMessage(update.message.text)]})

    async def on_button(self, update, context):
        q = update.callback_query
        await q.answer()
        if q.message.chat_id != self.owner:
            return
        chat = q.message.chat
        await q.edit_message_reply_markup(reply_markup=None)
        if not await self._pending():
            await chat.send_message("Already handled.")
            return
        await self._run(chat, Command(resume=q.data == "yes"))
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_telegram.py -v` — Expected: 6 PASS.
If `state.interrupts` is missing in the installed LangGraph, derive it with `[i for t in state.tasks for i in t.interrupts]` in `_pending` and in the failure-test stub.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: telegram channel with confirmation buttons and owner filter"
```

---

### Task 10: App wiring, run instructions, acceptance script

**Files:**
- Create: `src/jarvis/main.py`, `ACCEPTANCE.md`, `tests/test_main.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `app` (FastAPI) started with `uvicorn jarvis.main:app`; `GET /health -> {"ok": true}`.

- [ ] **Step 1: Write failing test**

`tests/test_main.py`:
```python
from fastapi.testclient import TestClient

from jarvis.main import create_app


def test_health_without_lifespan():
    # lifespan wiring needs Postgres, Telegram and Google; /health must not.
    app = create_app(with_lifespan=False)
    assert TestClient(app).get("/health").json() == {"ok": True}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_main.py -v` — Expected: FAIL `ModuleNotFoundError: jarvis.main`

- [ ] **Step 3: Implement**

`src/jarvis/main.py`:
```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from jarvis.agent.graph import build_graph
from jarvis.audit import Audit
from jarvis.channels.telegram import TelegramChannel
from jarvis.config import get_settings
from jarvis.db import init_schema, make_pool
from jarvis.google.auth import PgTokenStore, build_service
from jarvis.google.calendar import CalendarClient
from jarvis.google.tasks import TasksClient
from jarvis.llm import LLMProvider
from jarvis.tools.calendar_tools import register_calendar_tools
from jarvis.tools.registry import Registry
from jarvis.tools.task_tools import register_task_tools


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    pool = make_pool(s.database_url)
    init_schema(pool)
    audit = Audit(pool)
    audit.purge(90)
    store = PgTokenStore(pool)

    def svc(name: str, version: str):
        return lambda: build_service(name, version, store, s.fernet_key)

    registry = Registry()
    register_calendar_tools(registry, CalendarClient(svc("calendar", "v3"), s.timezone), s.timezone)
    register_task_tools(registry, TasksClient(svc("tasks", "v1")))

    async with AsyncPostgresSaver.from_conn_string(s.database_url) as saver:
        await saver.setup()
        graph = build_graph(LLMProvider(s, audit), registry, audit, saver, s.timezone)
        tg = TelegramChannel(graph, s.telegram_owner_chat_id).build(s.telegram_bot_token)
        await tg.initialize()
        await tg.start()
        await tg.updater.start_polling()
        try:
            yield
        finally:
            await tg.updater.stop()
            await tg.stop()
            await tg.shutdown()
    pool.close()


def create_app(with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(lifespan=lifespan if with_lifespan else None)

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


app = create_app()
```

`ACCEPTANCE.md`:
```markdown
# Manual acceptance (sub-project 1)

Setup: `docker compose up -d db`, copy `.env.example` to `.env` and fill it, put the Google OAuth client
file at `client_secret.json` (OAuth consent screen set to "In production"), run
`python -m jarvis.google.auth`, then `uvicorn jarvis.main:app`. Message the bot from the owner account.

Do not run the checks below until the PRD owner has clarified scenario 7 (its two constraints cannot both hold).

| # | Send | Expect |
|---|------|--------|
| 1 | "Put gym at 7 tomorrow in my calendar" | Confirm prompt naming `create_event`; after Confirm the event exists in Google Calendar |
| 2 | Same request, tap Cancel | No event created; the bot says it was cancelled |
| 3 | "Reschedule Friday's call with Raj to next week, same time" | Event found, new slot proposed via Confirm, event moved after tap (scenario 3) |
| 4 | "What's on my calendar today?" | Answer with no Confirm prompt |
| 5 | "Add a task: call mum, due Friday" then "what tasks are overdue?" | Confirm for create; list answers without Confirm |
| 6 | "Delete my gym event" on a recurring event | Bot asks this occurrence or all; Confirm names the chosen scope |
| 7 | Send a text while a Confirm prompt is open | "Confirm or cancel the pending action first." |
| 8 | Message the bot from a second Telegram account | No reply; a warning appears in the server log |
| 9 | `SELECT kind, name, confirmation FROM audit_log ORDER BY id DESC LIMIT 20;` | Every tool call and LLM call above is present; writes show `approved` or `cancelled` |
| 10 | Revoke the app at myaccount.google.com, then ask for today's events | Bot reports authorization expired and names the re-auth command |
```

- [ ] **Step 4: Run the full suite, commit**

Run: `TEST_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis_test pytest -v`
Expected: all PASS, no skips.

```bash
git add -A && git commit -m "feat: app wiring, health endpoint, acceptance script"
```

- [ ] **Step 5: Smoke run**

Run: `docker compose up -d db && uvicorn jarvis.main:app` with a filled `.env`, then work through `ACCEPTANCE.md` rows 1, 2, 4, 5, 7, 8, 9, 10.
Expected: each row behaves as written. Record any failure as a bug before moving on; do not edit the acceptance table to match behavior.

---

## Self-review

**Spec coverage:** router and domain agents (Task 8); confirmation gate with untagged-default and cross-domain scoping (Tasks 3, 8); Telegram long-polling, owner filter, inline buttons, stale taps (Task 9); Google OAuth with encrypted token, Calendar and Tasks scopes only (Task 4); FR-4 to FR-6 and recurring events (Task 5); FR-12 (Task 6); Europe/Berlin default and local-time injection (Tasks 1, 8); second-zone slot finding (Task 5); OpenRouter tiers, fallback, `data_collection: "deny"`, `require_parameters: true` (Task 7); audit log with 90-day purge and LLM/tool logging (Tasks 2, 7, 8); error handling for Google failure, LLM failure, expired token (Tasks 8, 9, acceptance row 10); Postgres checkpointing keyed by user (Tasks 9, 10, thread id `owner`). Gaps are listed at the top: transcript retention, subgraph form, scenario 7, client-action producers.

**Placeholder scan:** none. Model ids are intentionally left to `.env` because the spec defers them to scenario evaluation.

**Type consistency:** `Registry.for_domain/lc_tools/needs_confirm/get` (Task 3) match their uses in Task 8; `Audit.record` keyword names match `MemoryAudit` and the callers in Tasks 7 and 8; interrupt payload `{"actions": [{"tool", "args"}]}` (Task 8) matches `format_confirmation` (Task 9); `turn_replies` is imported by Task 9 from Task 8; the `State` keys used in `build_graph` match the dict returns.
