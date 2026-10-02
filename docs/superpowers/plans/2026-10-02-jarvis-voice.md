# Jarvis Voice (Pixel app) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/voice` WebSocket channel on the existing agent (Deepgram STT, Cartesia TTS, barge-in, spoken confirmations, phone client actions, push) plus a Flutter Pixel app (wake word, overlay, intents, default assistant, FCM tap-to-play).

**Architecture:** `src/jarvis/voice/` holds a `VoiceService` that authenticates a device token, runs one `VoiceSession` per socket, and drives the same LangGraph graph and thread as Telegram (`thread_id: "owner"`), with a `voice` config flag selecting the fast tier. STT/TTS sit behind small Protocols so tests use fakes. Phone tools only queue `client_actions`; the app runs them as Android intents. The Flutter app (`jarvis_app/`) keeps all logic in a testable `SessionController` with ports for socket, mic, player, phone actions and speaker.

**Tech Stack:** Python 3.11+, FastAPI/Starlette WebSocket, LangGraph, `websockets`, `httpx`, Postgres, pytest (`TestClient`). Flutter/Dart 3, `web_socket_channel`, `record`, `porcupine_flutter`, `flutter_foreground_task`, `android_intent_plus`, `firebase_messaging`.

**Spec:** `docs/superpowers/specs/2026-10-02-jarvis-voice-design.md` (binding). Builds on sub-projects 1 and 2 on `main`.

## Global Constraints

- Work on branch `feat/voice` (never on `main`). Run Python from the venv: `. .venv/bin/activate`; tests: `TEST_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis_test pytest -q` (the DB name must end in `_test`).
- Every commit message ends with these two trailer lines (shown as `# + trailers` in the steps):
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_018XhFKuKZv7cok6HQ5Vtq6u`.
- Audio is 16 kHz, mono, signed 16-bit little-endian PCM in both directions (`SAMPLE_RATE = 16000`). Text frames are JSON objects with a string `type`.
- Frames, exact names. Up: `hello` (optional `fcm_token`), `cancel`, `confirm` (`decision` `yes`|`no`, `interrupt_id`), `speak` (`text`), `bye`. Down: `state` (`state` = `listening`|`thinking`|`speaking`), `transcript` (`role` `user`|`assistant`, `text`, `final`), `confirm_card` (`interrupt_id`, `summary`, `tap_only`, `after_untrusted`), `client_actions` (`actions`), `error` (`message`).
- Close codes: `4401` bad/missing token (closed before `accept`), `4000` replaced by a newer session, `4408` utterance cap, `1009` frame too large, `1011` upstream failure, `1013` voice not configured.
- Caps: audio frame 64 KiB, utterance 60 s of audio counted while listening, `speak` text 2000 chars, one live session.
- Safety rules are unchanged: only the graph's `interrupt()` gate authorises side effects. Voice may resume a pending confirmation only with a bare yes/no (`confirm.py`), and never a spoken yes for `send_draft`.
- Never log or store audio or transcripts beyond the existing audit rules; no secrets in the app except the device token and the build-time Picovoice AccessKey.
- Do not change the graph's gate, tools node or untrusted handling, except the one-line tier switch and router prompt in Task 2.
- Flutter: Android only, package `com.jarvis.jarvis_app`, add dependencies with `flutter pub add` so versions resolve to the latest compatible; if an API differs from the code shown, adapt the thin wrapper to the installed version (`flutter analyze` must be clean) without changing the interfaces other tasks use.

## Review Focus

- A spoken lookalike ("yes, and also delete everything", "yes please") must not resume a confirmation. Pinned in Task 6.
- Barge-in or a dropped socket mid-turn must not lose or repeat a write: an executed write stays audited and a pending confirmation survives the disconnect. Pinned in Task 7.
- Garbage input (non-JSON text frame, unknown type, oversized frame, empty `speak`, bytes with no STT script) must produce an `error` or close, never hang or crash the session. Pinned in Task 7.
- A phone action requested from Telegram with no voice client connected must tell the user it was not run. Pinned in Task 8.
- An empty graph reply is still spoken (the Telegram `EMPTY_TEXT`), and a second connection replaces the first cleanly. Pinned in Tasks 5 and 8.
- Known limit, recorded not hidden: the reply goes to TTS only after the graph turn finishes (no LLM token streaming into TTS). If measured p50 misses 1.5 s, streaming the final agent tokens into TTS is the next step; it is listed in `ACCEPTANCE.md`.

---

## Server

### Task 1: Settings, devices table, device store, token CLI

**Files:**
- Modify: `src/jarvis/config.py`, `src/jarvis/db.py`, `tests/conftest.py`, `pyproject.toml`, `.env.example`
- Create: `src/jarvis/voice/__init__.py` (empty), `src/jarvis/voice/devices.py`, `src/jarvis/voice/token.py`
- Test: `tests/test_devices.py`

**Interfaces:**
- Produces: `hash_token(token: str) -> str`; `Devices(pool)` with `create() -> tuple[int, str]`, `verify(token: str) -> int | None`, `set_fcm(device_id: int, fcm_token: str) -> None`, `fcm_tokens() -> list[str]`, `revoke(device_id: int) -> bool`; `run(argv, devices) -> str` in `token.py`; `Settings.deepgram_api_key`, `cartesia_api_key`, `cartesia_voice_id`, `fcm_credentials_path` (all default `""`).

- [ ] **Step 1: Write the failing test** — `tests/test_devices.py`

```python
from jarvis.voice.devices import Devices, hash_token
from jarvis.voice.token import run


def test_create_then_verify_roundtrip(pool):
    d = Devices(pool)
    did, token = d.create()
    assert d.verify(token) == did
    assert d.verify(token + "x") is None
    assert d.verify("") is None


def test_only_the_hash_is_stored(pool):
    d = Devices(pool)
    _, token = d.create()
    with pool.connection() as c:
        rows = c.execute("SELECT token_hash FROM devices").fetchall()
    assert rows == [(hash_token(token),)] and token not in rows[0][0]


def test_verify_updates_last_seen(pool):
    d = Devices(pool)
    did, token = d.create()
    d.verify(token)
    with pool.connection() as c:
        assert c.execute("SELECT last_seen FROM devices WHERE id=%s", (did,)).fetchone()[0] is not None


def test_fcm_token_stored_and_listed(pool):
    d = Devices(pool)
    did, _ = d.create()
    assert d.fcm_tokens() == []
    d.set_fcm(did, "fcm-abc")
    d.set_fcm(did, "fcm-def")  # a refreshed token replaces the old one
    assert d.fcm_tokens() == ["fcm-def"]


def test_revoke(pool):
    d = Devices(pool)
    did, token = d.create()
    assert d.revoke(did) is True
    assert d.verify(token) is None
    assert d.revoke(did) is False


def test_cli_creates_and_revokes(pool):
    d = Devices(pool)
    out = run([], d)
    token = out.splitlines()[-1]
    did = d.verify(token)
    assert did is not None and f"Device {did}" in out
    assert run(["--revoke", str(did)], d) == f"Revoked device {did}."
    assert run(["--revoke", str(did)], d) == f"No device {did}."
```

- [ ] **Step 2: Run to verify failure**

Run: `TEST_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis_test pytest tests/test_devices.py -q`
Expected: FAIL (`ModuleNotFoundError: jarvis.voice`).

- [ ] **Step 3: Implement**

`src/jarvis/config.py` — add after `timezone`:

```python
    deepgram_api_key: str = ""
    cartesia_api_key: str = ""
    cartesia_voice_id: str = ""
    fcm_credentials_path: str = ""  # Firebase service-account JSON, kept outside the repo
```

`src/jarvis/db.py` — append to `SCHEMA` (inside the triple-quoted string, after `oauth_tokens`):

```sql
CREATE TABLE IF NOT EXISTS devices (
  id SERIAL PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  fcm_token TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen TIMESTAMPTZ
);
```

`tests/conftest.py` — change the truncate to `c.execute("TRUNCATE audit_log, oauth_tokens, devices")`.

`pyproject.toml` — add `"websockets>=13", "httpx>=0.27",` to `dependencies` (uvicorn needs `websockets` to serve WebSockets; `httpx` is used by FCM push and the test client). Run `pip install -e '.[dev]'`.

`.env.example` — append:

```
JARVIS_DEEPGRAM_API_KEY=
JARVIS_CARTESIA_API_KEY=
JARVIS_CARTESIA_VOICE_ID=
# Firebase service-account JSON for push (optional until you want tap-to-play push)
JARVIS_FCM_CREDENTIALS_PATH=
```

`src/jarvis/voice/devices.py`:

```python
import hashlib
import hmac
import secrets


def hash_token(token: str) -> str:
    # 256 random bits, so a fast hash is enough; a slow KDF would only add latency to every connect
    return hashlib.sha256(token.encode()).hexdigest()


class Devices:
    def __init__(self, pool):
        self.pool = pool

    def create(self) -> tuple[int, str]:
        token = secrets.token_urlsafe(32)
        with self.pool.connection() as c:
            did = c.execute("INSERT INTO devices (token_hash) VALUES (%s) RETURNING id", (hash_token(token),)).fetchone()[0]
        return did, token

    def verify(self, token: str) -> int | None:
        if not token:
            return None
        h = hash_token(token)
        with self.pool.connection() as c:
            rows = c.execute("SELECT id, token_hash FROM devices").fetchall()
            found = None
            for did, stored in rows:  # compare every row so timing does not reveal a prefix match
                if hmac.compare_digest(stored, h):
                    found = did
            if found is not None:
                c.execute("UPDATE devices SET last_seen = now() WHERE id = %s", (found,))
        return found

    def set_fcm(self, device_id: int, fcm_token: str) -> None:
        with self.pool.connection() as c:
            c.execute("UPDATE devices SET fcm_token = %s WHERE id = %s", (fcm_token, device_id))

    def fcm_tokens(self) -> list[str]:
        with self.pool.connection() as c:
            return [r[0] for r in c.execute("SELECT fcm_token FROM devices WHERE fcm_token IS NOT NULL").fetchall()]

    def revoke(self, device_id: int) -> bool:
        with self.pool.connection() as c:
            return c.execute("DELETE FROM devices WHERE id = %s", (device_id,)).rowcount > 0
```

`src/jarvis/voice/token.py`:

```python
import argparse
import sys

from jarvis.config import get_settings
from jarvis.db import init_schema, make_pool
from jarvis.voice.devices import Devices


def run(argv: list[str], devices: Devices) -> str:
    p = argparse.ArgumentParser(prog="python -m jarvis.voice.token")
    p.add_argument("--revoke", type=int, metavar="ID", help="delete a device")
    a = p.parse_args(argv)
    if a.revoke is not None:
        return f"Revoked device {a.revoke}." if devices.revoke(a.revoke) else f"No device {a.revoke}."
    did, token = devices.create()
    return f"Device {did} created. Token (shown once, enter it in the app):\n{token}"


def main() -> None:
    pool = make_pool(get_settings().database_url)
    try:
        init_schema(pool)
        print(run(sys.argv[1:], Devices(pool)))
    finally:
        pool.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `TEST_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis_test pytest -q`
Expected: all pass (188 existing + 6 new).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: devices table, device token store and token CLI"  # + trailers
```

---

### Task 2: Phone tools, `phone` domain, voice tier switch

**Files:**
- Create: `src/jarvis/tools/phone_tools.py`
- Modify: `src/jarvis/agent/domains.py`, `src/jarvis/agent/graph.py`, `src/jarvis/main.py`, `tests/test_wiring.py`
- Test: `tests/test_phone_tools.py`, additions to `tests/test_graph.py`

**Interfaces:**
- Produces: `register_phone_tools(registry)`; tools `set_alarm(hour, minute, label?)`, `set_timer(seconds, label?)`, `start_navigation(destination)`, `compose_message(app, contact, text)`, domain `phone`, none confirm-gated. Each returns `{"queued_for_phone": True, "client_action": {"type": <tool name>, ...args}}`; the existing tools node already collects `client_action` into `State.client_actions`. Action `type` values: `set_alarm`, `set_timer`, `start_navigation`, `compose_message`.
- Consumes: graph config key `configurable.voice` (set by Task 5).

- [ ] **Step 1: Write the failing tests**

`tests/test_phone_tools.py`:

```python
import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from jarvis.agent.graph import turn_replies
from jarvis.tools.phone_tools import ComposeArgs, SetAlarmArgs, SetTimerArgs, register_phone_tools
from jarvis.tools.registry import Registry
from tests.test_graph import CFG, call, make_graph, say


def reg():
    r = Registry()
    register_phone_tools(r)
    return r


def test_four_phone_tools_none_need_confirmation():
    r = reg()
    assert {t.name for t in r.for_domain("phone")} == {"set_alarm", "set_timer", "start_navigation", "compose_message"}
    assert not any(r.needs_confirm(t.name) for t in r.for_domain("phone"))


def test_each_tool_queues_a_typed_client_action():
    r = reg()
    assert r.get("set_alarm").fn(hour=6, minute=0) == {
        "queued_for_phone": True, "client_action": {"type": "set_alarm", "hour": 6, "minute": 0}}
    assert r.get("set_timer").fn(seconds=300, label="tea")["client_action"] == {"type": "set_timer", "seconds": 300, "label": "tea"}
    assert r.get("start_navigation").fn(destination="Marienplatz")["client_action"] == {"type": "start_navigation", "destination": "Marienplatz"}
    assert r.get("compose_message").fn(app="whatsapp", contact="Anna", text="10 min late")["client_action"] == {
        "type": "compose_message", "app": "whatsapp", "contact": "Anna", "text": "10 min late"}


def test_argument_validation():
    with pytest.raises(ValidationError):
        SetAlarmArgs(hour=24, minute=0)
    with pytest.raises(ValidationError):
        SetAlarmArgs(hour=6, minute=60)
    with pytest.raises(ValidationError):
        SetTimerArgs(seconds=0)
    with pytest.raises(ValidationError):
        ComposeArgs(app="telegram", contact="Anna", text="hi")


async def test_alarm_reaches_client_actions_through_the_graph():
    g, audit = make_graph({"fast": [AIMessage("phone"), call("set_alarm", {"hour": 6, "minute": 0}),
                                    AIMessage("Asking your phone to set a 06:00 alarm.")]}, reg())
    out = await g.ainvoke(say("set an alarm for 6"), CFG)
    assert "__interrupt__" not in out
    assert out["client_actions"] == [{"type": "set_alarm", "hour": 6, "minute": 0}]
    assert turn_replies(out["messages"]) == ["Asking your phone to set a 06:00 alarm."]
    assert audit.records[0]["confirmation"] == "not_required"
```

Add to `tests/test_graph.py` — first refactor `make` so a ready registry can be passed. Replace the existing `make` with:

```python
def make_graph(scripts, reg):
    audit = MemoryAudit()
    return build_graph(FakeProvider(scripts), reg, audit, InMemorySaver(), "Europe/Berlin"), audit


def make(scripts, tools):
    reg = Registry()
    for t in tools:
        reg.add(t)
    return make_graph(scripts, reg)
```

and append:

```python
VOICE = {"configurable": {"thread_id": "t", "voice": True}, "recursion_limit": 40}


async def test_voice_config_uses_fast_tier_for_calendar():
    g, _ = make({"fast": [AIMessage("calendar"), AIMessage("fast answer")]},  # no "strong" key: using it would KeyError
                [tool("list_events", "calendar", [], needs_confirm=False)])
    out = await g.ainvoke(say(), VOICE)
    assert turn_replies(out["messages"]) == ["fast answer"]


async def test_voice_config_keeps_gmail_on_strong_tier():
    g, _ = make({"fast": [AIMessage("gmail")], "strong": [AIMessage("strong answer")]},
                [tool("search_emails", "gmail", [], needs_confirm=False)])
    out = await g.ainvoke(say(), VOICE)
    assert turn_replies(out["messages"]) == ["strong answer"]


def test_phone_domain_is_routable_and_named_in_router_prompt():
    from jarvis.agent.graph import ROUTER_PROMPT
    assert parse_domains("calendar, phone") == ["calendar", "phone"]
    assert "phone" in ROUTER_PROMPT
```

Update `tests/test_wiring.py`: add `"set_alarm", "set_timer", "start_navigation", "compose_message"` to `NO_CONFIRM`, and change the domain assertion in `test_every_registered_tool_belongs_to_a_routable_domain` to `== {"calendar", "tasks", "gmail", "phone"}`.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_phone_tools.py tests/test_graph.py tests/test_wiring.py -q`
Expected: FAIL (`ModuleNotFoundError: jarvis.tools.phone_tools`).

- [ ] **Step 3: Implement**

`src/jarvis/tools/phone_tools.py`:

