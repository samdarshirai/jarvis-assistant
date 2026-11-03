# Conversations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every voice session, chat and Telegram conversation its own LangGraph thread, with chat history, "New chat", resume, and a 30-minute idle rule, so the model only sees the current conversation.

**Architecture:** A `conversation` table plus `ConversationStore` (new `src/jarvis/conversations.py`) decides which thread a turn belongs to. Each entry point (`VoiceSession` connect, `AppService.chat/confirm`, `TelegramChannel`) resolves a conversation once and builds its graph config from `conversation.thread_id`. The checkpointer does the isolation; `graph.py` does not change (a fresh thread makes `followup` false, so `keyword_domain` fires again). The app gets a New chat button, a history screen and resume.

**Tech Stack:** Python 3.11, FastAPI, LangGraph + Postgres checkpointer, psycopg pool, pytest; Flutter/Dart (`flutter_test`).

**Spec:** `docs/superpowers/specs/2026-11-03-conversations-design.md`

**Prerequisite (not part of this plan):** the working tree has uncommitted temporary `DEBUG` log lines in `src/jarvis/agent/graph.py` and `src/jarvis/voice/ws.py`, and an uncommitted `_where` helper in `ws.py`. Commit the `_where` change; the `DEBUG` lines are removed in Task 7. Run Postgres tests with a local Postgres >= 15 and `export TEST_DATABASE_URL=postgresql://localhost/jarvis_test` (the DB name must end in `_test`); without it every `pool` test skips. Python is `.venv/bin/python`.

## Global Constraints

- Idle rule: a chat or Telegram conversation idle for more than **30 minutes** (`IDLE = timedelta(minutes=30)`, one constant) is closed when the next message arrives and a new one starts.
- Thread id of a new conversation: `user:{user_id}:c:{uuid4().hex}`. The legacy thread `user:{user_id}` is kept as-is for the legacy conversation.
- Every conversation query is scoped by `user_id` (`USER_REF` pattern from `src/jarvis/db.py`); one user can never read another's conversation.
- Never log message text or exception text (`tests/test_voice_robust.py` enforces it).
- Telegram has its own active conversation, separate from chat. Typing in chat while a voice conversation is open starts a separate chat conversation.
- Model tier follows the channel of the turn (`voice` flag in the graph config), not the conversation's origin.
- `HISTORY = 40` in `agent/graph.py` stays.

## Review Focus

- Message at exactly 30 min vs 30 min + 1 s after `last_at`: only the latter starts a new conversation (Task 1 test).
- Two simultaneous first messages for one user and surface must not create two open conversations or raise (unique-index race; Task 1 test).
- A voice session replaced by a newer one (`CLOSE_REPLACED`) must not close the newer conversation when the old one tears down (Task 4 test).
- `/chat/confirm` with another user's `conversation_id`, or an unknown one, answers nothing and writes nothing (Task 3 test).
- A pending confirmation card in conversation A survives "New chat" and is still answerable with A's id; a card is never answered in another conversation (Task 3 test).
- Legacy thread: user without checkpoints gets no legacy row; user with one gets exactly one even after repeated `list()`; missing checkpoint tables do not raise (Task 1 test).
- A first message that is whitespace-only or very long gives a sane title (None / trimmed to 60 chars) (Task 1 test).

---

### Task 1: Conversation table and `ConversationStore`

**Files:**
- Modify: `src/jarvis/db.py` (append the table to `SCHEMA`, using `USER_REF` like the neighbouring tables)
- Create: `src/jarvis/conversations.py`
- Test: `tests/test_conversations.py`