```python
from typing import Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import Registry, Tool


class SetAlarmArgs(BaseModel):
    hour: int = Field(ge=0, le=23, description="24-hour clock, local time")
    minute: int = Field(ge=0, le=59)
    label: str | None = None


class SetTimerArgs(BaseModel):
    seconds: int = Field(gt=0, le=86400)
    label: str | None = None


class NavigationArgs(BaseModel):
    destination: str = Field(min_length=1, description="Address or place name")


class ComposeArgs(BaseModel):
    app: Literal["whatsapp", "sms"]
    contact: str = Field(min_length=1, description="Contact name as the user said it; the phone resolves it")
    text: str = Field(min_length=1)


def register_phone_tools(registry: Registry) -> None:
    def queue(type_: str, **args) -> dict:
        # nothing runs on the server: the voice app executes the action as an Android intent
        return {"queued_for_phone": True, "client_action": {"type": type_, **args}}

    def set_alarm(**kw):
        return queue("set_alarm", **kw)

    def set_timer(**kw):
        return queue("set_timer", **kw)

    def start_navigation(**kw):
        return queue("start_navigation", **kw)

    def compose_message(**kw):
        return queue("compose_message", **kw)

    for name, desc, schema, fn in [
        ("set_alarm", "Set an alarm on the user's phone.", SetAlarmArgs, set_alarm),
        ("set_timer", "Start a countdown timer on the user's phone.", SetTimerArgs, set_timer),
        ("start_navigation", "Start Google Maps navigation on the user's phone.", NavigationArgs, start_navigation),
        ("compose_message", "Open a WhatsApp or SMS message, pre-filled, for the user to send. Never sends it.",
         ComposeArgs, compose_message),
    ]:
        registry.add(Tool(name=name, domain="phone", description=desc, args_schema=schema, fn=fn, needs_confirm=False))
```

`src/jarvis/agent/domains.py` — add to `DOMAINS` before `chat`:

```python
    "phone": Domain("phone", "fast", _BASE + " You control the user's phone with tools: set_alarm, set_timer, "
                    "start_navigation, compose_message. These are queued for the phone app, which runs them; say "
                    "you asked the phone, never that it is done. compose_message only opens the message for the "
                    "user to send. If the phone app is not connected the user is told separately."),
```

`src/jarvis/agent/graph.py`:
- Change `ROUTER_PROMPT` to:

```python
ROUTER_PROMPT = (
    "Classify the user's latest request. Reply with ONLY a comma-separated list, in the order the work "
    "must happen, chosen from: calendar, tasks, gmail, phone, chat. Use 'chat' alone when no calendar, task, "
    "email or phone work is needed. Examples: 'add that booking email to my calendar' -> gmail, calendar; "
    "'set an alarm for 6 and put gym at 7 in my calendar' -> calendar, phone."
)
```
- Add `from langchain_core.runnables import RunnableConfig` and change the `agent` node to:

```python
    async def agent(state: State, config: RunnableConfig) -> dict:
        dom = DOMAINS[state["domains"][state["idx"]]]
        # voice turns trade some tool-calling strength for latency; gmail stays strong (untrusted content)
        voice = (config.get("configurable") or {}).get("voice")
        tier = "fast" if voice and dom.name != "gmail" else dom.tier
        llm = provider.get(tier, registry.lc_tools(dom.name) or None)
        system = SystemMessage(f"{dom.prompt}\nCurrent local time: {now_local(tz).strftime('%A %Y-%m-%d %H:%M %Z (UTC%z)')} ({tz}).")
        reply = await llm.ainvoke([system, *repair_tool_gaps(window(state["messages"]))])
        return {"messages": [reply], "approved": False}
```

`src/jarvis/main.py` — import `from jarvis.tools.phone_tools import register_phone_tools` and add `register_phone_tools(registry)` as the last line of `build_registry` before `return`.

- [ ] **Step 4: Run to verify pass**

Run: `TEST_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis_test pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: phone tools, phone domain, fast tier for voice turns"  # + trailers
```

---

### Task 3: Frame helpers and the strict confirmation matcher

**Files:**
- Create: `src/jarvis/voice/protocol.py`, `src/jarvis/voice/confirm.py`
- Test: `tests/test_voice_protocol.py`

**Interfaces:**
- Produces: `protocol.frame(type_, **fields) -> str`, `protocol.parse(text) -> dict | None`, constants `SAMPLE_RATE, MAX_AUDIO_FRAME, MAX_UTTERANCE_BYTES, MAX_SPEAK_CHARS, CLOSE_UNAUTHORIZED, CLOSE_REPLACED, CLOSE_TOO_LONG, CLOSE_TOO_BIG, CLOSE_UPSTREAM, CLOSE_UNAVAILABLE`; `confirm.match_confirmation(text) -> bool | None`.

- [ ] **Step 1: Write the failing test** — `tests/test_voice_protocol.py`

```python
import json

import pytest

from jarvis.voice import protocol as P
from jarvis.voice.confirm import match_confirmation


def test_frame_roundtrip_and_unicode():
    assert json.loads(P.frame("state", state="listening")) == {"type": "state", "state": "listening"}
    assert "ä" in P.frame("transcript", text="Grüße")  # ensure_ascii=False


@pytest.mark.parametrize("bad", ["", "nope", "[]", "42", '{"no_type": 1}', '{"type": 3}', "{"])
def test_parse_rejects_non_frames(bad):
    assert P.parse(bad) is None


def test_parse_accepts_object_with_string_type():
    assert P.parse('{"type": "cancel"}') == {"type": "cancel"}


def test_caps_are_the_spec_values():
    assert (P.SAMPLE_RATE, P.MAX_AUDIO_FRAME, P.MAX_SPEAK_CHARS) == (16000, 65536, 2000)
    assert P.MAX_UTTERANCE_BYTES == 60 * 16000 * 2


@pytest.mark.parametrize("text", ["yes", "Yes.", " YEAH! ", "confirm", "Do it!", "do   it"])
def test_yes_variants(text):
    assert match_confirmation(text) is True


@pytest.mark.parametrize("text", ["no", "No.", "cancel", "Stop!"])
def test_no_variants(text):
    assert match_confirmation(text) is False


@pytest.mark.parametrize("text", ["", "   ", "yes and also delete everything", "yes please", "yes yes",
                                  "no wait", "not now", "maybe", "okay", "send it", "yes, send it to bob"])
def test_anything_else_is_not_a_decision(text):
    assert match_confirmation(text) is None
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_voice_protocol.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement**

`src/jarvis/voice/protocol.py`:

```python
import json

SAMPLE_RATE = 16000
MAX_AUDIO_FRAME = 64 * 1024
MAX_UTTERANCE_BYTES = 60 * SAMPLE_RATE * 2  # 60 s of 16-bit mono
MAX_SPEAK_CHARS = 2000

CLOSE_REPLACED = 4000
CLOSE_UNAUTHORIZED = 4401
CLOSE_TOO_LONG = 4408
CLOSE_TOO_BIG = 1009
CLOSE_UPSTREAM = 1011
CLOSE_UNAVAILABLE = 1013


def frame(type_: str, **fields) -> str:
    return json.dumps({"type": type_, **fields}, ensure_ascii=False)


def parse(text: str) -> dict | None:
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) and isinstance(obj.get("type"), str) else None
```

`src/jarvis/voice/confirm.py`:

```python
import re

YES = {"yes", "yeah", "confirm", "do it"}
NO = {"no", "cancel", "stop"}


def match_confirmation(text: str) -> bool | None:
    """True/False only when the whole utterance is one decision word; anything else is a new request (None).
    Plain code on purpose: the LLM never decides whether the user confirmed."""
    t = " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())
    if t in YES:
        return True
    if t in NO:
        return False
    return None
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_voice_protocol.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: voice frame helpers and strict confirmation matcher"  # + trailers`

---

### Task 4: STT and TTS adapters (Deepgram, Cartesia) and test fakes

**Files:**
- Create: `src/jarvis/voice/stt.py`, `src/jarvis/voice/tts.py`
- Modify: `tests/fakes.py`
- Test: `tests/test_voice_speech.py`

**Interfaces:**
- Produces: `SttEvent(kind: "partial"|"final", text)`; `STT.open() -> STTStream`; `STTStream.send(pcm)`, `.events() -> AsyncIterator[SttEvent]`, `.close()`; `parse_deepgram(msg, buf) -> SttEvent | None`; `DeepgramSTT(api_key)`; `split_sentences(text) -> list[str]`; `parse_cartesia(msg) -> bytes | "done" | None` (raises `RuntimeError` on an error message); `TTS.synth(sentences: Sequence[str]) -> AsyncIterator[bytes]`; `CartesiaTTS(api_key, voice_id)`. Test fakes `FakeSTT(script, fail_events=False, fail_open=False)` (each audio frame received pops one scripted `SttEvent | None`) and `FakeTTS(stall=False, fail=False)` (yields `b"audio:<sentence>"`, records `spoken`, sets `cancelled` on cancel).

- [ ] **Step 1: Write the failing test** — `tests/test_voice_speech.py`

```python
import base64

import pytest

from jarvis.voice.stt import SttEvent, parse_deepgram
from jarvis.voice.tts import parse_cartesia, split_sentences


def results(text, is_final=False, speech_final=False):
    return {"type": "Results", "is_final": is_final, "speech_final": speech_final,
            "channel": {"alternatives": [{"transcript": text}]}}


def test_interim_result_is_a_partial():
    assert parse_deepgram(results("what's my"), []) == SttEvent("partial", "what's my")


def test_empty_transcripts_are_ignored():
    assert parse_deepgram(results(""), []) is None
    assert parse_deepgram(results("", is_final=True, speech_final=True), []) is None


def test_finals_accumulate_until_speech_final():
    buf: list[str] = []
    assert parse_deepgram(results("set an alarm", is_final=True), buf) is None
    assert parse_deepgram(results("for six", is_final=True, speech_final=True), buf) == SttEvent("final", "set an alarm for six")
    assert buf == []


def test_utterance_end_flushes_buffer():
    buf = ["what's my day"]
    assert parse_deepgram({"type": "UtteranceEnd"}, buf) == SttEvent("final", "what's my day")
    assert parse_deepgram({"type": "UtteranceEnd"}, buf) is None


def test_unknown_messages_are_ignored():
    assert parse_deepgram({"type": "Metadata"}, []) is None


def test_split_sentences():
    assert split_sentences("Hi. How can I help?  Fine!") == ["Hi.", "How can I help?", "Fine!"]
    assert split_sentences("no punctuation") == ["no punctuation"]
    assert split_sentences("   ") == []


def test_parse_cartesia():
    assert parse_cartesia({"type": "chunk", "data": base64.b64encode(b"\x01\x02").decode()}) == b"\x01\x02"
    assert parse_cartesia({"type": "done"}) == "done"
    assert parse_cartesia({"type": "timestamps"}) is None
    with pytest.raises(RuntimeError):
        parse_cartesia({"type": "error", "error": "bad voice"})
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_voice_speech.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement**

`src/jarvis/voice/stt.py`:

```python
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
```

`src/jarvis/voice/tts.py`:

```python
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
```

`tests/fakes.py` — append:

```python
import asyncio

from jarvis.voice.stt import SttEvent  # noqa: F401  (re-exported for tests)


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
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_voice_speech.py -q` → PASS; then `pytest -q` (whole suite) → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: Deepgram and Cartesia adapters behind STT/TTS protocols, test fakes"  # + trailers`

---

### Task 5: VoiceService — auth, session replacement, the basic turn

**Files:**
- Create: `src/jarvis/voice/ws.py`, `tests/voice_helpers.py`
- Test: `tests/test_voice_ws.py`

**Interfaces:**
- Consumes: Tasks 1-4 (`Devices.verify/set_fcm`, `protocol`, `confirm`, `split_sentences`, `STT`/`TTS`), `turn_replies`, `THREAD/EMPTY_TEXT/FAIL_TEXT` from `channels/telegram.py`.
- Produces: `VoiceService(graph, devices, stt, tts)` with `async handle(ws)`, `async deliver(actions) -> bool`, attribute `current`; module constants `VOICE_CFG`, `TAP_ONLY`; class `VoiceSession` (internals below are extended by Tasks 6-8). `tests/voice_helpers.py` with `FakeDevices`, `build(...)`, `read`, `until`, `ping`, `AUTH`.

- [ ] **Step 1: Write the failing tests**

`tests/voice_helpers.py`:

```python
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
```

`tests/test_voice_ws.py`:

```python
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from starlette.websockets import WebSocketDisconnect

from tests.voice_helpers import AUTH, FINAL, build, chat_scripts, ping, read, until, until_state, texts, audio


def test_missing_or_bad_token_is_closed_before_accept():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c:
        for headers in ({}, {"authorization": "Bearer nope"}, {"authorization": "good"}, {"authorization": "Bearer "}):
            with pytest.raises(WebSocketDisconnect) as e:
                with c.websocket_connect("/voice", headers=headers):
                    pass
            assert e.value.code == 4401
    assert h.stt.streams == []  # no audio pipeline was opened for a stranger


def test_voice_not_configured_closes_1013():
    h = build(chat_scripts("Hi."), with_voice=False)
    with TestClient(h.app) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/voice", headers=AUTH):
                pass
        assert e.value.code == 1013


def test_audio_turn_streams_transcript_reply_and_audio_in_order():
    h = build(chat_scripts("Hi. How can I help?"), stt_script=[FINAL("hello jarvis")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        assert read(ws) == ("text", {"type": "state", "state": "listening"})
        ping(ws)
        seen = until_state(ws, "listening")  # the turn ends back in listening
        t = texts(seen)
        assert t[0] == {"type": "transcript", "role": "user", "text": "hello jarvis", "final": True}
        assert {"type": "state", "state": "thinking"} in t
        assert {"type": "transcript", "role": "assistant", "text": "Hi. How can I help?", "final": True} in t
        assert audio(seen) == [b"audio:Hi.", b"audio:How can I help?"]
        # first audio arrives while still "speaking", before the turn is over
        first_audio = next(i for i, (k, _) in enumerate(seen) if k == "bytes")
        speaking = next(i for i, (k, v) in enumerate(seen) if k == "text" and v.get("state") == "speaking")
        assert speaking < first_audio < len(seen) - 1
    assert h.stt.streams[0].closed


def test_empty_reply_is_still_spoken():
    h = build({"fast": [AIMessage("chat"), AIMessage("")]}, stt_script=[FINAL("hi")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        until_state(ws, "listening")
        ping(ws)
        seen = until_state(ws, "listening")
        assert audio(seen) and h.tts.spoken[0].startswith("Finished, but I have no summary")


def test_second_connection_replaces_the_first():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c:
        with c.websocket_connect("/voice", headers=AUTH) as w1:
            read(w1)
            with c.websocket_connect("/voice", headers=AUTH) as w2:
                read(w2)
                with pytest.raises(WebSocketDisconnect) as e:
                    for _ in range(10):
                        read(w1)
                assert e.value.code == 4000
                assert len(h.stt.streams) == 2
    assert all(s.closed for s in h.stt.streams)  # both pipelines are released once the sockets are gone


def test_hello_stores_the_fcm_token():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_json({"type": "hello", "fcm_token": "tok-1"})
        ws.send_json({"type": "speak", "text": "Ping."})  # round trip so the hello has been processed
        until(ws, "state")
        until_state(ws, "listening")
    assert h.devices.fcm == {1: "tok-1"}


def test_bye_closes_the_socket():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_json({"type": "bye"})
        with pytest.raises(WebSocketDisconnect):
            for _ in range(5):
                read(ws)
    assert h.stt.streams[0].closed
```


- [ ] **Step 2: Run to verify failure** — `pytest tests/test_voice_ws.py -q` → FAIL (`jarvis.voice.ws` missing).

- [ ] **Step 3: Implement** — `src/jarvis/voice/ws.py`

```python
import asyncio
import contextlib
import json
import logging

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from starlette.websockets import WebSocket

from jarvis.agent.graph import turn_replies
from jarvis.channels.telegram import EMPTY_TEXT, FAIL_TEXT, THREAD
from jarvis.voice import protocol as P
from jarvis.voice.confirm import match_confirmation
from jarvis.voice.tts import split_sentences

log = logging.getLogger(__name__)

# One thread for every channel (FR-1). The "voice" flag only selects the fast model tier inside the graph.
VOICE_CFG = {**THREAD, "configurable": {**THREAD["configurable"], "voice": True}}
TAP_ONLY = {"send_draft"}  # irreversible and third-party-facing: a spoken yes is never enough
PENDING_TEXT = "Confirm or cancel the pending action first."
TAP_TEXT = "Tap Confirm on the screen to send."
STT_DOWN_TEXT = "I can't hear you right now."
WARN_TEXT = "Warning: proposed after reading email content. "
HANDLED_TEXT = "Already handled."


def card_lines(payload: dict) -> list[str]:
    return [a.get("summary") or f"{a['tool']}: {json.dumps(a['args'], ensure_ascii=False)}" for a in payload["actions"]]


class VoiceSession:
    def __init__(self, ws: WebSocket, graph, devices, device_id: int, stt, tts):
        self.ws, self.graph, self.devices, self.device_id, self.stt, self.tts = ws, graph, devices, device_id, stt, tts
        self.state = "listening"
        self.turn: asyncio.Task | None = None
        self.utterance_bytes = 0

    # --- output ---
    async def send(self, type_: str, **fields) -> bool:
        try:
            await self.ws.send_text(P.frame(type_, **fields))
            return True
        except Exception:  # socket already gone
            return False

    async def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            if state == "listening":
                self.utterance_bytes = 0
            await self.send("state", state=state)

    async def close(self, code: int) -> None:
        with contextlib.suppress(Exception):
            await self.ws.close(code=code)

    async def _say(self, texts: list[str]) -> None:
        sentences = [s for t in texts for s in split_sentences(t)]
        for t in texts:
            await self.send("transcript", role="assistant", text=t, final=True)
        if sentences:
            await self.set_state("speaking")
            try:
                async for chunk in self.tts.synth(sentences):
                    await self.ws.send_bytes(chunk)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tts failed")
                await self.send("error", message="Speech output failed; the reply is in the transcript.")
        await self.set_state("listening")

    # --- turn control ---
    async def _stop_turn(self) -> None:
        t, self.turn = self.turn, None
        if t and not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _start(self, coro) -> None:
        await self._stop_turn()
        self.turn = asyncio.create_task(self._guarded(coro))

    async def _guarded(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._failed()

    async def _failed(self) -> None:
        log.exception("voice turn failed")
        await self.send("error", message=FAIL_TEXT)
        await self._say([FAIL_TEXT])
        with contextlib.suppress(Exception):  # buttons may be gone: re-offer a still-pending confirmation
            state = await self.graph.aget_state(VOICE_CFG)
            if state.interrupts:
                await self._card(state.interrupts[0])

    async def _utterance(self, text: str) -> None:
        await self.set_state("thinking")
        await self._invoke({"messages": [HumanMessage(text)]})

    async def _invoke(self, graph_input) -> None:
        result = await self.graph.ainvoke(graph_input, VOICE_CFG)
        await self._present(result)

    async def _present(self, result: dict) -> None:
        interrupts = result.get("__interrupt__")
        if interrupts:
            await self._offer(interrupts[0])
            return
        await self._say(turn_replies(result["messages"]) or [EMPTY_TEXT])

    # --- confirmation cards (spoken-yes handling is added in Task 6) ---
    async def _card(self, it) -> None:
        payload = it.value
        await self.send("confirm_card", interrupt_id=it.id, summary="\n".join(card_lines(payload)),
                        tap_only=any(a["tool"] in TAP_ONLY for a in payload["actions"]),
                        after_untrusted=bool(payload.get("after_untrusted")))

    async def _offer(self, it) -> None:
        await self._card(it)
        payload = it.value
        tap_only = any(a["tool"] in TAP_ONLY for a in payload["actions"])
        spoken = (WARN_TEXT if payload.get("after_untrusted") else "") + "; ".join(card_lines(payload))
        await self._say([spoken + (". " + TAP_TEXT if tap_only else ". Say yes or no.")])

    # --- input ---
    async def _consume_events(self) -> None:
        try:
            async for ev in self.stt.events():
                if ev.kind == "partial":
                    await self.send("transcript", role="user", text=ev.text, final=False)
                else:
                    self.utterance_bytes = 0
                    await self.send("transcript", role="user", text=ev.text, final=True)
                    await self._start(self._utterance(ev.text))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("stt failed")
            await self._stt_failed()

    async def _stt_failed(self) -> None:
        await self.send("error", message="Speech recognition is unavailable.")
        with contextlib.suppress(Exception):
            await self._say([STT_DOWN_TEXT])
        await self.close(P.CLOSE_UPSTREAM)

    async def _audio(self, data: bytes) -> bool:
        if len(data) > P.MAX_AUDIO_FRAME:
            await self.send("error", message="Audio frame too large.")
            await self.close(P.CLOSE_TOO_BIG)
            return False
        if self.state == "listening":  # the cap bounds how long the mic can stay open, not reply playback
            self.utterance_bytes += len(data)
            if self.utterance_bytes > P.MAX_UTTERANCE_BYTES:
                await self.send("error", message="Utterance too long.")
                await self.close(P.CLOSE_TOO_LONG)
                return False
        await self.stt.send(data)
        return True

    async def _control(self, text: str) -> bool:
        m = P.parse(text)
        if m is None:
            await self.send("error", message="Bad frame.")
            return True
        kind = m["type"]
        if kind == "hello":
            fcm = m.get("fcm_token")
            if isinstance(fcm, str) and fcm:
                await asyncio.to_thread(self.devices.set_fcm, self.device_id, fcm)
        elif kind == "bye":
            return False
        else:
            await self.send("error", message=f"Unknown frame type: {kind}")
        return True

    async def run(self) -> None:
        events = asyncio.create_task(self._consume_events())
        try:
            await self.send("state", state="listening")
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    if not await self._audio(msg["bytes"]):
                        break
                elif msg.get("text") is not None:
                    if not await self._control(msg["text"]):
                        break
        except RuntimeError:  # receive() after the peer closed
            pass
        finally:
            events.cancel()
            await self._stop_turn()
            with contextlib.suppress(Exception):
                await self.stt.close()


class VoiceService:
    def __init__(self, graph, devices, stt, tts):
        self.graph, self.devices, self.stt, self.tts = graph, devices, stt, tts
        self.current: VoiceSession | None = None

    async def handle(self, ws: WebSocket) -> None:
        if self.stt is None or self.tts is None:
            await ws.close(code=P.CLOSE_UNAVAILABLE)
            return
        auth = ws.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        device_id = await asyncio.to_thread(self.devices.verify, token) if token else None
        if device_id is None:
            await ws.close(code=P.CLOSE_UNAUTHORIZED)  # before accept: no audio is ever read from a stranger
            return
        await ws.accept()
        try:
            stream = await self.stt.open()
        except Exception:
            log.exception("could not open stt")
            await ws.send_text(P.frame("error", message="Speech recognition is unavailable."))
            await ws.close(code=P.CLOSE_UPSTREAM)
            return
        session = VoiceSession(ws, self.graph, self.devices, device_id, stream, self.tts)
        old, self.current = self.current, session
        if old:
            await old.close(P.CLOSE_REPLACED)
        try:
            await session.run()
        finally:
            if self.current is session:
                self.current = None
            with contextlib.suppress(Exception):
                await ws.close()

    async def deliver(self, actions: list[dict]) -> bool:
        s = self.current
        return bool(s) and await s.send("client_actions", actions=actions)
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_voice_ws.py -q` → PASS; `pytest -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: /voice service with token auth, session replacement and the basic spoken turn"  # + trailers`

---

### Task 6: Spoken and tapped confirmations

**Files:**
- Modify: `src/jarvis/voice/ws.py`
- Test: `tests/test_voice_confirm.py`

**Interfaces:**
- Consumes: Task 5 (`_card`, `_offer`, `_say`, `_start`, `_invoke`, `_utterance`, `_control`).
- Produces: `_utterance` resumes a pending interrupt with `match_confirmation`; `_control` handles the `confirm` frame. Behaviour: bare yes/no resumes; anything else is refused with `PENDING_TEXT` and the card is re-sent; a spoken **yes** to a `send_draft` card is refused with `TAP_TEXT` (a spoken **no** still cancels, since declining is always safe); stale or malformed taps say `HANDLED_TEXT`.

- [ ] **Step 1: Write the failing tests** — `tests/test_voice_confirm.py`

```python
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from jarvis.tools.registry import Tool
from tests.test_graph import Args, call, tool
from tests.voice_helpers import (AUTH, FINAL, FakeDevices, build, audio, chat_scripts, ping, read, texts, until,
                                 until_state)

CAL = {"fast": [AIMessage("calendar"), call("create_event", {"summary": "Gym"}), AIMessage("Created.")]}


def gated(calls):
    return [tool("create_event", "calendar", calls)]


def ask(ws):
    ping(ws)
    seen = until_state(ws, "listening")
    return texts(seen, "confirm_card")[0], seen


def test_gated_action_sends_a_card_speaks_it_and_does_not_run():
    calls = []
    h = build(CAL, gated(calls), [FINAL("put gym at seven")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        card, seen = ask(ws)
        assert card["tap_only"] is False and card["after_untrusted"] is False and "create_event" in card["summary"]
        assert card["interrupt_id"] and calls == []
        assert h.tts.spoken[-1].endswith("Say yes or no.")


def test_spoken_yes_resumes_and_runs_once():
    calls = []
    h = build(CAL, gated(calls), [FINAL("put gym at seven"), None, FINAL("yes")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ask(ws)
        ping(ws)
        ping(ws)
        seen = until_state(ws, "listening")
        assert calls == [{"summary": "Gym"}]
        assert "Created." in h.tts.spoken


def test_lookalike_yes_does_not_resume_and_card_is_resent():
    calls = []
    h = build(CAL, gated(calls), [FINAL("put gym at seven"), FINAL("yes, and also delete everything")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        card, _ = ask(ws)
        ping(ws)
        seen = until_state(ws, "listening")
        assert calls == []
        assert texts(seen, "confirm_card")[0]["interrupt_id"] == card["interrupt_id"]
        assert h.tts.spoken[-1] == "Confirm or cancel the pending action first."


def test_spoken_no_cancels_and_nothing_runs():
    calls = []
    h = build({"fast": [AIMessage("calendar"), call("create_event", {"summary": "Gym"}), AIMessage("OK, cancelled.")]},
              gated(calls), [FINAL("put gym at seven"), FINAL("no")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ask(ws)
        ping(ws)
        until_state(ws, "listening")
        assert calls == [] and "OK, cancelled." in h.tts.spoken
        assert [r for r in h.audit.records if r["name"] == "create_event"][0]["confirmation"] == "cancelled"


def send_draft_harness(calls):
    return build({"fast": [AIMessage("gmail")], "strong": [call("send_draft", {}), AIMessage("Sent.")]},
                 [tool("send_draft", "gmail", calls)], [FINAL("send it"), FINAL("yes"), None, FINAL("no")])


def test_spoken_yes_never_confirms_send_draft_but_a_tap_does():
    calls = []
    h = send_draft_harness(calls)
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        card, _ = ask(ws)
        assert card["tap_only"] is True and h.tts.spoken[-1].endswith("Tap Confirm on the screen to send.")
        ping(ws)
        seen = until_state(ws, "listening")
        assert calls == [] and h.tts.spoken[-1] == "Tap Confirm on the screen to send."
        ws.send_json({"type": "confirm", "decision": "yes", "interrupt_id": card["interrupt_id"]})
        until_state(ws, "listening")
        assert calls == [{}] and "Sent." in h.tts.spoken


def test_spoken_no_still_cancels_a_send_draft_card():
    calls = []
    h = build({"fast": [AIMessage("gmail")], "strong": [call("send_draft", {}), AIMessage("Not sent.")]},
              [tool("send_draft", "gmail", calls)], [FINAL("send it"), FINAL("no")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ask(ws)
        ping(ws)
        until_state(ws, "listening")
        assert calls == [] and "Not sent." in h.tts.spoken


def test_tap_with_stale_or_missing_id_says_already_handled():
    calls = []
    h = build(CAL, gated(calls), [FINAL("put gym at seven")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        card, _ = ask(ws)
        for frame in ({"type": "confirm", "decision": "yes", "interrupt_id": "stale"},
                      {"type": "confirm", "decision": "yes"},
                      {"type": "confirm", "decision": "maybe", "interrupt_id": card["interrupt_id"]}):
            ws.send_json(frame)
            until_state(ws, "listening")
            assert h.tts.spoken[-1] == "Already handled."
        assert calls == []


def test_tap_confirm_runs_once_then_a_second_tap_is_already_handled():
    calls = []
    h = build(CAL, gated(calls), [FINAL("put gym at seven")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        card, _ = ask(ws)
        tap = {"type": "confirm", "decision": "yes", "interrupt_id": card["interrupt_id"]}
        ws.send_json(tap)
        until_state(ws, "listening")
        ws.send_json(tap)
        until_state(ws, "listening")
        assert calls == [{"summary": "Gym"}] and h.tts.spoken[-1] == "Already handled."


def test_untrusted_card_is_warned_in_the_frame_and_spoken():
    calls = []
    read_email = Tool(name="read_email", domain="gmail", description="d", args_schema=Args, needs_confirm=False,
                      untrusted=True, fn=lambda **kw: {"body": "hi"})
    h = build({"fast": [AIMessage("gmail")],
               "strong": [call("read_email", {}, id="c1"), call("send_draft", {}, id="c2"), AIMessage("Sent.")]},
              [read_email, tool("send_draft", "gmail", calls)], [FINAL("reply to that")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        card, _ = ask(ws)
        assert card["after_untrusted"] is True
        assert h.tts.spoken[-1].startswith("Warning: proposed after reading email content.")
        assert calls == []


def test_failed_turn_reports_error_and_reoffers_pending_card():
    pending = SimpleNamespace(id="i1", value={"actions": [{"tool": "create_event", "args": {"summary": "Gym"}}]})

    class StubGraph:
        def __init__(self):
            self.states = [SimpleNamespace(interrupts=[]), SimpleNamespace(interrupts=[pending])]

        async def aget_state(self, cfg):
            return self.states.pop(0) if len(self.states) > 1 else self.states[0]

        async def ainvoke(self, inp, cfg):
            raise RuntimeError("boom")

    h = build(chat_scripts("x"), stt_script=[FINAL("hello")])
    h.svc.graph = StubGraph()
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = until(ws, "confirm_card")
        assert any(t["type"] == "error" for t in texts(seen))
        assert texts(seen, "confirm_card")[0]["interrupt_id"] == "i1"
        assert h.tts.spoken[0] == "Something went wrong. Please try again."
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_voice_confirm.py -q` → FAIL (voice cannot resume; `confirm` frame unknown).

- [ ] **Step 3: Implement** — edit `src/jarvis/voice/ws.py`

Replace `_utterance` with:

```python
    async def _utterance(self, text: str) -> None:
        await self.set_state("thinking")
        pending = (await self.graph.aget_state(VOICE_CFG)).interrupts
        if pending:
            await self._spoken_decision(text, pending[0])
            return
        await self._invoke({"messages": [HumanMessage(text)]})

    async def _spoken_decision(self, text: str, it) -> None:
        decision = match_confirmation(text)
        tap_only = any(a["tool"] in TAP_ONLY for a in it.value["actions"])
        if decision is None or (decision and tap_only):
            await self._card(it)  # re-show the card; nothing was decided
            await self._say([TAP_TEXT if decision else PENDING_TEXT])
            return
        await self._invoke(Command(resume=decision))

    async def _tap(self, m: dict) -> None:
        await self.set_state("thinking")
        decision = m.get("decision")
        interrupts = (await self.graph.aget_state(VOICE_CFG)).interrupts
        if decision not in ("yes", "no") or not interrupts or interrupts[0].id != m.get("interrupt_id"):
            await self._say([HANDLED_TEXT])  # stale, repeated or bare taps never resume anything
            return
        await self._invoke(Command(resume=decision == "yes"))
```

In `_control`, add before the `elif kind == "bye"` branch:

```python
        elif kind == "confirm":
            await self._start(self._tap(m))
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_voice_confirm.py tests/test_voice_ws.py -q` → PASS; `pytest -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: spoken and tapped confirmations with tap-only send_draft"  # + trailers`

---

### Task 7: Barge-in, caps, failure handling, `speak` request

**Files:**
- Modify: `src/jarvis/voice/ws.py`
- Test: `tests/test_voice_robust.py`

**Interfaces:**
- Consumes: Tasks 5-6.
- Produces: `cancel` frame and speech-during-playback stop the turn and TTS (and the partial transcript frame is sent **before** the stop, so the app can flush its player on seeing user text while `speaking`); `speak` frame voices text without the graph; STT failure, TTS failure and oversize/garbage inputs end cleanly.

- [ ] **Step 1: Write the failing tests** — `tests/test_voice_robust.py`