**Interfaces:**
- Produces (`src/jarvis/conversations.py`):
  - `IDLE: timedelta`
  - `@dataclass(frozen=True) class Conversation: id: int; channel: str; surface: str; title: str | None; thread_id: str; created_at: datetime; last_at: datetime; closed_at: datetime | None` (`channel` = where it started, never changes; `surface` = which of `"voice" | "chat" | "telegram"` currently holds it)
  - `class ConversationStore: __init__(self, pool, user_id: int, clock=lambda: datetime.now(timezone.utc))` with `open_for(surface: str) -> Conversation | None` (the open one, no idle check); `current(surface: str) -> Conversation` (open one if `clock() - last_at <= IDLE`, else close it and start a new one); `start(surface: str) -> Conversation` (closes any open one on that surface first); `touch(conv_id: int, text: str | None = None) -> None` (sets `last_at`; sets `title` from `text` when the title is null: stripped, first 60 chars, ignored if empty); `close(conv_id: int) -> None` (sets `closed_at` only if null: idempotent); `get(conv_id: int) -> Conversation | None`; `list(limit: int = 50) -> list[Conversation]` (newest `last_at` first; imports the legacy thread first); `resume(conv_id: int, surface: str) -> Conversation | None` (closes the surface's current open conversation if it is a different one, reopens the target on `surface`; returns None if unknown or currently open on `"voice"`)
  - `def conversation_json(c: Conversation) -> dict` -> `{"id", "title", "channel", "last_at" (iso), "open": c.closed_at is None}`
  - `async def messages_json(graph, thread_id: str) -> list[dict]` -> `[{"role": "user" | "assistant", "text": str}]` oldest first, human messages and assistant messages with non-empty string content only (no tool calls/results).
- Schema: table `conversation(id SERIAL PK, user_id via USER_REF, channel, surface (both CHECK IN ('voice','chat','telegram')), title, thread_id TEXT NOT NULL UNIQUE, created_at, last_at, closed_at)`; `UNIQUE (user_id, id)`; **partial unique index** on `(user_id, surface) WHERE closed_at IS NULL`; index on `(user_id, last_at DESC)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_conversations.py`. Parametrize a `store` fixture over `["pg"]` now (Task 2 adds `"memory"`); use the `pool`/`uids` fixtures and a mutable clock (`class Clock: now = datetime(2026,11,3,12,tzinfo=utc); def __call__(self): return self.now`). Tests:
  - `test_current_creates_then_reuses`: two `current("chat")` calls return the same id; `thread_id` starts with `f"user:{uid}:c:"`.
  - `test_idle_boundary`: after `clock.now += IDLE`, `current("chat").id` is unchanged; after `+ timedelta(seconds=1)` more, it is a different id and the old one has `closed_at` set.
  - `test_surfaces_are_independent`: `current("chat")`, `current("telegram")`, `start("voice")` are three different open conversations; `start("voice")` again closes the first voice one.
  - `test_race_single_open`: two threads call `current("chat")` simultaneously (barrier); both return a conversation, and exactly one open chat conversation exists.
  - `test_touch_sets_title_once`: `touch(id, "  hello  ")` -> title `"hello"`; second `touch(id, "other")` keeps it; whitespace-only text leaves it `None`; an 80-char text gives a 60-char title.
  - `test_close_idempotent_and_resume`: `close` twice keeps the first `closed_at`; `resume(old.id, "chat")` closes the current chat conversation and reopens `old` (`closed_at is None`, `surface == "chat"`); `resume` of an open voice conversation returns `None`; unknown id returns `None`.
  - `test_other_user_cannot_get_or_resume`: user B's store returns `None` for A's id on `get` and `resume`, and `list()` excludes it.
  - `test_legacy_thread_imported_once`: insert a row into `checkpoints` with `thread_id = f"user:{uid}"` (copy the minimal column set from how `tests/test_users.py` or `tests/test_isolation.py` seeds checkpoint rows; if none does, create the saver tables with `AsyncPostgresSaver(...).setup()` as `tests/test_isolation.py` does), then `list()` twice returns exactly one conversation with `thread_id == f"user:{uid}"`, title `"Earlier conversation"`, `closed_at` set; a user with no checkpoints gets none; with the checkpoint tables dropped, `list()` returns `[]` without raising.
  - `test_messages_json` (no DB): build a graph-like object whose `aget_state(cfg)` returns `SimpleNamespace(values={"messages": [HumanMessage("hi"), AIMessage("", tool_calls=[...]), ToolMessage(...), AIMessage("done")]})`; expect `[{"role":"user","text":"hi"},{"role":"assistant","text":"done"}]`; a thread with no state returns `[]`.

- [ ] **Step 2: Run to verify failure**
  Run: `TEST_DATABASE_URL=postgresql://localhost/jarvis_test .venv/bin/python -m pytest tests/test_conversations.py -v`
  Expected: collection/import error `No module named 'jarvis.conversations'`.

- [ ] **Step 3: Add the table to `SCHEMA` in `src/jarvis/db.py`** exactly as in Interfaces (idempotent `CREATE ... IF NOT EXISTS`, partial index via `CREATE UNIQUE INDEX IF NOT EXISTS`). Add `conversation` to the `TRUNCATE` list in `tests/conftest.py`.

- [ ] **Step 4: Implement `src/jarvis/conversations.py`** per Interfaces. Notes: use `pool.connection()` blocks like `kg/store.py`; `current`/`start` insert inside a transaction and, on `psycopg.errors.UniqueViolation` from the partial index, re-read `open_for(surface)` and return it (that is the race fix); the legacy import runs at the top of `list()`: if `to_regclass('checkpoints')` is not null and a row with `thread_id = f"user:{user_id}"` exists, `INSERT ... (channel,surface,title,thread_id,closed_at) VALUES ('chat','chat','Earlier conversation', <legacy thread>, now()) ON CONFLICT (thread_id) DO NOTHING`. `messages_json` reads `(await graph.aget_state({"configurable": {"thread_id": thread_id}})).values.get("messages", [])`.

- [ ] **Step 5: Run to verify pass** (same command). Expected: all PASS (the DB tests are skipped without `TEST_DATABASE_URL`; run once with it set).

- [ ] **Step 6: Commit**
  ```bash
  git add src/jarvis/db.py src/jarvis/conversations.py tests/test_conversations.py tests/conftest.py
  git commit -m "feat: conversation table and store with idle rule, surfaces and legacy import"
  ```

---

### Task 2: Runtime wiring, in-memory twin, `thread_config`, user deletion

**Files:**
- Modify: `src/jarvis/tenant.py` (`UserRuntime` gets `conversations: ConversationStore`; `Tenants._build` creates `ConversationStore(pool, user_id)`; `thread_config`)
- Modify: `src/jarvis/users.py` (delete + CLI counts)
- Modify: `tests/fakes.py` (add `MemoryConversations`; `FakeTenants.add` attaches one to each runtime)
- Test: `tests/test_conversations.py` (add `"memory"` to the `store` fixture), `tests/test_tenant.py`, `tests/test_users.py`

**Interfaces:**
- Consumes: `ConversationStore`, `Conversation` from Task 1.
- Produces:
  - `thread_config(thread: str | int, *, voice: bool = False) -> dict`. A `str` is used as the thread id; an `int` is a **transitional shim** meaning the legacy `thread_id(user_id)` (removed in Task 7). Result shape unchanged: `{"configurable": {"thread_id": ..., **({"voice": True} if voice else {})}, "recursion_limit": 40}`.
  - `tests/fakes.py::MemoryConversations(user_id: int, clock=...)`: same public methods and behaviour as `ConversationStore` (dict-backed, ids from a counter, same `thread_id` format, same idle/surface/resume rules, `list()` has no legacy import).
  - `UserRuntime.conversations` (attribute).
  - `UserStore.delete` and the `users` CLI counts also cover conversation threads.

- [ ] **Step 1: Write the failing tests**
  - In `tests/test_conversations.py` add `"memory"` to the `store` fixture params so every Task 1 contract test (except the legacy-import and race tests, which are pg-only: mark them with a `pg_only` skip) runs against `MemoryConversations` too.
  - `tests/test_tenant.py`: replace `test_thread_config_shape` with assertions that `thread_config("user:7:c:abc") == {"configurable": {"thread_id": "user:7:c:abc"}, "recursion_limit": 40}`, that `voice=True` adds `"voice": True`, and that `thread_config(7)` still equals the old `user:7` shape (shim).
  - `tests/test_users.py` (pg): after creating a user and seeding checkpoint rows with `thread_id` in `{f"user:{uid}", f"user:{uid}:c:aaa", f"user:{other}:c:bbb"}`, `UserStore.delete(uid)` removes the first two and leaves the third; the CLI dry-run count (`run(["delete", email])` output) counts both of the user's threads.

- [ ] **Step 2: Run to verify failure**
  Run: `.venv/bin/python -m pytest tests/test_tenant.py tests/test_conversations.py -v`
  Expected: FAIL (`MemoryConversations` import / `thread_config` string handling).

- [ ] **Step 3: Implement** the signature and shim in `thread_config`; add `conversations` to `UserRuntime` and `Tenants._build`; write `MemoryConversations`; in `users.py` replace both exact `WHERE thread_id = %s` matches (delete at ~line 110, counts at ~line 232) with `WHERE thread_id = %s OR thread_id LIKE %s` using `(thread_id(uid), f"user:{uid}:c:%")` (`uid` is an int, so the pattern cannot be injected). Leave the startup `owner` -> `user:{id}` rename (~line 172) unchanged.

- [ ] **Step 4: Run to verify pass**
  Run: `TEST_DATABASE_URL=... .venv/bin/python -m pytest tests -q`
  Expected: PASS (existing tests unchanged thanks to the shim).

- [ ] **Step 5: Commit** `feat: wire conversations into the runtime and user deletion`

---

### Task 3: Chat API: conversations in `/chat`, list/new/resume/get endpoints

**Files:**
- Modify: `src/jarvis/appapi.py` (`chat`, `confirm`, `_present`, new service methods), `src/jarvis/main.py` (`ConfirmIn`, new routes), `Caddyfile` (add `/conversations /conversations/*` to the `@public` path list)
- Test: `tests/test_appapi.py`

**Interfaces:**
- Consumes: `rt.conversations` (`current`, `start`, `get`, `resume`, `touch`, `list`), `conversation_json`, `messages_json`, `thread_config(thread_str)`.
- Produces (HTTP, all device-token authed like `/notes`):
  - `POST /chat {text}` -> existing fields plus `"conversation": conversation_json(...)` (server picks `rt.conversations.current("chat")`, then `touch(conv.id, text)`).
  - `POST /chat/confirm {interrupt_id, decision, conversation_id?: int}` -> existing fields plus `"conversation"`. With `conversation_id`: the conversation must exist for this user, else the standard `HANDLED_TEXT` response and nothing resumes; the interrupt is read and resumed on that conversation's thread. Without it: the open chat conversation (`open_for("chat")`, no idle check).
  - `GET /conversations` -> `{"conversations": [conversation_json...]}`.
  - `POST /conversations` -> `{"conversation": conversation_json}` (`start("chat")`).
  - `POST /conversations/{id}/resume` -> `{"conversation", "messages", "card"}`; 404 unknown/other user; 409 `{"error": "in_progress"}` when open on voice. `card` = `AppService._card(interrupt)` of the thread's pending interrupt, or `null`.
  - `GET /conversations/{id}` -> same body without changing state (works for open voice conversations); 404 as above.
  - `AppService` methods: `chat(rt, text)`, `confirm(rt, interrupt_id, decision, conversation_id=None)`, `conversations(rt)`, `new_conversation(rt)`, `resume_conversation(rt, id)`, `conversation_detail(rt, id)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_appapi.py` (use the existing `FakeTenants`/`FakeProvider` patterns in that file; the fake runtime now carries `MemoryConversations`):
  - `test_chat_response_names_conversation_and_reuses_it`: two `/chat` calls return the same `conversation["id"]`; the checkpoint thread used is `conversation.thread_id`, not `user:{uid}`.
  - `test_chat_after_idle_starts_new_conversation_with_no_history`: advance the store clock past `IDLE`; the second call has a different id and the model's input messages contain none from the first (inspect `FakeProvider` recorded calls).
  - `test_keyword_alarm_in_new_conversation_skips_router`: after an earlier exchange in conversation 1, `POST /chat "set an alarm for 6am"` in a fresh conversation is routed to `phone` with zero router-tagged LLM calls (assert on the provider's recorded tags); and the same message **without** the new conversation (legacy behaviour, same thread after a prior reply) is not asserted.
  - `test_confirm_uses_given_conversation_and_new_chat_keeps_pending_card`: raise a confirm card in conversation A; `POST /conversations` (new chat B); `/chat/confirm` with `conversation_id=A` resumes it; with `conversation_id=B` and A's interrupt id returns `HANDLED_TEXT` and the write tool was not called.
  - `test_confirm_with_foreign_or_unknown_conversation_id`: another user's id and `99999` both return the handled response and call no tool.
  - `test_resume_returns_messages_and_card_and_voice_is_refused`: resume a closed chat conversation -> `messages` oldest first and `card` for a pending interrupt; resume an open voice conversation -> 409 `in_progress`; `GET /conversations/{id}` on that open voice conversation -> 200.
  - `test_list_newest_first_and_scoped_to_user`.

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_appapi.py -v -k "conversation or keyword_alarm"`; Expected: FAIL.

- [ ] **Step 3: Implement.** `chat`/`confirm` resolve a conversation, build `cfg = thread_config(conv.thread_id)` once per call, and pass it to `_present` (new `cfg` parameter) and the interrupt checks (currently `thread_config(rt.user.id)` at `appapi.py:236,256,265`). Add the routes in `main.py` next to `/notes` using the same `guarded(request)` + `found(...)` helpers; `ConfirmIn` gains `conversation_id: int | None = None`. Add the Caddyfile paths.

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest tests/test_appapi.py tests/test_main.py -q`; Expected: PASS. Fix existing tests in these files that assert `thread_config(uid)` by building the expected thread from the response's `conversation` (`rt.conversations.get(id).thread_id`).

- [ ] **Step 5: Commit** `feat: chat conversations with history, new chat and resume endpoints`

---

### Task 4: Voice: one conversation per connection

**Files:**
- Modify: `src/jarvis/voice/ws.py` (`VoiceSession.__init__` gains `on_text`; the `/voice` handler around line 416 creates/closes the conversation)
- Test: `tests/test_voice_*.py` (add `tests/test_voice_conversation.py`; follow `tests/test_voice_confirm.py` for the websocket harness)

**Interfaces:**
- Consumes: `rt.conversations.start("voice")`, `.touch`, `.close`, `thread_config(conv.thread_id, voice=True)`.
- Produces: `VoiceSession(..., cfg, *, user_id, token, on_text: Callable[[str], None] | None = None)`; `on_text(text)` is called with each **final** transcript before the graph step (the handler passes `lambda t: rt.conversations.touch(conv.id, t)`, run via `asyncio.to_thread`-safe wrapper or a sync call that only does one short query).

- [ ] **Step 1: Write the failing tests**
  - `test_each_connection_gets_its_own_thread`: two sequential voice connections for one user use different `thread_id`s; the second's model input has none of the first's messages.
  - `test_conversation_closed_on_every_exit`: after (a) client `bye`, (b) silence timeout, (c) socket drop, (d) `CLOSE_REPLACED` by a newer connection, `rt.conversations.get(id).closed_at` is set.
  - `test_replaced_session_does_not_close_newer_conversation`: connection 1 is replaced by connection 2; after connection 1's teardown finishes, connection 2's conversation is still open.
  - `test_title_from_first_final_transcript`.

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_voice_conversation.py -v`; Expected: FAIL.

- [ ] **Step 3: Implement.** In the handler: `conv = await asyncio.to_thread(rt.conversations.start, "voice")` right after the missing-setup checks (so rejected connections create nothing); build the config from `conv.thread_id`; call `rt.conversations.close(conv.id)` in the handler's `finally` (close is idempotent and `start("voice")` for the newer connection already closed the older one, so this cannot close the newer conversation). In `VoiceSession._consume_events`, call `self.on_text(ev.text)` for final transcripts.

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest tests -q -k voice`; Expected: PASS. Update existing voice tests that pass `thread_config(uid, voice=True)` to construct the session.

- [ ] **Step 5: Commit** `feat: a voice session is its own conversation`

---

### Task 5: Telegram: own conversation, idle rule and `/new`

**Files:**
- Modify: `src/jarvis/channels/telegram.py` (remove `self.cfg`; add `_cfg`, `/new` handler, `NEW_TEXT`)
- Test: `tests/test_telegram.py`

**Interfaces:**
- Consumes: `rt.conversations.current("telegram")`, `.open_for("telegram")`, `.start("telegram")`, `.touch`.
- Produces: `TelegramChannel._cfg(rt, *, new_text: bool) -> dict`: `new_text=True` -> `current("telegram")` + `touch(conv.id, text)` (caller passes the text); `False` (button taps, re-offers) -> `open_for("telegram")` or, if none, `current("telegram")`. The returned config is `thread_config(conv.thread_id)` (callers add `channel: "telegram"` as `_invoke` does today). `NEW_TEXT = "Started a new conversation."`; command `/new` calls `start("telegram")` and sends it.

- [ ] **Step 1: Write the failing tests** (reuse `make_channel`/`owner_channel` helpers; make the fake runtime carry `MemoryConversations`):
  - `test_telegram_idle_gap_starts_new_conversation`: second message after the clock passes `IDLE` has a different thread and the model sees none of the first.
  - `test_new_command_starts_conversation_and_confirms`: `/new` replies `NEW_TEXT`; the next message uses a new thread.
  - `test_button_tap_after_idle_still_answers_pending_card`: raise a card, advance past `IDLE`, tap yes: the card's write runs (the button path uses `open_for`, not the idle rule).
  - `test_telegram_and_chat_conversations_are_separate`: a chat message and a Telegram message for the same user use different threads.
  - Update `test_telegram_uses_owner_thread_and_lock` to assert the lock and the conversation thread rather than `user:{id}`.

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_telegram.py -v`; Expected: FAIL.

- [ ] **Step 3: Implement.** Replace each `self.cfg` use (lines ~68, 111, 125, 148, 185) with a per-call config from `_cfg`; register `CommandHandler("new", self.on_new)` restricted by `self.owner_filter`.

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest tests/test_telegram.py -q`; Expected: PASS.

- [ ] **Step 5: Commit** `feat: telegram conversations with idle rule and /new`

---

### Task 6: Flutter: New chat, history, resume

**Files:**
- Modify: `jarvis_app/lib/chat_screen.dart`
- Create: `jarvis_app/lib/conversations_screen.dart`
- Test: `jarvis_app/test/chat_screen_test.dart`, `jarvis_app/test/conversations_screen_test.dart`

**Interfaces:**
- Consumes: the Task 3 endpoints.
- Produces:
  - `ChatScreen` keeps its constructor; internally tracks `int? _conversationId` from `response['conversation']['id']`. If a response carries a different id than the tracked one (idle rule started a new conversation), the message list is cleared before the reply is added.
  - `/chat/confirm` body gains `'conversation_id': _conversationId` when it is non-null.
  - Header actions: `Icons.add_comment_outlined` ("New chat", tooltip `New chat`) -> `api.post('/conversations')`, clears messages, sets the id; `Icons.history` (tooltip `Past chats`) -> pushes `ConversationsScreen`, which pops with the resume payload `Map<String, dynamic>`; the chat screen then replaces its messages with `messages` (user bubbles `mine: true`) plus the `card` if present, and sets the id.
  - `class ConversationsScreen extends StatefulWidget { const ConversationsScreen({super.key, required this.api}); final ApiClient api; }`: `api.get('/conversations')`, rows show title (or `Untitled`), a channel icon (mic for `voice`, chat bubble for `chat`, paper plane for `telegram`) and relative time; tapping calls `api.post('/conversations/$id/resume')` and pops its response; an `ApiError` with status 409 shows a snackbar `Still in a voice session` and stays.

- [ ] **Step 1: Write the failing widget tests** (extend `FakeApi` in the existing test with a `get` override and a `calls` log, as in `chat_screen_test.dart`):
  - chat: a reply with `conversation.id` 2 after a previous id 1 clears earlier bubbles; the confirm call body contains `conversation_id`; tapping `Icons.add_comment_outlined` posts `/conversations` and empties the list; resuming from the history screen shows the returned messages and a pending card.
  - conversations screen: lists rows newest first as given; tap posts `/conversations/7/resume` and pops the payload; a 409 shows `Still in a voice session`; empty list shows `No past chats yet.`

- [ ] **Step 2: Run to verify failure** — `cd jarvis_app && flutter test test/chat_screen_test.dart test/conversations_screen_test.dart`; Expected: FAIL.

- [ ] **Step 3: Implement** both files per Interfaces, following the existing cocoa theme widgets (`RoundButton`, `CocoaBackground`) used in `chat_screen.dart`.

- [ ] **Step 4: Run to verify pass** — `cd jarvis_app && flutter test`; Expected: all PASS.

- [ ] **Step 5: Commit** `feat(app): new chat, past chats and resume`

---

### Task 7: Remove shim and debug logging; docs and acceptance rows

**Files:**
- Modify: `src/jarvis/tenant.py` (drop the `int` shim), `src/jarvis/agent/graph.py` and `src/jarvis/voice/ws.py` (delete the three temporary `DEBUG` log blocks: `DEBUG phone tool`, `DEBUG router`, `DEBUG present`), remaining tests that call `thread_config(<int>)`
- Modify: `ACCEPTANCE.md` (append rows), `docs/HANDOFF.md` (short "Conversations" section: table, store, surfaces, idle rule, endpoints)
- Test: whole suite

**Interfaces:**
- Consumes: everything above. Produces: `thread_config(thread: str, *, voice: bool = False)`; an `int` argument now raises `TypeError`.

- [ ] **Step 1: Write the failing test** in `tests/test_tenant.py`: `thread_config(7)` raises `TypeError`; `thread_config("user:7")` works.
- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_tenant.py -v`; Expected: FAIL (shim still accepts ints).
- [ ] **Step 3: Remove the shim; fix every remaining caller** found by `grep -rn "thread_config(" src tests` (legacy-thread assertions use `thread_config(thread_id(uid))`). Delete the three `DEBUG` blocks (`grep -n "DEBUG" src/jarvis/agent/graph.py src/jarvis/voice/ws.py` must return nothing). Do this step only after the alarm has been confirmed working on a device (Step 5), otherwise keep the debug lines and skip them here.
- [ ] **Step 4: Run the full suites** — `TEST_DATABASE_URL=... .venv/bin/python -m pytest tests -q` and `cd jarvis_app && flutter test`; Expected: PASS, no new skips beyond the DB-less ones.
- [ ] **Step 5: Device acceptance** (add as `ACCEPTANCE.md` rows and run): (a) voice "set an alarm for 6am" -> the alarm appears in the Clock app; (b) two voice sessions do not share context (second one does not know what the first asked); (c) chat: New chat clears the screen, Past chats lists it, resuming restores messages; (d) a chat message after >30 min starts a new conversation on its own; (e) a pending confirm card survives New chat and is answerable after resuming its conversation; (f) Telegram `/new` starts a fresh thread.
- [ ] **Step 6: Commit** `chore: drop legacy thread shim and debug logging; document conversations`