```python
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.voice import protocol as P
from tests.fakes import FakeSTT, FakeTTS
from tests.test_graph import call, tool
from tests.voice_helpers import (AUTH, FINAL, PARTIAL, build, audio, chat_scripts, ping, read, texts, until,
                                 until_state)

from langchain_core.messages import AIMessage


def test_speech_during_playback_cancels_tts_and_sends_the_partial_first():
    h = build(chat_scripts("One. Two. Three."), stt_script=[FINAL("hi"), PARTIAL("wait")], tts=FakeTTS(stall=True))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = []
        while not audio(seen):
            seen.append(read(ws))
        ping(ws)  # the partial arrives while speaking
        after = until_state(ws, "listening")
        assert h.tts.cancelled and audio(after) == []
        # the user's partial text precedes the state change so the app can flush its player
        partial_at = next(i for i, (k, v) in enumerate(after) if k == "text" and v["type"] == "transcript")
        assert partial_at == 0


def test_cancel_frame_stops_playback():
    h = build(chat_scripts("One. Two."), stt_script=[FINAL("hi")], tts=FakeTTS(stall=True))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = []
        while not audio(seen):
            seen.append(read(ws))
        ws.send_json({"type": "cancel"})
        until_state(ws, "listening")
        assert h.tts.cancelled


def test_partial_while_not_speaking_does_not_cancel_anything():
    h = build(chat_scripts("Hi."), stt_script=[PARTIAL("um")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = until(ws, "transcript")
        assert texts(seen, "transcript")[0]["final"] is False and not h.tts.cancelled


def test_executed_write_stays_audited_when_the_user_barges_in_afterwards():
    calls = []
    h = build({"fast": [AIMessage("calendar"), call("create_event", {"summary": "Gym"}), AIMessage("Created. Done.")]},
              [tool("create_event", "calendar", calls)],
              [FINAL("put gym at seven"), None, FINAL("yes"), PARTIAL("stop")], tts=FakeTTS(stall=True))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        until(ws, "confirm_card")
        ws.send_json({"type": "cancel"})  # leave the card's speech
        until_state(ws, "listening")
        ping(ws)
        ping(ws)
        seen = []
        while "Created." not in h.tts.spoken:
            seen.append(read(ws))
        ping(ws)
        until_state(ws, "listening")
        assert calls == [{"summary": "Gym"}]
        rec = [r for r in h.audit.records if r["name"] == "create_event"]
        assert len(rec) == 1 and rec[0]["confirmation"] == "approved"


def test_dropped_socket_mid_card_leaves_the_confirmation_pending_for_other_channels():
    calls = []
    h = build({"fast": [AIMessage("calendar"), call("create_event", {"summary": "Gym"})]},
              [tool("create_event", "calendar", calls)], [FINAL("put gym at seven")])
    with TestClient(h.app) as c:
        with c.websocket_connect("/voice", headers=AUTH) as ws:
            read(ws)
            ping(ws)
            until(ws, "confirm_card")
        from jarvis.voice.ws import VOICE_CFG
        state = c.portal.call(h.graph.aget_state, VOICE_CFG)
        assert state.interrupts and calls == []
        assert h.svc.current is None


def test_oversized_frame_closes_1009():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_bytes(b"\x00" * (P.MAX_AUDIO_FRAME + 1))
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(5):
                read(ws)
        assert e.value.code == 1009


def test_utterance_cap_closes_4408(monkeypatch):
    monkeypatch.setattr(P, "MAX_UTTERANCE_BYTES", 1000)
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws, 640)
        ping(ws, 640)
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(5):
                read(ws)
        assert e.value.code == 4408


@pytest.mark.parametrize("payload", ["not json", "[]", '{"type": 3}', '{"type": "launch_missiles"}'])
def test_garbage_text_frames_get_an_error_and_the_session_survives(payload):
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_text(payload)
        assert until(ws, "error")
        ws.send_json({"type": "speak", "text": "Still alive."})
        until_state(ws, "listening")
        assert h.tts.spoken == ["Still alive."]


def test_speak_voices_text_without_running_the_graph():
    h = build(chat_scripts("never used"))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_json({"type": "speak", "text": "Good morning. Here is your brief."})
        seen = until_state(ws, "listening")
        assert audio(seen) == [b"audio:Good morning.", b"audio:Here is your brief."]
        assert h.audit.records == []


@pytest.mark.parametrize("text", ["", "   ", None, 5, "x" * (P.MAX_SPEAK_CHARS + 1)])
def test_bad_speak_text_is_an_error(text):
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ws.send_json({"type": "speak", "text": text})
        assert until(ws, "error") and h.tts.spoken == []


def test_stt_failure_during_session_sends_error_speaks_fallback_and_closes():
    h = build(chat_scripts("Hi."), stt=FakeSTT(fail_events=True))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(20):
                read(ws)
        assert e.value.code == 1011 and h.tts.spoken == ["I can't hear you right now."]


def test_stt_open_failure_sends_error_and_closes_1011():
    h = build(chat_scripts("Hi."), stt=FakeSTT(fail_open=True))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        kind, frame = read(ws)
        assert frame["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as e:
            read(ws)
        assert e.value.code == 1011


def test_tts_failure_keeps_the_transcript_reports_error_and_stays_open():
    h = build(chat_scripts("Hi there."), stt_script=[FINAL("hello")], tts=FakeTTS(fail=True))
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = until_state(ws, "listening")
        t = texts(seen)
        assert {"type": "transcript", "role": "assistant", "text": "Hi there.", "final": True} in t
        assert any(x["type"] == "error" for x in t) and audio(seen) == []
        ws.send_json({"type": "speak", "text": "Again."})  # session still usable
        until(ws, "error")
```


- [ ] **Step 2: Run to verify failure** — `pytest tests/test_voice_robust.py -q` → FAIL (no cancel/speak handling; partial does not cancel).

- [ ] **Step 3: Implement** — edit `src/jarvis/voice/ws.py`

Add constant `MAX_SPEAK = P.MAX_SPEAK_CHARS` is unnecessary; use `P.MAX_SPEAK_CHARS` directly.

In `_consume_events`, replace the partial branch with:

```python
                if ev.kind == "partial":
                    await self.send("transcript", role="user", text=ev.text, final=False)  # first: the app flushes on it
                    if self.state == "speaking":  # speech over playback = barge-in
                        await self._stop_turn()
                        await self.set_state("listening")
```

Add method:

```python
    async def _speak_text(self, m: dict) -> None:
        text = m.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > P.MAX_SPEAK_CHARS:
            await self.send("error", message="speak needs text of 1 to 2000 characters.")
            return
        await self._say([text])
```

In `_control` add before `elif kind == "bye"`:

```python
        elif kind == "cancel":
            await self._stop_turn()
            await self.set_state("listening")
        elif kind == "speak":
            await self._start(self._speak_text(m))
```

(`cancel` while a turn is already idle is harmless: `_stop_turn` is a no-op and `set_state` only emits on change.)

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_voice_robust.py tests/test_voice_confirm.py tests/test_voice_ws.py -q` → PASS; `pytest -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: voice barge-in, caps, failure paths and speak request"  # + trailers`

---

### Task 8: Client-action delivery (voice and Telegram)

**Files:**
- Modify: `src/jarvis/voice/ws.py`, `src/jarvis/channels/telegram.py`
- Test: `tests/test_voice_actions.py`, additions to `tests/test_telegram.py`

**Interfaces:**
- Consumes: `VoiceService.deliver(actions) -> bool` (Task 5), `State.client_actions` in the graph result.
- Produces: voice turns send a `client_actions` frame (before the reply is spoken) when the turn produced actions; `TelegramChannel(graph, owner_chat_id, deliver_actions=None)` forwards a Telegram turn's actions via `await deliver_actions(actions)` and, when that returns falsy (or is unset), sends `PHONE_OFFLINE_TEXT` to the chat.

- [ ] **Step 1: Write the failing tests**

`tests/test_voice_actions.py`:

```python
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from jarvis.tools.phone_tools import register_phone_tools
from jarvis.tools.registry import Registry
from tests.fakes import FakeSTT, FakeTTS
from tests.test_graph import call, tool
from tests.voice_helpers import AUTH, FINAL, build, audio, ping, read, texts, until_state

ALARM = {"fast": [AIMessage("phone"), call("set_alarm", {"hour": 6, "minute": 0}),
                  AIMessage("Asking your phone to set a 06:00 alarm.")]}


def phone_tools():
    r = Registry()
    register_phone_tools(r)
    return r._tools.values()


def test_client_actions_frame_arrives_before_the_reply_is_spoken():
    h = build(ALARM, phone_tools(), [FINAL("set an alarm for six")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        seen = until_state(ws, "listening")
        frames = texts(seen, "client_actions")
        assert frames == [{"type": "client_actions", "actions": [{"type": "set_alarm", "hour": 6, "minute": 0}]}]
        order = [("a" if k == "bytes" else v["type"]) for k, v in seen]
        assert order.index("client_actions") < order.index("a")


def test_turn_without_actions_sends_no_frame():
    h = build({"fast": [AIMessage("chat"), AIMessage("Hi.")]}, (), [FINAL("hello")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        assert texts(until_state(ws, "listening"), "client_actions") == []


def test_deliver_reaches_the_connected_session_and_reports_false_without_one():
    h = build({"fast": [AIMessage("chat"), AIMessage("Hi.")]})
    with TestClient(h.app) as c:
        assert c.portal.call(h.svc.deliver, [{"type": "set_timer", "seconds": 60}]) is False
        with c.websocket_connect("/voice", headers=AUTH) as ws:
            read(ws)
            assert c.portal.call(h.svc.deliver, [{"type": "set_timer", "seconds": 60}]) is True
            assert read(ws) == ("text", {"type": "client_actions", "actions": [{"type": "set_timer", "seconds": 60}]})
```

Append to `tests/test_telegram.py` (uses the file's existing `chat`, `text_update`, `sent`, `make_channel`, `Args`, `Tool`; add `from jarvis.tools.phone_tools import register_phone_tools` is not needed — build the tool inline):

```python
def alarm_tool():
    return Tool(name="set_alarm", domain="phone", description="d", args_schema=Args, needs_confirm=False,
                fn=lambda **kw: {"queued_for_phone": True, "client_action": {"type": "set_alarm", "hour": 6, "minute": 0}})


def alarm_scripts():
    call_ = AIMessage("", tool_calls=[{"name": "set_alarm", "args": {}, "id": "1", "type": "tool_call"}])
    return {"fast": [AIMessage("phone"), call_, AIMessage("Asking your phone to set the alarm.")]}


async def test_phone_action_is_forwarded_when_a_voice_client_is_connected():
    got = []

    async def deliver(actions):
        got.append(actions)
        return True

    ch = make_channel(alarm_scripts(), [], extra=[alarm_tool()])
    ch.deliver_actions = deliver
    c = chat()
    await ch.on_text(text_update(c, "alarm at 6"), None)
    assert got == [[{"type": "set_alarm", "hour": 6, "minute": 0}]]
    assert sent(c) == [("Asking your phone to set the alarm.", {})]


async def test_phone_action_without_a_voice_client_tells_the_user_it_was_not_run():
    from jarvis.channels.telegram import PHONE_OFFLINE_TEXT
    ch = make_channel(alarm_scripts(), [], extra=[alarm_tool()])
    c = chat()
    await ch.on_text(text_update(c, "alarm at 6"), None)
    assert sent(c) == [("Asking your phone to set the alarm.", {}), (PHONE_OFFLINE_TEXT, {})]
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_voice_actions.py tests/test_telegram.py -q` → FAIL.

- [ ] **Step 3: Implement**

`src/jarvis/voice/ws.py` — replace `_present` with:

```python
    async def _present(self, result: dict) -> None:
        interrupts = result.get("__interrupt__")
        if interrupts:
            await self._offer(interrupts[0])
            return
        if result.get("client_actions"):  # before the speech, so an alarm is set while Jarvis is still talking
            await self.send("client_actions", actions=result["client_actions"])
        await self._say(turn_replies(result["messages"]) or [EMPTY_TEXT])
```

`src/jarvis/channels/telegram.py`:
- add constant `PHONE_OFFLINE_TEXT = "Phone action not run: the Jarvis voice app is not connected."`
- change `__init__` to `def __init__(self, graph, owner_chat_id: int, deliver_actions=None):` and store `self.deliver_actions = deliver_actions`.
- in `_run`, replace the last loop with:

```python
        for text in turn_replies(result["messages"]) or [EMPTY_TEXT]:
            await chat.send_message(text)
        if result.get("client_actions"):
            delivered = bool(self.deliver_actions) and await self.deliver_actions(result["client_actions"])
            if not delivered:
                await chat.send_message(PHONE_OFFLINE_TEXT)
```

- [ ] **Step 4: Run to verify pass** — `pytest -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: deliver phone client actions to the voice app, tell Telegram when the phone is offline"  # + trailers`

---

### Task 9: Wire voice into the app, FCM push and its CLI

**Files:**
- Create: `src/jarvis/voice/push.py`
- Modify: `src/jarvis/main.py`, `tests/test_main.py`
- Test: `tests/test_voice_push.py`, addition to `tests/test_main.py`

**Interfaces:**
- Consumes: `VoiceService`, `Devices`, `DeepgramSTT`, `CartesiaTTS`, `Settings` fields.
- Produces: `send_push(project_id, access_token, tokens, text, client) -> int` (count delivered); `fcm_access_token(path) -> tuple[str, str]`; CLI `python -m jarvis.voice.push "text"`; a `/voice` WebSocket route on the app backed by `app.state.voice`, set in `lifespan`.

- [ ] **Step 1: Write the failing tests**

`tests/test_voice_push.py`:

```python
import httpx

from jarvis.voice.push import send_push


def test_posts_one_message_per_token_with_text_in_data_and_counts_successes():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = req.read().decode()
        seen.append((str(req.url), req.headers["authorization"], body))
        return httpx.Response(200 if "tok-ok" in body else 404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    n = send_push("proj-1", "AT", ["tok-ok", "tok-gone"], "Good morning. Brief here.", client)
    assert n == 1 and len(seen) == 2
    url, auth, body = seen[0]
    assert url == "https://fcm.googleapis.com/v1/projects/proj-1/messages:send" and auth == "Bearer AT"
    assert '"speak": "Good morning. Brief here."' in body and '"priority": "high"' in body


def test_no_tokens_sends_nothing():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError("no call"))))
    assert send_push("p", "AT", [], "x", client) == 0
```

Append to `tests/test_main.py`:

```python
def test_voice_route_is_closed_until_the_lifespan_has_built_the_service():
    from starlette.websockets import WebSocketDisconnect
    app = create_app(with_lifespan=False)
    with pytest.raises(WebSocketDisconnect) as e:
        with TestClient(app).websocket_connect("/voice"):
            pass
    assert e.value.code == 1013
```

Also in `tests/test_main.py`, every `SimpleNamespace(...)` that stands in for settings (`grep -n "SimpleNamespace(" tests/test_main.py`) must gain `deepgram_api_key="", cartesia_api_key="", cartesia_voice_id="", fcm_credentials_path=""`.

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_voice_push.py tests/test_main.py -q` → FAIL.

- [ ] **Step 3: Implement**

`src/jarvis/voice/push.py`:

```python
import argparse
import logging

import httpx

from jarvis.config import get_settings
from jarvis.db import init_schema, make_pool
from jarvis.voice.devices import Devices

log = logging.getLogger(__name__)
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def fcm_access_token(credentials_path: str) -> tuple[str, str]:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=[FCM_SCOPE])
    creds.refresh(Request())
    return creds.project_id, creds.token


def send_push(project_id: str, access_token: str, tokens: list[str], text: str, client: httpx.Client) -> int:
    """Notification + the full text in data; tapping it opens the app, which asks the server to speak `text`."""
    sent = 0
    for t in tokens:
        r = client.post(f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
                        headers={"Authorization": f"Bearer {access_token}"},
                        json={"message": {"token": t, "notification": {"title": "Jarvis", "body": text[:200]},
                                          "data": {"speak": text}, "android": {"priority": "high"}}})
        if r.is_success:
            sent += 1
        else:
            log.warning("fcm push failed: %s %s", r.status_code, r.text[:200])
    return sent


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m jarvis.voice.push", description="Send a tap-to-play push to the paired phone.")
    p.add_argument("text")
    text = p.parse_args().text
    s = get_settings()
    if not s.fcm_credentials_path:
        raise SystemExit("Set JARVIS_FCM_CREDENTIALS_PATH first.")
    pool = make_pool(s.database_url)
    try:
        init_schema(pool)
        tokens = Devices(pool).fcm_tokens()
    finally:
        pool.close()
    project, access = fcm_access_token(s.fcm_credentials_path)
    with httpx.Client(timeout=10) as client:
        print(f"Delivered to {send_push(project, access, tokens, text, client)} of {len(tokens)} device(s).")


if __name__ == "__main__":
    main()
```

`src/jarvis/main.py`:
- imports: `from fastapi import FastAPI, WebSocket`; `from jarvis.voice.devices import Devices`; `from jarvis.voice.stt import DeepgramSTT`; `from jarvis.voice.tts import CartesiaTTS`; `from jarvis.voice.ws import VoiceService`.
- in `lifespan`, right after `graph = build_graph(...)` and before `tg = ...`:

```python
            voice = VoiceService(
                graph, Devices(pool),
                DeepgramSTT(s.deepgram_api_key) if s.deepgram_api_key else None,
                CartesiaTTS(s.cartesia_api_key, s.cartesia_voice_id) if s.cartesia_api_key and s.cartesia_voice_id else None)
            app.state.voice = voice
```
- change the Telegram construction to `TelegramChannel(graph, s.telegram_owner_chat_id, deliver_actions=voice.deliver).build(s.telegram_bot_token)`.
- in `create_app`, after `/health`:

```python
    @app.websocket("/voice")
    async def voice(ws: WebSocket):
        svc = getattr(app.state, "voice", None)
        if svc is None:  # lifespan has not built it (or voice is disabled)
            await ws.close(code=1013)
            return
        await svc.handle(ws)
```

- [ ] **Step 4: Run to verify pass** — `TEST_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis_test pytest -q` → PASS (all).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: mount /voice in the app, FCM push sender and CLI"  # + trailers`

---

### Task 10: Python test client

**Files:**
- Create: `src/jarvis/voice/client.py`
- Test: `tests/test_voice_client.py`

**Interfaces:**
- Produces: `read_wav(path) -> bytes` (raises `ValueError` unless 16 kHz mono 16-bit), `chunks(pcm, size=640) -> Iterator[bytes]`, CLI `python -m jarvis.voice.client URL TOKEN file.wav [--confirm yes|no] [--wait 15] [--out reply.pcm]`.

- [ ] **Step 1: Write the failing test** — `tests/test_voice_client.py`

```python
import wave

import pytest

from jarvis.voice.client import chunks, read_wav


def write(path, rate=16000, channels=1, width=2, frames=1600):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * frames * channels if width == 2 else b"\x01" * frames)


def test_read_wav_returns_raw_pcm(tmp_path):
    write(tmp_path / "a.wav")
    assert len(read_wav(tmp_path / "a.wav")) == 3200


@pytest.mark.parametrize("kw", [{"rate": 44100}, {"channels": 2}, {"width": 1}])
def test_read_wav_rejects_other_formats(tmp_path, kw):
    write(tmp_path / "b.wav", **kw)
    with pytest.raises(ValueError):
        read_wav(tmp_path / "b.wav")


def test_chunks_cover_the_audio_in_20ms_frames():
    pcm = b"\x00" * 1500
    out = list(chunks(pcm))
    assert [len(c) for c in out] == [640, 640, 220] and b"".join(out) == pcm
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_voice_client.py -q` → FAIL.

- [ ] **Step 3: Implement** — `src/jarvis/voice/client.py`

```python
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
```

- [ ] **Step 4: Run to verify pass** — `pytest -q` → PASS (whole suite). Also run `python -m jarvis.voice.client --help` → prints usage.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: python test client for the voice endpoint"  # + trailers`


---

## App (Flutter, `jarvis_app/`)

Verification available in this environment: `flutter analyze`, `flutter test`, and a debug build (`flutter build apk --debug`). Wake word, screen wake, intents, assistant role, push and echo behaviour only show on the Pixel (Task 17 rows).

### Task 11: Scaffold and the wire protocol

**Files:**
- Create: `jarvis_app/` via `flutter create`, `jarvis_app/lib/protocol.dart`, `jarvis_app/lib/config.dart`
- Test: `jarvis_app/test/protocol_test.dart`, `jarvis_app/test/config_test.dart`

**Interfaces:**
- Produces: sealed `ServerEvent` with `StateEvent(state)`, `TranscriptEvent(role, text, isFinal)`, `ConfirmCardEvent(interruptId, summary, tapOnly, afterUntrusted)`, `ClientActionsEvent(actions)`, `ErrorEvent(message)`, `AudioEvent(pcm)`; `ServerEvent? decodeServer(Object? raw)`; `String encode(String type, [Map<String, dynamic> fields])`; `Config(url, token)` with `Uri get voiceUri`.

- [ ] **Step 1: Scaffold** (from the repo root)

```bash
flutter create --org com.jarvis --project-name jarvis_app --platforms android jarvis_app
cd jarvis_app
flutter pub add web_socket_channel record flutter_secure_storage porcupine_flutter flutter_foreground_task \
  flutter_local_notifications android_intent_plus flutter_contacts firebase_core firebase_messaging flutter_tts \
  permission_handler flutter_sound
flutter pub add --dev fake_async
rm test/widget_test.dart
cd .. && printf '\njarvis_app/build/\njarvis_app/.dart_tool/\njarvis_app/android/app/google-services.json\njarvis_app/android/local.properties\n' >> .gitignore
```

- [ ] **Step 2: Write the failing tests**

`jarvis_app/test/protocol_test.dart`:

```dart
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/protocol.dart';

void main() {
  test('binary frames are audio', () {
    final e = decodeServer(Uint8List.fromList([1, 2, 3]));
    expect(e, isA<AudioEvent>());
    expect((e as AudioEvent).pcm, [1, 2, 3]);
  });

  test('state', () {
    final e = decodeServer('{"type":"state","state":"speaking"}') as StateEvent;
    expect(e.state, 'speaking');
  });

  test('transcript', () {
    final e = decodeServer('{"type":"transcript","role":"user","text":"hi","final":false}') as TranscriptEvent;
    expect((e.role, e.text, e.isFinal), ('user', 'hi', false));
  });

  test('confirm card', () {
    final e = decodeServer(jsonEncode({
      'type': 'confirm_card', 'interrupt_id': 'i1', 'summary': 'Create Gym', 'tap_only': true, 'after_untrusted': false,
    })) as ConfirmCardEvent;
    expect((e.interruptId, e.summary, e.tapOnly, e.afterUntrusted), ('i1', 'Create Gym', true, false));
  });

  test('client actions', () {
    final e = decodeServer('{"type":"client_actions","actions":[{"type":"set_alarm","hour":6,"minute":0}]}')
        as ClientActionsEvent;
    expect(e.actions, [
      {'type': 'set_alarm', 'hour': 6, 'minute': 0}
    ]);
  });

  test('error', () {
    expect((decodeServer('{"type":"error","message":"boom"}') as ErrorEvent).message, 'boom');
  });

  test('garbage and unknown frames decode to null', () {
    for (final raw in ['', 'nope', '[]', '{"type":3}', '{"type":"mystery"}', '{"type":"state"}', 42, null]) {
      expect(decodeServer(raw), isNull, reason: '$raw');
    }
  });

  test('encode makes a typed object', () {
    expect(jsonDecode(encode('confirm', {'decision': 'yes', 'interrupt_id': 'i1'})),
        {'type': 'confirm', 'decision': 'yes', 'interrupt_id': 'i1'});
    expect(jsonDecode(encode('cancel')), {'type': 'cancel'});
  });
}
```

`jarvis_app/test/config_test.dart`:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/config.dart';

void main() {
  test('https becomes wss on /voice', () {
    expect(const Config('https://jarvis.example.com', 't').voiceUri.toString(), 'wss://jarvis.example.com/voice');
  });
  test('http becomes ws and a trailing slash is tolerated', () {
    expect(const Config('http://192.168.1.5:8000/', 't').voiceUri.toString(), 'ws://192.168.1.5:8000/voice');
  });
  test('a bare host is treated as https', () {
    expect(const Config('jarvis.example.com', 't').voiceUri.toString(), 'wss://jarvis.example.com/voice');
  });
}
```

- [ ] **Step 3: Run to verify failure** — `cd jarvis_app && flutter test` → FAIL (files missing).

- [ ] **Step 4: Implement**

`jarvis_app/lib/protocol.dart`:

```dart
import 'dart:convert';
import 'dart:typed_data';

sealed class ServerEvent {
  const ServerEvent();
}

class StateEvent extends ServerEvent {
  const StateEvent(this.state);
  final String state;
}

class TranscriptEvent extends ServerEvent {
  const TranscriptEvent(this.role, this.text, this.isFinal);
  final String role, text;
  final bool isFinal;
}

class ConfirmCardEvent extends ServerEvent {
  const ConfirmCardEvent(
      {required this.interruptId, required this.summary, required this.tapOnly, required this.afterUntrusted});
  final String interruptId, summary;
  final bool tapOnly, afterUntrusted;
}

class ClientActionsEvent extends ServerEvent {
  const ClientActionsEvent(this.actions);
  final List<Map<String, dynamic>> actions;
}

class ErrorEvent extends ServerEvent {
  const ErrorEvent(this.message);
  final String message;
}

class AudioEvent extends ServerEvent {
  const AudioEvent(this.pcm);
  final Uint8List pcm;
}

ServerEvent? decodeServer(Object? raw) {
  try {
    if (raw is Uint8List) return AudioEvent(raw);
    if (raw is List<int>) return AudioEvent(Uint8List.fromList(raw));
    if (raw is! String) return null;
    final j = jsonDecode(raw);
    if (j is! Map<String, dynamic>) return null;
    switch (j['type']) {
      case 'state':
        return StateEvent(j['state'] as String);
      case 'transcript':
        return TranscriptEvent(j['role'] as String, j['text'] as String, j['final'] as bool);
      case 'confirm_card':
        return ConfirmCardEvent(
            interruptId: j['interrupt_id'] as String,
            summary: j['summary'] as String,
            tapOnly: j['tap_only'] as bool,
            afterUntrusted: j['after_untrusted'] as bool);
      case 'client_actions':
        return ClientActionsEvent((j['actions'] as List).cast<Map<String, dynamic>>());
      case 'error':
        return ErrorEvent(j['message'] as String);
      default:
        return null;
    }
  } catch (_) {
    return null; // a malformed frame must never crash the session
  }
}

String encode(String type, [Map<String, dynamic> fields = const {}]) => jsonEncode({'type': type, ...fields});
```

`jarvis_app/lib/config.dart`:

```dart
class Config {
  const Config(this.url, this.token);
  final String url, token;

  Uri get voiceUri {
    var u = url.trim().replaceAll(RegExp(r'/+$'), '');
    if (!u.contains('://')) u = 'https://$u';
    final p = Uri.parse(u);
    return p.replace(scheme: p.scheme == 'http' ? 'ws' : 'wss', path: '/voice');
  }
}
```

- [ ] **Step 5: Run to verify pass** — `cd jarvis_app && flutter test && flutter analyze` → PASS, no issues.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(app): flutter scaffold, wire protocol codec, pairing config"  # + trailers`

---

### Task 12: Voice activity detector and the session controller

**Files:**
- Create: `jarvis_app/lib/vad.dart`, `jarvis_app/lib/session.dart`
- Test: `jarvis_app/test/vad_test.dart`, `jarvis_app/test/fakes.dart`, `jarvis_app/test/session_test.dart`

**Interfaces:**
- Consumes: Task 11 types.
- Produces: `Vad({threshold = 1500, frames = 3}).feed(Uint8List pcm) -> bool`; ports `VoiceSocket`, `Mic`, `Player`, `PhoneActions`, `Speaker`; `SessionController` (a `ChangeNotifier`) with `phase`, `userText`, `jarvisText`, `card`, `error`, `start({String? speakText})`, `confirm(bool yes)`, `stop()`; `Phase { idle, connecting, listening, thinking, speaking, offline }`.
- Behaviour pinned by the tests below: a user transcript while `speaking` flushes the player; local speech while `speaking` sends `cancel` and flushes; silence timeout 8 s (30 s while a card is pending, none while thinking/speaking); offline fallback line `Jarvis is offline.`; unexpected close says `Jarvis connection lost.`; phone-action errors are shown, not sent upstream (the protocol has no upstream error frame).

- [ ] **Step 1: Write the failing tests**

`jarvis_app/test/vad_test.dart`:

```dart
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/vad.dart';

Uint8List pcm(int amp, [int samples = 320]) {
  final b = ByteData(samples * 2);
  for (var i = 0; i < samples; i++) {
    b.setInt16(i * 2, i.isEven ? amp : -amp, Endian.little);
  }
  return b.buffer.asUint8List();
}

void main() {
  test('silence never triggers', () {
    final v = Vad();
    for (var i = 0; i < 20; i++) {
      expect(v.feed(pcm(0)), isFalse);
    }
  });

  test('needs several loud frames in a row', () {
    final v = Vad();
    expect([v.feed(pcm(8000)), v.feed(pcm(8000)), v.feed(pcm(8000))], [false, false, true]);
  });

  test('a quiet frame resets the run, and triggering resets it too', () {
    final v = Vad();
    v.feed(pcm(8000));
    v.feed(pcm(8000));
    expect(v.feed(pcm(0)), isFalse);
    expect([v.feed(pcm(8000)), v.feed(pcm(8000)), v.feed(pcm(8000))], [false, false, true]);
    expect(v.feed(pcm(8000)), isFalse);
  });
}
```

`jarvis_app/test/fakes.dart`:

```dart
import 'dart:async';
import 'dart:typed_data';

import 'package:jarvis_app/protocol.dart';
import 'package:jarvis_app/session.dart';

class FakeSocket implements VoiceSocket {
  final ctrl = StreamController<ServerEvent>(sync: true);
  final audio = <Uint8List>[];
  final sent = <(String, Map<String, dynamic>)>[];
  bool closed = false;
  @override
  Stream<ServerEvent> get events => ctrl.stream;
  @override
  void sendAudio(Uint8List pcm) => audio.add(pcm);
  @override
  void sendJson(String type, [Map<String, dynamic> fields = const {}]) => sent.add((type, fields));
  @override
  Future<void> close() async {
    closed = true;
    await ctrl.close();
  }

  List<String> get types => sent.map((e) => e.$1).toList();
}

class FakeMic implements Mic {
  final ctrl = StreamController<Uint8List>(sync: true);
  bool stopped = false;
  @override
  Future<Stream<Uint8List>> start() async => ctrl.stream;
  @override
  Future<void> stop() async => stopped = true;
}

class FakePlayer implements Player {
  final played = <Uint8List>[];
  int flushes = 0;
  @override
  void play(Uint8List pcm) => played.add(pcm);
  @override
  Future<void> flush() async => flushes++;
}

class FakePhone implements PhoneActions {
  final ran = <Map<String, dynamic>>[];
  final errors = <String, String>{}; // action type -> error to return
  @override
  Future<String?> run(Map<String, dynamic> action) async {
    ran.add(action);
    return errors[action['type']];
  }
}

class FakeSpeaker implements Speaker {
  final said = <String>[];
  @override
  Future<void> say(String text) async => said.add(text);
}

Uint8List loud() {
  final b = ByteData(640);
  for (var i = 0; i < 320; i++) {
    b.setInt16(i * 2, i.isEven ? 9000 : -9000, Endian.little);
  }
  return b.buffer.asUint8List();
}

Uint8List quiet() => Uint8List(640);
```

`jarvis_app/test/session_test.dart`:

```dart
import 'dart:typed_data';

import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/protocol.dart';
import 'package:jarvis_app/session.dart';

import 'fakes.dart';

class Rig {
  final socket = FakeSocket(), mic = FakeMic(), player = FakePlayer(), phone = FakePhone(), speaker = FakeSpeaker();
  int ended = 0;
  bool failConnect = false;
  late final SessionController c = SessionController(
    connect: () async {
      if (failConnect) throw Exception('down');
      return socket;
    },
    mic: mic,
    player: player,
    phone: phone,
    speaker: speaker,
    fcmToken: () async => 'fcm-1',
    onEnded: () => ended++,
  );
}

const card = ConfirmCardEvent(interruptId: 'i1', summary: 'Create Gym', tapOnly: false, afterUntrusted: false);

void main() {
  test('start sends hello with the fcm token, streams mic audio, and listens', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      expect(r.c.phase, Phase.listening);
      expect(r.socket.sent.first.$1, 'hello');
      expect(r.socket.sent.first.$2, {'fcm_token': 'fcm-1'});
      r.mic.ctrl.add(quiet());
      expect(r.socket.audio.length, 1);
    });
  });

  test('speakText sends a speak request after hello', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start(speakText: 'Good morning.');
      a.flushMicrotasks();
      expect(r.socket.types, ['hello', 'speak']);
      expect(r.socket.sent[1].$2, {'text': 'Good morning.'});
    });
  });

  test('connect failure is offline: says so, ends, never listens', () {
    fakeAsync((a) {
      final r = Rig()..failConnect = true;
      r.c.start();
      a.flushMicrotasks();
      expect(r.c.phase, Phase.offline);
      expect(r.speaker.said, ['Jarvis is offline.']);
      expect(r.ended, 1);
      expect(r.mic.ctrl.hasListener, isFalse);
    });
  });

  test('starting while active is ignored', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.c.start();
      a.flushMicrotasks();
      expect(r.socket.types.where((t) => t == 'hello').length, 1);
    });
  });

  test('server states map to phases and audio is played', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(const StateEvent('thinking'));
      expect(r.c.phase, Phase.thinking);
      r.socket.ctrl.add(const StateEvent('speaking'));
      r.socket.ctrl.add(AudioEvent(Uint8List.fromList([1, 2])));
      expect(r.c.phase, Phase.speaking);
      expect(r.player.played.single, [1, 2]);
      r.socket.ctrl.add(const StateEvent('listening'));
      expect(r.c.phase, Phase.listening);
      expect(r.player.flushes, 0); // a normal end of speech must not cut the buffered tail
    });
  });

  test('user text while speaking flushes the player (server-side barge-in)', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(const StateEvent('speaking'));
      r.socket.ctrl.add(const TranscriptEvent('user', 'wait', false));
      expect(r.player.flushes, 1);
      expect(r.c.userText, 'wait');
    });
  });

  test('local speech while speaking sends cancel and flushes', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(const StateEvent('speaking'));
      for (var i = 0; i < 3; i++) {
        r.mic.ctrl.add(loud());
      }
      expect(r.socket.types.where((t) => t == 'cancel').length, 1);
      expect(r.player.flushes, 1);
      expect(r.c.phase, Phase.listening);
    });
  });

  test('card then tap sends the confirm frame with its id and clears the card', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(card);
      expect(r.c.card, isNotNull);
      r.c.confirm(true);
      expect(r.socket.sent.last.$1, 'confirm');
      expect(r.socket.sent.last.$2, {'decision': 'yes', 'interrupt_id': 'i1'});
      expect(r.c.card, isNull);
      r.c.confirm(false); // no card any more: nothing is sent
      expect(r.socket.types.where((t) => t == 'confirm').length, 1);
    });
  });

  test('silence for 8 s ends the session with bye', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      a.elapse(const Duration(seconds: 7));
      expect(r.c.phase, Phase.listening);
      a.elapse(const Duration(seconds: 2));
      a.flushMicrotasks();
      expect(r.socket.types, contains('bye'));
      expect(r.socket.closed && r.mic.stopped, isTrue);
      expect((r.c.phase, r.ended), (Phase.idle, 1));
      expect(r.speaker.said, isEmpty); // our own close is not "connection lost"
    });
  });

  test('activity restarts the silence timer', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      a.elapse(const Duration(seconds: 6));
      r.socket.ctrl.add(const TranscriptEvent('user', 'hm', false));
      a.elapse(const Duration(seconds: 6));
      expect(r.c.phase, Phase.listening);
    });
  });

  test('a pending card waits 30 s, and thinking or speaking never time out', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(card);
      a.elapse(const Duration(seconds: 29));
      expect(r.c.phase, Phase.listening);
      a.elapse(const Duration(seconds: 2));
      a.flushMicrotasks();
      expect(r.c.phase, Phase.idle);

      final s = Rig();
      s.c.start();
      a.flushMicrotasks();
      s.socket.ctrl.add(const StateEvent('thinking'));
      a.elapse(const Duration(seconds: 60));
      expect(s.c.phase, Phase.thinking);
    });
  });

  test('client actions run in order and a failure is shown, not sent', () {
    fakeAsync((a) {
      final r = Rig();
      r.phone.errors['compose_message'] = 'No contact named Anna.';
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(const ClientActionsEvent([
        {'type': 'set_alarm', 'hour': 6, 'minute': 0},
        {'type': 'compose_message', 'app': 'sms', 'contact': 'Anna', 'text': 'hi'},
      ]));
      a.flushMicrotasks();
      expect(r.phone.ran.map((m) => m['type']), ['set_alarm', 'compose_message']);
      expect(r.c.error, 'No contact named Anna.');
      expect(r.socket.types, isNot(contains('error')));
    });
  });

  test('server error frames are surfaced', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(const ErrorEvent('Speech output failed'));
      expect(r.c.error, 'Speech output failed');
    });
  });

  test('an unexpected server close says the connection was lost and ends', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.close();
      a.flushMicrotasks();
      expect(r.speaker.said, ['Jarvis connection lost.']);
      expect((r.c.phase, r.ended), (Phase.idle, 1));
    });
  });
}
```

- [ ] **Step 2: Run to verify failure** — `cd jarvis_app && flutter test` → FAIL (vad/session missing).

- [ ] **Step 3: Implement**

`jarvis_app/lib/vad.dart`:

```dart
import 'dart:typed_data';

/// Energy-based speech detector for barge-in: true once `frames` consecutive frames are loud.
/// ponytail: fixed RMS threshold; tune on the Pixel (echo cancellation decides how loud the speaker leaks), or swap in a real VAD.
class Vad {
  Vad({this.threshold = 1500.0, this.frames = 3});
  final double threshold;
  final int frames;
  int _run = 0;

  bool feed(Uint8List pcm) {
    final n = pcm.length ~/ 2;
    if (n == 0) return false;
    final bd = ByteData.sublistView(pcm);
    var sum = 0.0;
    for (var i = 0; i < n; i++) {
      final s = bd.getInt16(i * 2, Endian.little);
      sum += s * s;
    }
    final loud = (sum / n) > threshold * threshold;
    _run = loud ? _run + 1 : 0;
    if (_run >= frames) {
      _run = 0;
      return true;
    }
    return false;
  }
}
```

`jarvis_app/lib/session.dart`:

```dart
import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

import 'protocol.dart';
import 'vad.dart';

enum Phase { idle, connecting, listening, thinking, speaking, offline }

abstract class VoiceSocket {
  Stream<ServerEvent> get events;
  void sendAudio(Uint8List pcm);
  void sendJson(String type, [Map<String, dynamic> fields]);
  Future<void> close();
}

abstract class Mic {
  Future<Stream<Uint8List>> start();
  Future<void> stop();
}

abstract class Player {
  void play(Uint8List pcm);
  Future<void> flush();
}

abstract class PhoneActions {
  /// Runs one client action; returns an error message, or null on success.
  Future<String?> run(Map<String, dynamic> action);
}

abstract class Speaker {
  Future<void> say(String text);
}

class SessionController extends ChangeNotifier {
  SessionController({
    required this.connect,
    required this.mic,
    required this.player,
    required this.phone,
    required this.speaker,
    this.fcmToken,
    this.onEnded,
    Vad? vad,
    this.silence = const Duration(seconds: 8),
    this.confirmWait = const Duration(seconds: 30),
  }) : vad = vad ?? Vad();

  final Future<VoiceSocket> Function() connect;
  final Mic mic;
  final Player player;
  final PhoneActions phone;
  final Speaker speaker;
  final Future<String?> Function()? fcmToken;
  final VoidCallback? onEnded;
  final Vad vad;
  final Duration silence, confirmWait;

  Phase phase = Phase.idle;
  String userText = '', jarvisText = '';
  ConfirmCardEvent? card;
  String? error;

  VoiceSocket? _socket;
  StreamSubscription<ServerEvent>? _events;
  StreamSubscription<Uint8List>? _micSub;
  Timer? _timer;
  bool _ending = false;

  void _set(Phase p) {
    phase = p;
    notifyListeners();
  }

  Future<void> start({String? speakText}) async {
    if (phase != Phase.idle && phase != Phase.offline) return;
    _ending = false;
    error = null;
    card = null;
    userText = jarvisText = '';
    _set(Phase.connecting);
    try {
      _socket = await connect();
    } catch (_) {
      _set(Phase.offline);
      await speaker.say('Jarvis is offline.');
      onEnded?.call();
      return;
    }
    _events = _socket!.events.listen(_onEvent, onDone: _onClosed, onError: (_) => _onClosed());
    String? token;
    try {
      token = await fcmToken?.call();
    } catch (_) {} // push is optional; never block a session on it
    _socket!.sendJson('hello', {if (token != null) 'fcm_token': token});
    if (speakText != null) _socket!.sendJson('speak', {'text': speakText});
    _micSub = (await mic.start()).listen(_onMic);
    _set(Phase.listening);
    _arm();
  }

  void _onMic(Uint8List pcm) {
    _socket?.sendAudio(pcm);
    if (phase == Phase.speaking) {
      if (vad.feed(pcm)) _bargeIn();
    } else if (phase == Phase.listening && vad.feed(pcm)) {
      _arm(); // the user is talking: keep the session alive
    }
  }

  void _bargeIn() {
    player.flush();
    _socket?.sendJson('cancel');
    _set(Phase.listening);
    _arm();
  }

  void _onEvent(ServerEvent e) {
    switch (e) {
      case StateEvent(:final state):
        const map = {'listening': Phase.listening, 'thinking': Phase.thinking, 'speaking': Phase.speaking};
        if (map[state] != null) _set(map[state]!);
      case TranscriptEvent(:final role, :final text):
        if (role == 'user') {
          if (phase == Phase.speaking) player.flush(); // the user spoke over Jarvis: drop the buffered audio
          userText = text;
        } else {
          jarvisText = text;
        }
        notifyListeners();
      case ConfirmCardEvent c:
        card = c;
        notifyListeners();
      case ClientActionsEvent(:final actions):
        _runActions(actions);
      case ErrorEvent(:final message):
        error = message;
        notifyListeners();
      case AudioEvent(:final pcm):
        player.play(pcm);
    }
    _arm();
  }

  Future<void> _runActions(List<Map<String, dynamic>> actions) async {
    for (final a in actions) {
      final err = await phone.run(a);
      if (err != null) {
        error = err;
        notifyListeners();
      }
    }
  }

  void _arm() {
    _timer?.cancel();
    if (phase == Phase.listening) {
      _timer = Timer(card != null ? confirmWait : silence, _end);
    }
  }

  void confirm(bool yes) {
    final c = card;
    if (c == null) return;
    _socket?.sendJson('confirm', {'decision': yes ? 'yes' : 'no', 'interrupt_id': c.interruptId});
    card = null;
    notifyListeners();
    _arm();
  }

  void stop() => _end();

  Future<void> _end() async {
    if (_ending || phase == Phase.idle) return;
    _ending = true;
    _timer?.cancel();
    _socket?.sendJson('bye');
    await _teardown();
  }

  void _onClosed() {
    if (_ending || phase == Phase.idle) return;
    _ending = true;
    speaker.say('Jarvis connection lost.');
    _teardown(closeSocket: false);
  }

  Future<void> _teardown({bool closeSocket = true}) async {
    _timer?.cancel();
    await _micSub?.cancel();
    await mic.stop();
    await player.flush();
    await _events?.cancel();
    if (closeSocket) await _socket?.close();
    _socket = null;
    card = null;
    _set(Phase.idle);
    onEnded?.call();
  }
}
```

- [ ] **Step 4: Run to verify pass** — `cd jarvis_app && flutter test && flutter analyze` → PASS, no issues. (If `fake_async` timing of `_teardown`'s awaits needs extra `a.flushMicrotasks()` in a test, add it to the test, not the code.)

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(app): session controller with barge-in, silence timeout, offline handling"  # + trailers`

---

### Task 13: Phone actions as Android intents

**Files:**
- Create: `jarvis_app/lib/actions.dart`, `jarvis_app/lib/phone.dart`
- Test: `jarvis_app/test/actions_test.dart`

**Interfaces:**
- Consumes: `PhoneActions` port (Task 12).
- Produces: `IntentSpec({action, data, package, arguments})`; `IntentSpec? buildIntent(Map action, String? phone)` (pure; `phone` is the resolved number for `compose_message`); `String? pickContact(String name, List<(String, String)> contacts)` (pure, returns a phone number); `AndroidPhoneActions(contacts)` implementing `PhoneActions` through `android_intent_plus`.

- [ ] **Step 1: Write the failing test** — `jarvis_app/test/actions_test.dart`

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/actions.dart';

void main() {
  test('alarm', () {
    final i = buildIntent({'type': 'set_alarm', 'hour': 6, 'minute': 5, 'label': 'Gym'}, null)!;
    expect(i.action, 'android.intent.action.SET_ALARM');
    expect(i.arguments, {
      'android.intent.extra.alarm.HOUR': 6,
      'android.intent.extra.alarm.MINUTES': 5,
      'android.intent.extra.alarm.SKIP_UI': true,
      'android.intent.extra.alarm.MESSAGE': 'Gym',
    });
  });

  test('alarm without label has no message extra', () {
    final i = buildIntent({'type': 'set_alarm', 'hour': 6, 'minute': 0}, null)!;
    expect(i.arguments!.containsKey('android.intent.extra.alarm.MESSAGE'), isFalse);
  });

  test('timer', () {
    final i = buildIntent({'type': 'set_timer', 'seconds': 300}, null)!;
    expect(i.action, 'android.intent.action.SET_TIMER');
    expect(i.arguments, {'android.intent.extra.alarm.LENGTH': 300, 'android.intent.extra.alarm.SKIP_UI': true});
  });

  test('navigation uses the maps uri and package', () {
    final i = buildIntent({'type': 'start_navigation', 'destination': 'Marienplatz München'}, null)!;
    expect(i.data, 'google.navigation:q=Marienplatz%20M%C3%BCnchen');
    expect((i.action, i.package), ('android.intent.action.VIEW', 'com.google.android.apps.maps'));
  });

  test('whatsapp compose is a prefilled wa.me link with digits only', () {
    final i = buildIntent({'type': 'compose_message', 'app': 'whatsapp', 'contact': 'Anna', 'text': '10 min late'},
        '+49 151 234-567')!;
    expect(i.data, 'https://wa.me/49151234567?text=10%20min%20late');
    expect(i.package, 'com.whatsapp');
  });

  test('sms compose is SENDTO with sms_body', () {
    final i = buildIntent({'type': 'compose_message', 'app': 'sms', 'contact': 'Anna', 'text': 'hi'}, '+49151234567')!;
    expect((i.action, i.data), ('android.intent.action.SENDTO', 'smsto:+49151234567'));
    expect(i.arguments, {'sms_body': 'hi'});
  });

  test('unknown or malformed actions build nothing', () {
    expect(buildIntent({'type': 'launch_missiles'}, null), isNull);
    expect(buildIntent({'type': 'set_alarm'}, null), isNull);
    expect(buildIntent({'type': 'compose_message', 'app': 'sms', 'contact': 'A', 'text': 'x'}, null), isNull);
  });

  group('pickContact', () {
    const people = [('Anna Schmidt', '111'), ('Annabel', '222'), ('Raj', '333'), ('Joanna Lee', '444')];
    test('exact beats prefix beats substring, case-insensitive', () {
      expect(pickContact('raj', people), '333');
      expect(pickContact('anna', people), '111'); // prefix of "Anna Schmidt" (first prefix match)
      expect(pickContact('lee', people), '444');
    });
    test('no match is null', () => expect(pickContact('zed', people), isNull));
  });
}
```

- [ ] **Step 2: Run to verify failure** — `cd jarvis_app && flutter test test/actions_test.dart` → FAIL.

- [ ] **Step 3: Implement**

`jarvis_app/lib/actions.dart`:

```dart
class IntentSpec {
  const IntentSpec({required this.action, this.data, this.package, this.arguments});
  final String action;
  final String? data, package;
  final Map<String, dynamic>? arguments;
}

IntentSpec? buildIntent(Map<String, dynamic> a, String? phone) {
  try {
    switch (a['type']) {
      case 'set_alarm':
        return IntentSpec(action: 'android.intent.action.SET_ALARM', arguments: {
          'android.intent.extra.alarm.HOUR': a['hour'] as int,
          'android.intent.extra.alarm.MINUTES': a['minute'] as int,
          'android.intent.extra.alarm.SKIP_UI': true,
          if (a['label'] != null) 'android.intent.extra.alarm.MESSAGE': a['label'] as String,
        });
      case 'set_timer':
        return IntentSpec(action: 'android.intent.action.SET_TIMER', arguments: {
          'android.intent.extra.alarm.LENGTH': a['seconds'] as int,
          'android.intent.extra.alarm.SKIP_UI': true,
          if (a['label'] != null) 'android.intent.extra.alarm.MESSAGE': a['label'] as String,
        });
      case 'start_navigation':
        return IntentSpec(
            action: 'android.intent.action.VIEW',
            data: 'google.navigation:q=${Uri.encodeComponent(a['destination'] as String)}',
            package: 'com.google.android.apps.maps');
      case 'compose_message':
        if (phone == null) return null;
        final text = a['text'] as String;
        if (a['app'] == 'whatsapp') {
          final digits = phone.replaceAll(RegExp(r'\D'), '');
          return IntentSpec(
              action: 'android.intent.action.VIEW',
              data: 'https://wa.me/$digits?text=${Uri.encodeComponent(text)}',
              package: 'com.whatsapp');
        }
        return IntentSpec(
            action: 'android.intent.action.SENDTO', data: 'smsto:$phone', arguments: {'sms_body': text});
      default:
        return null;
    }
  } catch (_) {
    return null; // missing or wrongly typed fields
  }
}

/// Best contact for a spoken name: exact, then prefix, then substring (case-insensitive). Returns the phone number.
String? pickContact(String name, List<(String, String)> contacts) {
  final n = name.trim().toLowerCase();
  if (n.isEmpty) return null;
  for (final test in <bool Function(String)>[(s) => s == n, (s) => s.startsWith(n), (s) => s.contains(n)]) {
    for (final (display, phone) in contacts) {
      if (test(display.toLowerCase())) return phone;
    }
  }
  return null;
}
```

`jarvis_app/lib/phone.dart`:

```dart
import 'package:android_intent_plus/android_intent_plus.dart';
import 'package:flutter_contacts/flutter_contacts.dart';

import 'actions.dart';
import 'session.dart';

class AndroidPhoneActions implements PhoneActions {
  /// Contact names are resolved here, on the phone, so the server never sees the address book.
  Future<String?> _phoneFor(String name) async {
    if (!await FlutterContacts.requestPermission(readonly: true)) return null;
    final all = await FlutterContacts.getContacts(withProperties: true);
    return pickContact(name, [
      for (final c in all)
        if (c.phones.isNotEmpty) (c.displayName, c.phones.first.number)
    ]);
  }

  @override
  Future<String?> run(Map<String, dynamic> action) async {
    String? phone;
    if (action['type'] == 'compose_message') {
      phone = await _phoneFor('${action['contact']}');
      if (phone == null) return 'No contact named ${action['contact']}.';
    }
    final spec = buildIntent(action, phone);
    if (spec == null) return 'Unsupported phone action: ${action['type']}.';
    try {
      await AndroidIntent(
        action: spec.action,
        data: spec.data,
        package: spec.package,
        arguments: spec.arguments,
        flags: const [0x10000000], // FLAG_ACTIVITY_NEW_TASK
      ).launch();
      return null;
    } catch (e) {
      return 'Could not run ${action['type']}: $e';
    }
  }
}
```

- [ ] **Step 4: Run to verify pass** — `cd jarvis_app && flutter test && flutter analyze` → PASS, no issues.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(app): phone actions as android intents with on-device contact lookup"  # + trailers`

---

### Task 14: Real socket, mic, player, TTS fallback, pairing storage, UI and app entry

**Files:**
- Create: `jarvis_app/lib/ws_socket.dart`, `jarvis_app/lib/audio.dart`, `jarvis_app/lib/store.dart`, `jarvis_app/lib/ui.dart`, `jarvis_app/lib/app.dart`
- Modify: `jarvis_app/lib/main.dart`, `jarvis_app/android/app/src/main/AndroidManifest.xml`
- Test: `jarvis_app/test/ui_test.dart`

**Interfaces:**
- Consumes: `SessionController`, `Config`, ports from Task 12, `AndroidPhoneActions`.
- Produces: `WsVoiceSocket.open(Config)`; `RecordMic` (`Mic`), `PcmPlayer` (`Player`), `TtsSpeaker` (`Speaker`); `ConfigStore` (`load()`, `save(Config)`); `SessionScreen(controller)`, `PairingScreen(onSaved)`; `JarvisApp` wiring everything and exposing a `startSession({String? speakText})` used by Tasks 15-16 via `AppHost`.

- [ ] **Step 1: Write the failing test** — `jarvis_app/test/ui_test.dart`

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/protocol.dart';
import 'package:jarvis_app/ui.dart';

import 'fakes.dart';
import 'session_rig.dart';

void main() {
  testWidgets('shows phase, transcripts and the confirm card with working buttons', (t) async {
    final r = Rig();
    await t.pumpWidget(MaterialApp(home: SessionScreen(controller: r.c)));
    await r.c.start();
    r.socket.ctrl.add(const TranscriptEvent('user', 'put gym at seven', true));
    r.socket.ctrl.add(const ConfirmCardEvent(
        interruptId: 'i1', summary: 'Create Gym', tapOnly: false, afterUntrusted: true));
    await t.pump();
    expect(find.text('put gym at seven'), findsOneWidget);
    expect(find.textContaining('Create Gym'), findsOneWidget);
    expect(find.textContaining('after reading email'), findsOneWidget);
    await t.tap(find.text('Confirm'));
    await t.pump();
    expect(r.socket.sent.last.$1, 'confirm');
    expect(r.socket.sent.last.$2, {'decision': 'yes', 'interrupt_id': 'i1'});
    expect(find.text('Confirm'), findsNothing);
    r.c.stop(); // cancel the silence timer so the test ends cleanly
    await t.pump();
  });
}
```

For reuse, move `Rig` from `session_test.dart` into `jarvis_app/test/session_rig.dart` (same code, `import`ing `fakes.dart`, `session.dart`) and have `session_test.dart` import it.

- [ ] **Step 2: Run to verify failure** — `cd jarvis_app && flutter test` → FAIL (`ui.dart` missing).

- [ ] **Step 3: Implement**

`jarvis_app/lib/ws_socket.dart`:

```dart
import 'dart:typed_data';

import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'config.dart';
import 'protocol.dart';
import 'session.dart';

class WsVoiceSocket implements VoiceSocket {
  WsVoiceSocket._(this._ch);
  final WebSocketChannel _ch;

  static Future<WsVoiceSocket> open(Config c) async {
    final ch = IOWebSocketChannel.connect(c.voiceUri,
        headers: {'Authorization': 'Bearer ${c.token}'}, connectTimeout: const Duration(seconds: 5));
    await ch.ready; // throws when the backend is down or the token is refused
    return WsVoiceSocket._(ch);
  }

  @override
  Stream<ServerEvent> get events =>
      _ch.stream.map(decodeServer).where((e) => e != null).cast<ServerEvent>();
  @override
  void sendAudio(Uint8List pcm) => _ch.sink.add(pcm);
  @override
  void sendJson(String type, [Map<String, dynamic> fields = const {}]) => _ch.sink.add(encode(type, fields));
  @override
  Future<void> close() => _ch.sink.close();
}
```

`jarvis_app/lib/audio.dart` (thin wrappers over hardware plugins; adapt calls to the installed versions, keep the class names and the port methods):

```dart
import 'dart:typed_data';

import 'package:flutter_sound/flutter_sound.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:record/record.dart';

import 'session.dart';

class RecordMic implements Mic {
  final _rec = AudioRecorder();

  @override
  Future<Stream<Uint8List>> start() => _rec.startStream(const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: 16000,
        numChannels: 1,
        echoCancel: true, // barge-in depends on the mic not hearing Jarvis's own speaker
        noiseSuppress: true,
      ));

  @override
  Future<void> stop() async {
    if (await _rec.isRecording()) await _rec.stop();
  }
}

class PcmPlayer implements Player {
  final _p = FlutterSoundPlayer();
  bool _open = false;

  Future<void> _ensure() async {
    if (_open) return;
    await _p.openPlayer();
    await _p.startPlayerFromStream(codec: Codec.pcm16, interleaved: true, numChannels: 1, sampleRate: 16000);
    _open = true;
  }

  @override
  void play(Uint8List pcm) {
    _ensure().then((_) => _p.uint8ListSink?.add(pcm));
  }

  @override
  Future<void> flush() async {
    if (!_open) return;
    _open = false; // stopping drops the buffered audio; the next play() reopens the stream
    await _p.stopPlayer();
  }
}

class TtsSpeaker implements Speaker {
  final _tts = FlutterTts();
  @override
  Future<void> say(String text) => _tts.speak(text); // Android's own TTS: works with the backend down
}
```

`jarvis_app/lib/store.dart`:

```dart
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'config.dart';

class ConfigStore {
  static const _s = FlutterSecureStorage();
  Future<Config?> load() async {
    final u = await _s.read(key: 'url'), t = await _s.read(key: 'token');
    return (u == null || t == null) ? null : Config(u, t);
  }

  Future<void> save(Config c) async {
    await _s.write(key: 'url', value: c.url);
    await _s.write(key: 'token', value: c.token);
  }
}
```

`jarvis_app/lib/ui.dart`:

```dart
import 'package:flutter/material.dart';

import 'config.dart';
import 'session.dart';

class SessionScreen extends StatelessWidget {
  const SessionScreen({super.key, required this.controller});
  final SessionController controller;

  static const _labels = {
    Phase.idle: 'Say "Hey Jarvis"',
    Phase.connecting: 'Connecting…',
    Phase.listening: 'Listening…',
    Phase.thinking: 'Thinking…',
    Phase.speaking: 'Speaking…',
    Phase.offline: 'Jarvis is offline',
  };

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final c = controller.card;
          return Scaffold(
            body: SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(_labels[controller.phase]!, style: Theme.of(context).textTheme.headlineMedium),
                  const SizedBox(height: 24),
                  if (controller.userText.isNotEmpty) Text(controller.userText),
                  if (controller.jarvisText.isNotEmpty) Text(controller.jarvisText, style: const TextStyle(fontWeight: FontWeight.bold)),
                  if (controller.error != null) Text(controller.error!, style: const TextStyle(color: Colors.red)),
                  const Spacer(),
                  if (c != null) ...[
                    if (c.afterUntrusted) const Text('⚠ Proposed after reading email content — check recipient and text.'),
                    Text(c.summary),
                    const SizedBox(height: 12),
                    Row(children: [
                      FilledButton(onPressed: () => controller.confirm(true), child: const Text('Confirm')),
                      const SizedBox(width: 12),
                      OutlinedButton(onPressed: () => controller.confirm(false), child: const Text('Cancel')),
                    ]),
                  ],
                  const SizedBox(height: 12),
                  Row(children: [
                    FilledButton.tonal(
                        onPressed: controller.phase == Phase.idle || controller.phase == Phase.offline
                            ? () => controller.start()
                            : controller.stop,
                        child: Text(controller.phase == Phase.idle || controller.phase == Phase.offline ? 'Talk' : 'Stop')),
                  ]),
                ]),
              ),
            ),
          );
        },
      );
}

class PairingScreen extends StatefulWidget {
  const PairingScreen({super.key, required this.onSaved});
  final void Function(Config) onSaved;
  @override
  State<PairingScreen> createState() => _PairingScreenState();
}

class _PairingScreenState extends State<PairingScreen> {
  final _url = TextEditingController(), _token = TextEditingController();

  @override
  Widget build(BuildContext context) => Scaffold(
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(children: [
              TextField(controller: _url, decoration: const InputDecoration(labelText: 'Backend URL (https://…)')),
              TextField(controller: _token, decoration: const InputDecoration(labelText: 'Device token'), obscureText: true),
              const SizedBox(height: 16),
              FilledButton(
                  onPressed: () => widget.onSaved(Config(_url.text.trim(), _token.text.trim())),
                  child: const Text('Pair')),
            ]),
          ),
        ),
      );
}
```

`jarvis_app/lib/app.dart`:

```dart
import 'package:flutter/material.dart';

import 'audio.dart';
import 'config.dart';
import 'phone.dart';
import 'session.dart';
import 'store.dart';
import 'ui.dart';
import 'ws_socket.dart';

/// Lets the wake-word, assistant and push entry points start a session on the one controller.
class AppHost {
  static SessionController? controller;
  static Future<void> startSession({String? speakText}) async => controller?.start(speakText: speakText);
}

class JarvisApp extends StatefulWidget {
  const JarvisApp({super.key, this.onSessionEnded, this.fcmToken});
  final VoidCallback? onSessionEnded;
  final Future<String?> Function()? fcmToken;
  @override
  State<JarvisApp> createState() => _JarvisAppState();
}

class _JarvisAppState extends State<JarvisApp> {
  final _store = ConfigStore();
  Config? _config;
  bool _loaded = false;
  SessionController? _controller;

  @override
  void initState() {
    super.initState();
    _store.load().then((c) => setState(() {
          _loaded = true;
          _bind(c);
        }));
  }

  void _bind(Config? c) {
    _config = c;
    _controller?.dispose();
    _controller = c == null
        ? null
        : SessionController(
            connect: () => WsVoiceSocket.open(c),
            mic: RecordMic(),
            player: PcmPlayer(),
            phone: AndroidPhoneActions(),
            speaker: TtsSpeaker(),
            fcmToken: widget.fcmToken,
            onEnded: widget.onSessionEnded,
          );
    AppHost.controller = _controller;
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Jarvis',
        theme: ThemeData(colorSchemeSeed: Colors.indigo, useMaterial3: true),
        home: !_loaded
            ? const Scaffold(body: Center(child: CircularProgressIndicator()))
            : _config == null
                ? PairingScreen(onSaved: (c) async {
                    await _store.save(c);
                    setState(() => _bind(c));
                  })
                : SessionScreen(controller: _controller!),
      );
}
```

`jarvis_app/lib/main.dart`:

```dart
import 'package:flutter/material.dart';

import 'app.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const JarvisApp());
}
```

`AndroidManifest.xml` — add inside `<manifest>` (before `<application>`):

```xml
    <uses-permission android:name="android.permission.INTERNET"/>
    <uses-permission android:name="android.permission.RECORD_AUDIO"/>
    <uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS"/>
    <uses-permission android:name="android.permission.READ_CONTACTS"/>
    <uses-permission android:name="com.android.alarm.permission.SET_ALARM"/>
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS"/>
    <uses-permission android:name="android.permission.WAKE_LOCK"/>
    <uses-permission android:name="android.permission.USE_FULL_SCREEN_INTENT"/>
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE"/>
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_MICROPHONE"/>
    <uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED"/>
    <queries>
        <intent><action android:name="android.intent.action.VIEW"/><data android:scheme="https"/></intent>
        <intent><action android:name="android.intent.action.SENDTO"/><data android:scheme="smsto"/></intent>
        <package android:name="com.google.android.apps.maps"/>
        <package android:name="com.whatsapp"/>
    </queries>
```

and on the `MainActivity` element add `android:showWhenLocked="true" android:turnScreenOn="true"` (screen wake on "Hey Jarvis", FR-15).

- [ ] **Step 4: Run to verify pass** — `cd jarvis_app && flutter test && flutter analyze` → PASS. Then `flutter build apk --debug` → builds (confirms the plugin wrappers compile; if a wrapper call does not match the installed package version, fix it in `audio.dart` and re-run).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(app): websocket transport, audio io, pairing and session UI"  # + trailers`

---

### Task 15: Wake word foreground service, screen wake, boot reminder

> **Superseded:** the Porcupine code, `PICOVOICE_ACCESS_KEY` and `.ppn` asset below were replaced by sherpa-onnx keyword spotting (`assets/kws/`, no key). See `jarvis_app/lib/wake.dart` and the voice design spec. The steps are kept as history.

**Files:**
- Create: `jarvis_app/lib/wake.dart`, `jarvis_app/android/app/src/main/kotlin/com/jarvis/jarvis_app/BootReceiver.kt`, `jarvis_app/assets/README.md`
- Modify: `jarvis_app/lib/main.dart`, `jarvis_app/lib/app.dart`, `jarvis_app/pubspec.yaml` (assets), `jarvis_app/android/app/src/main/AndroidManifest.xml`
- Test: `jarvis_app/test/wake_test.dart` (pure helper only)

**Interfaces:**
- Consumes: `AppHost.startSession`, `SessionController.onEnded` (resume the wake word when a session ends).
- Produces: `wakeIsFresh(int? savedMillis, DateTime now) -> bool` (pure; a saved wake counts for 15 s); `WakeService.init()`, `.ensureRunning()`, `.resume()`; `startCallback` entry point; `WakeBridge` hooking wake events to `AppHost.startSession`.

- [ ] **Step 1: Write the failing test** — `jarvis_app/test/wake_test.dart`

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/wake.dart';

void main() {
  final now = DateTime(2026, 10, 2, 7, 30, 0);
  test('a wake saved a few seconds ago is fresh', () {
    expect(wakeIsFresh(now.subtract(const Duration(seconds: 5)).millisecondsSinceEpoch, now), isTrue);
  });
  test('an old or missing wake is not', () {
    expect(wakeIsFresh(now.subtract(const Duration(seconds: 16)).millisecondsSinceEpoch, now), isFalse);
    expect(wakeIsFresh(null, now), isFalse);
  });
  test('a timestamp from the future is not fresh', () {
    expect(wakeIsFresh(now.add(const Duration(seconds: 5)).millisecondsSinceEpoch, now), isFalse);
  });
}
```

- [ ] **Step 2: Run to verify failure** — `cd jarvis_app && flutter test test/wake_test.dart` → FAIL.

- [ ] **Step 3: Implement**

`jarvis_app/lib/wake.dart` (match `flutter_foreground_task`'s `TaskHandler` signatures to the installed version; keep the names `startCallback`, `WakeService`, `wakeIsFresh`):

```dart
import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:porcupine_flutter/porcupine_manager.dart';

import 'app.dart';

const picovoiceKey = String.fromEnvironment('PICOVOICE_ACCESS_KEY');
const keywordAsset = 'assets/hey_jarvis_android.ppn'; // trained in the Picovoice console, see ACCEPTANCE.md
const _wakeKey = 'wake';

bool wakeIsFresh(int? savedMillis, DateTime now) {
  if (savedMillis == null) return false;
  final age = now.millisecondsSinceEpoch - savedMillis;
  return age >= 0 && age <= 15000;
}

@pragma('vm:entry-point')
void startCallback() => FlutterForegroundTask.setTaskHandler(WakeTaskHandler());

/// Runs Porcupine in the foreground service so the wake word works with the screen off (FR-14).
class WakeTaskHandler extends TaskHandler {
  PorcupineManager? _pm;

  Future<void> _listen() async {
    _pm ??= await PorcupineManager.fromKeywordPaths(picovoiceKey, [keywordAsset], _onWake, sensitivities: [0.6]);
    await _pm!.start();
  }

  Future<void> _onWake(int index) async {
    await _pm?.stop(); // the session takes the microphone
    await FlutterForegroundTask.saveData(key: _wakeKey, value: DateTime.now().millisecondsSinceEpoch);
    FlutterForegroundTask.sendDataToMain('wake');
    final n = FlutterLocalNotificationsPlugin();
    await n.initialize(const InitializationSettings(android: AndroidInitializationSettings('@mipmap/ic_launcher')));
    // Full-screen intent wakes the screen; if Android denies it, this degrades to a heads-up notification.
    await n.show(
      1,
      'Jarvis',
      'Listening…',
      const NotificationDetails(
          android: AndroidNotificationDetails('wake', 'Wake word',
              importance: Importance.max,
              priority: Priority.high,
              fullScreenIntent: true,
              category: AndroidNotificationCategory.call)),
    );
  }

  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) => _listen();
  @override
  void onRepeatEvent(DateTime timestamp) {}
  @override
  Future<void> onDestroy(DateTime timestamp) async => _pm?.delete();
  @override
  void onReceiveData(Object data) {
    if (data == 'resume') _listen();
  }
}

class WakeService {
  static void init() {
    FlutterForegroundTask.initCommunicationPort();
    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'jarvis_wake_service',
        channelName: 'Jarvis wake word',
        channelDescription: 'Listening for "Hey Jarvis"',
      ),
      iosNotificationOptions: const IOSNotificationOptions(),
      foregroundTaskOptions: ForegroundTaskOptions(eventAction: ForegroundTaskEventAction.nothing()),
    );
  }

  static Future<void> ensureRunning() async {
    if (await FlutterForegroundTask.isRunningService) return;
    await FlutterForegroundTask.startService(
      serviceId: 100,
      notificationTitle: 'Jarvis',
      notificationText: 'Listening for "Hey Jarvis"',
      serviceTypes: [ForegroundServiceTypes.microphone],
      callback: startCallback,
    );
  }

  static void resume() => FlutterForegroundTask.sendDataToTask('resume');

  /// Wake events arrive live when this isolate is up, or as a saved timestamp when the activity was started by the notification.
  static Future<void> bridgeWakeToSession() async {
    FlutterForegroundTask.addTaskDataCallback((d) {
      if (d == 'wake') AppHost.startSession();
    });
    final saved = await FlutterForegroundTask.getData<int>(key: _wakeKey);
    if (wakeIsFresh(saved, DateTime.now())) {
      await FlutterForegroundTask.removeData(key: _wakeKey);
      AppHost.startSession();
    }
  }
}
```

`jarvis_app/lib/main.dart` (replace):

```dart
import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';

import 'app.dart';
import 'wake.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  WakeService.init();
  await [Permission.microphone, Permission.notification, Permission.contacts].request();
  runApp(JarvisApp(onSessionEnded: WakeService.resume));
  await WakeService.ensureRunning(); // also covers "service not running" after a reboot once the user opens the app
  await WakeService.bridgeWakeToSession();
}
```

(`WakeService.resume` is a static tear-off passed as `onSessionEnded`.)

`jarvis_app/pubspec.yaml` — under `flutter:` add:

```yaml
  assets:
    - assets/hey_jarvis_android.ppn
```
and create `jarvis_app/assets/README.md` containing: "Put the Porcupine keyword file trained for 'Hey Jarvis' (Android platform) here as `hey_jarvis_android.ppn`. Not committed: see `.gitignore`." Add `jarvis_app/assets/*.ppn` to the root `.gitignore`. For local compile checks create an empty placeholder `jarvis_app/assets/hey_jarvis_android.ppn` (also gitignored) so the asset path resolves.

`jarvis_app/android/app/src/main/kotlin/com/jarvis/jarvis_app/BootReceiver.kt`:

```kotlin
package com.jarvis.jarvis_app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat

/** Android 14 blocks starting a microphone foreground service from boot, so ask the user to open the app. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(NotificationChannel("boot", "Jarvis reminders", NotificationManager.IMPORTANCE_DEFAULT))
        val open = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE)
        nm.notify(2, NotificationCompat.Builder(context, "boot")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Jarvis is not listening")
            .setContentText("Tap to resume \"Hey Jarvis\" after the restart.")
            .setContentIntent(open).setAutoCancel(true).build())
    }
}
```

`AndroidManifest.xml` — inside `<application>` add:

```xml
        <receiver android:name=".BootReceiver" android:exported="true">
            <intent-filter><action android:name="android.intent.action.BOOT_COMPLETED"/></intent-filter>
        </receiver>
        <service android:name="com.pravera.flutter_foreground_task.service.ForegroundService"
            android:foregroundServiceType="microphone" android:exported="false"/>
```

- [ ] **Step 4: Run to verify pass** — `cd jarvis_app && flutter test && flutter analyze && flutter build apk --debug` → PASS / builds. (On-device behaviour is checked by acceptance rows, not here.)

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(app): wake word foreground service, screen wake notification, boot reminder"  # + trailers`

---

### Task 16: Default assistant role and FCM tap-to-play

**Files:**
- Create: `jarvis_app/lib/push.dart`, `jarvis_app/lib/launch.dart`, Kotlin `MainActivity.kt` (replace), `JarvisInteractionService.kt`, `JarvisSessionService.kt`, `JarvisRecognitionService.kt`, `jarvis_app/android/app/src/main/res/xml/interaction_service.xml`
- Modify: `jarvis_app/lib/main.dart`, `jarvis_app/android/app/src/main/AndroidManifest.xml`, Gradle files for Firebase, root `.gitignore`
- Test: `jarvis_app/test/push_test.dart` (pure helper)

**Interfaces:**
- Consumes: `AppHost.startSession({speakText})`.
- Produces: `String? speakTextOf(Map<String, dynamic> data)` (the `speak` string from an FCM data payload, null if absent/blank); `PushBridge.init()` (token + opened-app handlers); `LaunchBridge.init()` (assistant long-press launches a session through the `jarvis/launch` method channel).

- [ ] **Step 1: Write the failing test** — `jarvis_app/test/push_test.dart`

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/push.dart';

void main() {
  test('takes the speak text from the data payload', () {
    expect(speakTextOf({'speak': 'Good morning. Brief here.'}), 'Good morning. Brief here.');
  });
  test('missing, blank or non-string text is ignored', () {
    expect(speakTextOf({}), isNull);
    expect(speakTextOf({'speak': '   '}), isNull);
    expect(speakTextOf({'speak': 5}), isNull);
  });
}
```

- [ ] **Step 2: Run to verify failure** — `cd jarvis_app && flutter test test/push_test.dart` → FAIL.

- [ ] **Step 3: Implement**

`jarvis_app/lib/push.dart`:

```dart
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';

import 'app.dart';

String? speakTextOf(Map<String, dynamic> data) {
  final s = data['speak'];
  return s is String && s.trim().isNotEmpty ? s : null;
}

class PushBridge {
  static bool ready = false;

  /// Push is optional: without google-services.json the app still works; only tap-to-play is unavailable.
  static Future<void> init() async {
    try {
      await Firebase.initializeApp();
      ready = true;
    } catch (_) {
      return;
    }
    Future<void> play(RemoteMessage m) async {
      final text = speakTextOf(m.data);
      if (text != null) await AppHost.startSession(speakText: text);
    }

    final initial = await FirebaseMessaging.instance.getInitialMessage(); // app was closed, notification tapped
    if (initial != null) await play(initial);
    FirebaseMessaging.onMessageOpenedApp.listen(play); // app was in the background
  }

  static Future<String?> token() async => ready ? FirebaseMessaging.instance.getToken() : null;
}
```

`jarvis_app/lib/launch.dart`:

```dart
import 'package:flutter/services.dart';

import 'app.dart';

/// The assistant role (power-button long-press) starts MainActivity with an extra; the native side forwards it here.
class LaunchBridge {
  static const _ch = MethodChannel('jarvis/launch');

  static Future<void> init() async {
    _ch.setMethodCallHandler((call) async {
      if (call.method == 'start') await AppHost.startSession();
    });
    if (await _ch.invokeMethod<bool>('launchedByAssistant') ?? false) await AppHost.startSession();
  }
}
```

`jarvis_app/lib/main.dart` — call the bridges and pass the FCM token:

```dart
import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';

import 'app.dart';
import 'launch.dart';
import 'push.dart';
import 'wake.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  WakeService.init();
  await [Permission.microphone, Permission.notification, Permission.contacts].request();
  await PushBridge.init();
  runApp(JarvisApp(onSessionEnded: WakeService.resume, fcmToken: PushBridge.token));
  await WakeService.ensureRunning();
  await WakeService.bridgeWakeToSession();
  await LaunchBridge.init();
}
```

Kotlin (all in `jarvis_app/android/app/src/main/kotlin/com/jarvis/jarvis_app/`):

`MainActivity.kt` (replace):

```kotlin
package com.jarvis.jarvis_app

import android.content.Intent
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var channel: MethodChannel? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/launch").also { ch ->
            ch.setMethodCallHandler { call, result ->
                if (call.method == "launchedByAssistant") {
                    val started = intent?.getBooleanExtra(EXTRA_START, false) == true
                    intent?.removeExtra(EXTRA_START)
                    result.success(started)
                } else result.notImplemented()
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent.getBooleanExtra(EXTRA_START, false)) {
            intent.removeExtra(EXTRA_START)
            channel?.invokeMethod("start", null)
        }
    }

    companion object { const val EXTRA_START = "jarvis_start" }
}
```

`JarvisInteractionService.kt`:

```kotlin
package com.jarvis.jarvis_app

import android.service.voice.VoiceInteractionService

class JarvisInteractionService : VoiceInteractionService()
```

`JarvisSessionService.kt`:

```kotlin
package com.jarvis.jarvis_app

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService

class JarvisSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = JarvisSession(this)
}

/** Power-button long-press: open the app straight into a voice session (FR-17). */
class JarvisSession(context: Context) : VoiceInteractionSession(context) {
    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        context.startActivity(Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            .putExtra(MainActivity.EXTRA_START, true))
        hide()
    }
}
```

`JarvisRecognitionService.kt` (the role requires one; the app does its own recognition server-side, so this is a stub that reports an error):

```kotlin
package com.jarvis.jarvis_app

import android.content.Intent
import android.speech.RecognitionService
import android.speech.SpeechRecognizer

class JarvisRecognitionService : RecognitionService() {
    override fun onStartListening(intent: Intent?, callback: Callback?) { callback?.error(SpeechRecognizer.ERROR_CLIENT) }
    override fun onCancel(callback: Callback?) {}
    override fun onStopListening(callback: Callback?) {}
}
```

`res/xml/interaction_service.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<voice-interaction-service xmlns:android="http://schemas.android.com/apk/res/android"
    android:sessionService="com.jarvis.jarvis_app.JarvisSessionService"
    android:recognitionService="com.jarvis.jarvis_app.JarvisRecognitionService"
    android:supportsAssist="true"/>
```

`AndroidManifest.xml` — inside `<application>` add:

```xml
        <service android:name=".JarvisInteractionService" android:exported="true"
            android:permission="android.permission.BIND_VOICE_INTERACTION">
            <meta-data android:name="android.voice_interaction" android:resource="@xml/interaction_service"/>
            <intent-filter><action android:name="android.service.voice.VoiceInteractionService"/></intent-filter>
        </service>
        <service android:name=".JarvisSessionService" android:exported="true"
            android:permission="android.permission.BIND_VOICE_INTERACTION"/>
        <service android:name=".JarvisRecognitionService" android:exported="true"
            android:permission="android.permission.RECORD_AUDIO">
            <intent-filter><action android:name="android.speech.RecognitionService"/></intent-filter>
        </service>
```

and on `MainActivity` add `<intent-filter><action android:name="android.intent.action.ASSIST"/><category android:name="android.intent.category.DEFAULT"/></intent-filter>` and `android:launchMode="singleTop"` if not already set.

Firebase Gradle: apply the `com.google.gms.google-services` plugin following the FlutterFire instructions for the installed Flutter version (project-level plugin declaration plus `id("com.google.gms.google-services")` in `android/app/build.gradle(.kts)`). `google-services.json` is the user's own file and is gitignored. For the local build check only, create a throwaway `jarvis_app/android/app/google-services.json` with this content (never commit it):

```json
{"project_info":{"project_number":"1","project_id":"jarvis-local-check","storage_bucket":"x.appspot.com"},
 "client":[{"client_info":{"mobilesdk_app_id":"1:1:android:1","android_client_info":{"package_name":"com.jarvis.jarvis_app"}},
 "oauth_client":[],"api_key":[{"current_key":"x"}],"services":{"appinvite_service":{"other_platform_oauth_client":[]}}}],
 "configuration_version":"1"}
```

- [ ] **Step 4: Run to verify pass** — `cd jarvis_app && flutter test && flutter analyze && flutter build apk --debug` → PASS / builds.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(app): default assistant role and FCM tap-to-play"  # + trailers`

---

### Task 17: Documentation and acceptance rows

**Files:**
- Modify: `ACCEPTANCE.md`
- Create: `jarvis_app/README.md`

- [ ] **Step 1: Append to `ACCEPTANCE.md`**

```markdown
# Manual acceptance (sub-project 3: Pixel voice app)

Needs: a Pixel on Android 14+, the laptop backend, and these accounts. Steps 1 to 6 are one-off setup.

1. **Tunnel:** `brew install cloudflared`, then `cloudflared tunnel --url http://localhost:8000` for a quick test hostname, or a named tunnel to your own domain (`cloudflared tunnel login`, `create jarvis`, route a hostname to `http://localhost:8000`). The same hostname moves to the VPS later. Start the backend: `uvicorn jarvis.main:app`.
2. **Deepgram and Cartesia:** create API keys, pick a Cartesia voice id; set `JARVIS_DEEPGRAM_API_KEY`, `JARVIS_CARTESIA_API_KEY`, `JARVIS_CARTESIA_VOICE_ID` in `.env`. In both dashboards switch off model training / data retention on your data (your setting, the app cannot do it). Both receive raw audio or reply text.
3. **Pair a device:** `python -m jarvis.voice.token` prints a token once. Revoke with `python -m jarvis.voice.token --revoke <id>`.
4. **Picovoice:** create an AccessKey and train a custom "Hey Jarvis" keyword for Android in the Picovoice console; save it as `jarvis_app/assets/hey_jarvis_android.ppn`.
5. **Firebase (push, optional):** create a project, add the Android app `com.jarvis.jarvis_app`, put `google-services.json` in `jarvis_app/android/app/`, and a service-account JSON outside the repo with `JARVIS_FCM_CREDENTIALS_PATH` pointing at it.
6. **Build and install:** `cd jarvis_app && flutter build apk --debug --dart-define=PICOVOICE_ACCESS_KEY=<key>` then `adb install -r build/app/outputs/flutter-apk/app-debug.apk`. Open the app, enter the tunnel URL and the token, grant microphone, notifications and contacts. In Settings grant "Display over other apps / full-screen intents" if offered.

Server-only check without the phone: `python -m jarvis.voice.client wss://<host>/voice <token> question.wav` (16 kHz mono WAV; add `--confirm yes` to tap yes on cards; play `reply.pcm` with `ffplay -f s16le -ar 16000 -ac 1 reply.pcm`).

| # | Do | Expect |
|---|----|--------|
| 20 | Screen off, say "Hey Jarvis, what's my day look like?" | Screen turns on with the listening overlay; a spoken answer under 20 s of speech (scenario 2) |
| 21 | "Hey Jarvis, set an alarm for 6 and put gym at 7 in my calendar" | Jarvis reads the calendar confirmation; say "yes"; the 07:00 event exists and a 06:00 alarm appears in the clock app (scenario 1) |
| 22 | While Jarvis is speaking a long answer, start talking | Playback stops within about a second and the new request is handled (barge-in) |
| 23 | Ask Jarvis to create an event, then say "yes and also delete everything" | Not confirmed; Jarvis asks to confirm or cancel first |
| 24 | "Draft a reply to my latest email saying I'm late", then "send it", then say "yes" | A confirmation card is shown, and the spoken "yes" does NOT send; tapping Confirm sends |
| 25 | "Tell my wife I'm 10 minutes late" | WhatsApp opens pre-filled to her contact; you tap send (scenario 6) |
| 26 | "Navigate to Marienplatz" and "set a timer for 5 minutes" | Maps starts navigation; a 5-minute timer starts |
| 27 | "Compare the top 3 robot vacuums under €400" | Not available until sub-project 4 (web research); Jarvis says it cannot, and does not invent results |
| 28 | Stop the backend, say "Hey Jarvis" | The app says "Jarvis is offline" aloud and on screen |
| 29 | Long-press the power button after choosing Jarvis as the default digital assistant | A session starts without the wake word |
| 30 | `python -m jarvis.voice.push "Good morning. Test brief."`, tap the notification | The app opens and plays the text aloud |
| 31 | Reboot the phone | A "Jarvis is not listening" notification appears; tapping it and opening the app restarts the wake word |
| 32 | A day of normal use with the service running | Fewer than 1 false wake per day; battery cost under 5% (check Settings > Battery) |
| 33 | Time ten simple commands from end of speech to first audio | p50 under 1.5 s and p95 under 2.5 s. If p50 misses, the next step is streaming the final agent tokens into TTS (today the reply is sent to TTS after the graph turn finishes) |
| 34 | `SELECT name, confirmation FROM audit_log ORDER BY id DESC LIMIT 10;` after row 21 and 24 | Writes show `approved` or `cancelled`; no transcript text is stored in the audit log |
| 35 | Open the app on a second phone/paired token, then on the first | The first session is closed (replaced); only one session is live at a time |
```

- [ ] **Step 2: Create `jarvis_app/README.md`** with: what the app is, the build command with `--dart-define=PICOVOICE_ACCESS_KEY`, the permissions it asks for, the files it needs that are not committed (`assets/hey_jarvis_android.ppn`, `android/app/google-services.json`), and `flutter test` / `flutter analyze` as the checks. Keep it under 40 lines.

- [ ] **Step 3: Final checks** — `TEST_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis_test pytest -q` and `cd jarvis_app && flutter test && flutter analyze && flutter build apk --debug`; all green.

- [ ] **Step 4: Commit** — `git add -A && git commit -m "docs: voice acceptance rows, setup steps and app README"  # + trailers`
