# Multi-tenant, bring-your-own-keys Implementation Plan

> **Superseded (2026-10-23):** Deepgram and Tavily are no longer per-user keys. They are operator-set (`JARVIS_DEEPGRAM_API_KEY`, `JARVIS_TAVILY_API_KEY`) and shared by all users; users bring only the LLM key. Per-user Deepgram/Tavily storage, `/settings/deepgram|tavily` and their onboarding steps were removed. Read the references to them below as historical.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a few hand-added friends use the hosted Jarvis backend and Android app on their own Google account and their own LLM/Deepgram/Tavily keys, with all data isolated per user and the owner's existing data becoming user #1.

**Architecture:** A `users` table plus `user_id` on every data table. A `Tenants` cache builds one `UserRuntime` per user (scoped stores, Google clients, per-user LLM provider, tool registry, compiled graph, per-user lock). A device token resolves to a user; every endpoint, the voice socket, Telegram (owner only) and the scheduler work through a `UserRuntime`. Sign-in is Google OAuth with a PKCE-style claim step that hands the app its device token.

**Tech Stack:** Python 3.11+, FastAPI, LangGraph + Postgres checkpointer, psycopg pool, Fernet, langchain-openai, httpx, APScheduler, pytest; Flutter (Android).

**Spec:** `docs/superpowers/specs/2026-10-20-multi-tenant-byok-design.md` (read it first; also `docs/HANDOFF.md` "Safety invariants", which must keep holding per user).

## Global Constraints

- Run Python as in HANDOFF: `cd /Users/ronalisenapati/Ronali/jarvis && . .venv/bin/activate`; tests: `TEST_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis_test pytest -q` (Postgres container `jarvis-db-1`). Flutter: `cd jarvis_app && flutter test && flutter analyze`.
- The whole suite (Python and Flutter where touched) is green at the end of every task; each task updates the existing tests its change breaks.
- Never use the operator's `JARVIS_OPENROUTER_API_KEY`, Deepgram or Tavily env values for any request after the owner migration; they are read only by `ensure_owner` (Task 1) to seed user #1's secrets.
- Secret values (LLM key, Deepgram key, Tavily key) never appear in: API responses (only `set` + last 4 chars), logs, `audit_log`, exception messages, the checkpointer.
- LLM base URL: `https` only, host must resolve to global addresses on every request, redirects refused. OpenRouter-only options (`provider: {data_collection: "deny", require_parameters: true, sort: "latency" for fast tier}`, `usage: {include: true}`) are sent only when the URL host is exactly `openrouter.ai`.
- User cap 100 (enforced in `UserStore.add`). Email is stored and compared lowercased.
- Sign-in attempt TTL 600 s (same as the existing `STATE_TTL`). Device tokens unchanged: `secrets.token_urlsafe(32)`, SHA-256 hashed at rest.
- Deep link scheme: `jarvis://signin`. Thread id for user N: `user:N`.
- Commit after each task; messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A friend with no LLM key calls chat/voice/proactive: must get `needs_setup`, never the operator's key (test sets the env key and asserts it is unused).
2. Sign-in edge cases: email in different case, email not on the allowlist, existing user whose Google `sub` differs, a disabled user: all refused without creating rows or devices.
3. Claim step abuse: wrong verifier, replay after success, expired attempt, nonce from another attempt.
4. Key/URL input mess: key with surrounding whitespace/newline, base URL with trailing slash, `http://`, no scheme, `localhost`, `169.254.169.254`, a host that resolves to a private IP, a redirect to a private IP.
5. Two users chatting at the same moment must not block each other (per-user lock), and a user disabled mid-session loses the voice socket and API access on the next request.

---

### Task 1: Users, secrets, schema, owner migration

**Files:**
- Modify: `src/jarvis/db.py` (schema), `src/jarvis/config.py`, `tests/conftest.py` (truncate list)
- Create: `src/jarvis/users.py` (stores + `ensure_owner`; CLI comes in Task 11), `tests/test_users.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class User: id: int; email: str; google_sub: str | None; tz: str; is_owner: bool; disabled: bool`
  - `class UserStore(pool)`: `add(email: str, tz: str, *, is_owner=False) -> User` (raises `ValueError` on duplicate, cap, bad email), `get(user_id) -> User | None`, `by_email(email) -> User | None`, `list() -> list[User]`, `enabled() -> list[User]`, `set_sub(user_id, sub)`, `set_tz(user_id, tz)`, `set_disabled(user_id, bool)`, `delete(user_id) -> None` (rows cascade; also deletes checkpoint rows with `thread_id = f"user:{id}"` when those tables exist).
  - `class SecretStore(pool, fernet_key)`: `put(user_id, name, value: str)`, `get(user_id, name) -> str | None`, `delete(user_id, name)`, `all(user_id) -> dict[str, str]`. Names: `llm_base_url, llm_key, model_fast, model_strong, deepgram_key, tavily_key`.
  - `ensure_owner(pool, s: Settings) -> int` returns the owner's user id; idempotent.
  - `Settings.owner_email: str = ""`; `openrouter_api_key`, `models_fast`, `models_strong` become optional (`""` default; models stay as server defaults for new users).
  - Every data table below has `user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE`: `audit_log`, `oauth_tokens` (PK `(user_id, provider)`), `devices`, `memories`, `notes`, `mail_seen` (PK `(user_id, message_id)`), `alerts_sent` (PK `(user_id, key)`), `auto_events` (PK `(user_id, event_id)`), `proactive_state` (PK `(user_id, key)`). New `users` and `user_secrets(user_id, name, blob BYTEA, PK(user_id,name))`.

- [ ] **Step 1: Write failing tests** in `tests/test_users.py`: `test_add_lowercases_and_rejects_duplicate`, `test_cap_is_100`, `test_secret_roundtrip_and_encrypted_at_rest` (raw `blob` does not contain the plaintext), `test_delete_cascades_to_every_data_table`, `test_fresh_schema_requires_user_id` (insert into `notes` without `user_id` fails), `test_ensure_owner_adopts_legacy_rows` (create legacy-shaped tables with rows, no `user_id`, plus a checkpoint row with `thread_id='owner'`; after `ensure_owner` all rows belong to the owner, PKs are the new shape, the checkpoint thread is `user:<id>`, and `user_secrets` holds `llm_key`/`model_fast`/`model_strong` copied from settings), `test_ensure_owner_is_idempotent`, `test_ensure_owner_requires_owner_email_when_legacy_rows_exist`.
- [ ] **Step 2: Run** `pytest tests/test_users.py -q`; expect failures (module missing).
- [ ] **Step 3: Implement.** `db.SCHEMA` creates the new shape for fresh databases; `init_schema` also contains an idempotent legacy-upgrade path (detect tables lacking `user_id`, add nullable column, leave NULL until `ensure_owner` adopts them, then set NOT NULL and rebuild PKs inside one transaction). `ensure_owner`: no-op if an owner exists; else if no legacy rows and no `owner_email` return without creating; else require `owner_email`, create the owner (tz from settings), adopt rows, copy secrets from settings, rename the checkpoint thread. `Settings.fernet_key` encrypts secrets.
- [ ] **Step 4: Run** the full suite; update `tests/conftest.py` truncate list (`users, user_secrets` added, `RESTART IDENTITY CASCADE`). Expect PASS.
- [ ] **Step 5: Commit** `feat(tenant): users, secrets and per-user schema with owner migration`.

---

### Task 2: User-scoped stores

**Files:**
- Modify: `src/jarvis/audit.py`, `memory.py`, `notes.py`, `proactive/store.py`, `google/auth.py` (`PgTokenStore`), `weather.py` callers if needed; their tests (`tests/test_audit.py`, `test_memory_store.py`, `test_notes_store.py`, `test_proactive_store.py`, `test_google_auth.py`, `test_weather.py`, `tests/fakes.py`)

**Interfaces:**
- Produces: constructors become `Audit(pool, user_id: int | None = None)` (`None` only for system rows; `audit.for_user(uid) -> Audit`), `MemoryStore(pool, user_id)`, `NoteStore(pool, user_id)`, `ProactiveStore(pool, user_id)` (`purge(days)` stays global via a module function `purge_all(pool, days)`; same for `Audit.purge`), `PgTokenStore(pool, user_id)`. Method signatures otherwise unchanged. `bump_epoch`/`current_epoch` are removed (Task 5 replaces them).

- [ ] **Step 1: Write failing tests** per store: `test_<store>_isolated_between_users` (two stores on the same pool; each sees only its own rows; `NoteStore.get(other_users_id)` returns `None`; `MemoryStore` cap of 50 counts per user; `ProactiveStore.claim_alert("k")` is `True` for both users independently; `PgTokenStore` tokens don't cross).
- [ ] **Step 2: Run** each file; expect FAIL.
- [ ] **Step 3: Implement**: add `AND user_id = %s` to every query and `user_id` to every insert; `ON CONFLICT` targets use the new composite keys. Update `tests/fakes.py` fakes to accept and ignore `user_id`.
- [ ] **Step 4: Run** full suite; callers in `main.py` temporarily pass the owner id from `ensure_owner` so the app still starts. Expect PASS.
- [ ] **Step 5: Commit** `feat(tenant): scope audit, memory, notes, proactive state and Google tokens by user`.

---

### Task 3: Devices belong to users

**Files:**
- Modify: `src/jarvis/voice/devices.py`, `src/jarvis/voice/token.py`, `tests/test_devices.py`, `tests/voice_helpers.py` and callers of `devices.verify`

**Interfaces:**
- Produces: `class DeviceAuth(NamedTuple): device_id: int; user_id: int`. `Devices.create(user_id) -> tuple[int, str]`, `Devices.verify(token) -> DeviceAuth | None` (None if the user is disabled), `set_fcm(device_id, fcm)`, `fcm_tokens(user_id) -> list[str]`, `revoke(device_id) -> bool`, `revoke_user(user_id) -> int`. Token CLI: `python -m jarvis.voice.token --user EMAIL [--revoke ID]`.

- [ ] **Step 1: Write failing tests**: `test_verify_returns_user_id`, `test_verify_refuses_disabled_user`, `test_fcm_tokens_only_for_that_user`, `test_revoke_user_deletes_all_their_devices`.
- [ ] **Step 2: Run** `pytest tests/test_devices.py -q`; expect FAIL.
- [ ] **Step 3: Implement**; keep the compare-all-rows constant-time loop (join `users` for the disabled check). Update every `devices.verify` caller (`dashboard.py`, `appapi.py`, `voice/ws.py`) to use `.device_id` for now.
- [ ] **Step 4: Run** full suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(tenant): bind device tokens to users`.

---

### Task 4: Per-user LLM provider, URL guard, key test

**Files:**
- Modify: `src/jarvis/llm.py`, `tests/test_llm.py`
- Create: `src/jarvis/llm_check.py`, `tests/test_llm_check.py`

**Interfaces:**
- Consumes: `web._check_url` / `_pin` logic (read `src/jarvis/web.py:104-153` and reuse it; do not copy it).
- Produces:
  - `@dataclass(frozen=True) class LLMCreds: base_url: str; api_key: str; fast: list[str]; strong: list[str]`
  - `LLMProvider(creds: LLMCreds, audit)` with the existing `get(tier, tools=None)`; no longer reads `Settings`.
  - `def creds_for(user_secrets: dict[str, str], s: Settings) -> LLMCreds | None` (None when no `llm_key`; base URL defaults to `https://openrouter.ai/api/v1`; model ids default to `s.models(tier)`).
  - `def normalize_base_url(raw: str) -> str` (strip whitespace, add nothing: requires explicit `https://`, strips trailing `/`; raises `ValueError`).
  - `def guarded_http_client() -> httpx.AsyncClient` used by every `ChatOpenAI` (re-checks resolved IPs per request through the pinned transport, `follow_redirects=False`).
  - `async def test_models(creds: LLMCreds) -> None`: one 1-token completion per model in `fast + strong`; raises `LLMCheckError(code: str, model: str | None)` with `code` in `rejected_key | out_of_credit | model_not_found | unreachable | bad_url`.

- [ ] **Step 1: Write failing tests**: `test_openrouter_extras_only_for_openrouter_host`, `test_other_host_gets_plain_request`, `test_normalize_rejects_http_and_no_scheme`, `test_normalize_strips_whitespace_and_trailing_slash`, `test_guard_rejects_loopback_link_local_metadata_and_private` (`127.0.0.1`, `localhost`, `169.254.169.254`, `10.0.0.5`), `test_guard_rejects_hostname_resolving_private` (inject resolver), `test_redirect_to_private_refused`, `test_test_models_names_failing_model` (fake transport returning 401 / 402 / 404), `test_key_never_in_error_message`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement** per the interfaces; map HTTP 401/403 -> `rejected_key`, 402 -> `out_of_credit`, 404 or "model" in body -> `model_not_found`, connect/timeout -> `unreachable`. Strip the key from any exception text.
- [ ] **Step 4: Run** full suite (existing `main.py` builds `creds_for` from the owner's secrets temporarily). Expect PASS.
- [ ] **Step 5: Commit** `feat(llm): per-user credentials, SSRF-guarded client and key test`.

---

### Task 5: Tenants, UserRuntime, thread config

**Files:**
- Create: `src/jarvis/tenant.py`, `tests/test_tenant.py`
- Modify: `src/jarvis/main.py` (`cached_service`, `build_registry`, lifespan), `src/jarvis/google/auth.py` (drop epoch), `src/jarvis/channels/telegram.py` (`THREAD` -> function), `src/jarvis/tools/research_tools.py` unchanged API, `tests/test_main.py`, `tests/test_wiring.py`

**Interfaces:**
- Produces:
  - `def thread_config(user_id: int, *, voice: bool = False) -> dict` returning `{"configurable": {"thread_id": f"user:{user_id}", **({"voice": True} if voice else {})}, "recursion_limit": 40}`; replaces `THREAD` and `VOICE_CFG`.
  - `@dataclass class UserRuntime: user: User; secrets: dict[str, str]; audit: Audit; stores (memories, notes, proactive); calendar: CalendarClient; tasks: TasksClient; gmail: GmailClient; registry: Registry; llm: LLMProvider | None; graph: CompiledGraph | None; lock: asyncio.Lock; tz: str` with property `llm_ready -> bool`. `graph`/`llm` are `None` when the user has no `llm_key`.
  - `class Tenants(pool, settings, saver, max_size=32)`: `async get(user_id) -> UserRuntime` (builds in a thread; raises `UnknownUser`), `invalidate(user_id)` (call after secrets or Google credentials change), `lock(user_id) -> asyncio.Lock` (kept for the process lifetime in a separate dict, so eviction never swaps a lock under a running step).
  - `build_registry(svc, tz, pool, tavily_key, user_id)`: Tavily search only when the user has a key; otherwise `DdgSearch()`.

- [ ] **Step 1: Write failing tests**: `test_get_builds_scoped_runtime` (two users get different stores, registries and locks), `test_runtime_without_llm_key_has_no_graph`, `test_invalidate_rebuilds`, `test_lru_evicts_but_lock_survives`, `test_tavily_key_selects_tavily_search`, `test_thread_config_shape`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.** Google clients come from `cached_service(lambda: build_service(name, version, PgTokenStore(pool, uid), fernet_key))` inside the runtime (the global epoch is deleted; credential changes call `Tenants.invalidate`). `build_graph(provider, registry, audit, saver, tz, memories=rt.memories.all)` is compiled per runtime. `main.py` lifespan constructs `Tenants` and keeps the existing owner-only paths working through `tenants.get(owner_id)` until Tasks 8-10 move them.
- [ ] **Step 4: Run** full suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(tenant): per-user runtime cache replaces global clients`.

---

### Task 6: Google sign-in and claim

**Files:**
- Modify: `src/jarvis/google/web_auth.py`, `src/jarvis/google/auth.py` (`SCOPES` + `openid`, `email`), `src/jarvis/db.py` (`auth_attempts`), `src/jarvis/main.py` (routes), `tests/test_google_web_auth.py`, `tests/test_main.py`
- Create: `src/jarvis/signin.py`, `tests/test_signin.py`

**Interfaces:**
- Produces:
  - Table `auth_attempts(nonce TEXT PK, challenge TEXT NOT NULL, created_at, device_token_enc BYTEA NULL)`.
  - `class SignIn(pool, users: UserStore, devices: Devices, secrets_key, ...)`: `start(challenge: str) -> tuple[str, str]` returns `(nonce, google_url)`; `finish(nonce, code) -> str` (exchange code, verify the ID token's `email_verified` and `sub`, require allowlisted email, create or update the user, store encrypted Google credentials via `PgTokenStore(pool, user.id)`, create a device, store the encrypted device token on the attempt, return the deep link `jarvis://signin?nonce=<nonce>`); `claim(nonce, verifier) -> str | None` (valid only if `sha256(verifier)` base64url equals `challenge`, attempt younger than 600 s; returns the device token once and deletes the row).
  - Routes (no device token needed): `POST /auth/start {challenge}` -> `{nonce, url}`; `GET /auth/callback?code&state` -> HTML page that redirects to the deep link (or an error page: "That Google account is not on the list. Ask the owner to add your email."); `POST /auth/claim {nonce, verifier}` -> `{token, needs_setup: bool}` or 401.
  - The old `/google/auth/start` and `/google/callback` are replaced by these (re-sign-in for an existing user replaces their Google credentials, adds a device, and calls `Tenants.invalidate`). `state` = the random nonce stored server-side.
  - `users.set_sub` on first sign-in; later sign-ins must match the stored `sub`.

- [ ] **Step 1: Write failing tests** (inject a fake Google exchange returning `(credentials, {"email", "sub", "email_verified"})`): `test_unlisted_email_refused_no_rows_created`, `test_email_case_insensitive_match`, `test_unverified_email_refused`, `test_sub_mismatch_refused`, `test_disabled_user_refused`, `test_owner_first_signin_sets_sub_and_keeps_existing_devices`, `test_claim_wrong_verifier_none`, `test_claim_replay_none`, `test_claim_expired_none`, `test_claim_for_other_nonce_none`, `test_callback_with_unknown_state_400`, `test_credentials_stored_encrypted_for_that_user_only`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.** `Flow` as in the existing `web_auth._flow`; request `access_type=offline`, `prompt=consent`; ID token verified with `google.oauth2.id_token.verify_oauth2_token` against the web client id. Purge attempts older than TTL on each `start`.
- [ ] **Step 4: Run** full suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(auth): Google sign-in with allowlist and PKCE-style device claim`.

---

### Task 7: Account and key endpoints, setup gating

**Files:**
- Modify: `src/jarvis/main.py` (routes), `src/jarvis/appapi.py`
- Create: `src/jarvis/keys.py`, `tests/test_keys.py`

**Interfaces:**
- Consumes: `Tenants`, `SecretStore`, `llm_check.test_models`, `normalize_base_url`.
- Produces (all device-token authenticated, resolved to `UserRuntime`):
  - `GET /me` -> `{email, tz, setup: {google: bool, llm: bool, deepgram: bool, tavily: bool}, keys: {llm_base_url, llm_key_last4, model_fast, model_strong, deepgram_last4, tavily_last4}}`.
  - `PUT /settings/llm {base_url, api_key, model_fast?, model_strong?}`: normalize, test every model, save only on success, `Tenants.invalidate`; failures return 422 `{error: <code>, model}`.
  - `PUT /settings/deepgram {api_key}`, `PUT /settings/tavily {api_key}`; `DELETE /settings/deepgram|tavily`. Keys are stripped of whitespace; empty -> 422.
  - `PUT /me {tz}` (IANA name validated with `zoneinfo`).
  - `/chat` and `/chat/confirm` return 409 `{error: "needs_setup", missing: "llm"}` when `rt.llm_ready` is false.

- [ ] **Step 1: Write failing tests**: `test_me_never_returns_secret_values`, `test_put_llm_saves_only_after_passing_test`, `test_put_llm_failure_saves_nothing_and_names_model`, `test_key_is_stripped_of_whitespace_and_newline`, `test_http_url_and_localhost_rejected_422`, `test_chat_without_llm_key_is_needs_setup_even_if_operator_key_in_env`, `test_delete_optional_key`, `test_bad_timezone_rejected`, `test_unauthenticated_401`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement** (`keys.py` holds the handlers' logic so `main.py` stays routing only). Secrets values are never logged; use `last4` helper.
- [ ] **Step 4: Run** full suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(settings): per-user key endpoints and setup gating`.

---

### Task 8: App API, dashboard and chat run per user

**Files:**
- Modify: `src/jarvis/appapi.py`, `src/jarvis/dashboard.py`, `src/jarvis/main.py`, `tests/test_appapi.py`, `tests/test_dashboard.py`, `tests/test_main.py`

**Interfaces:**
- Produces: `AppService(devices, tenants, audit_root, steps)` and `DashboardService(devices, tenants, ...)` no longer hold clients. `async authenticate(authorization) -> UserRuntime | None` (bearer, `Devices.verify`, `tenants.get`; disabled users get `None`). Every `AppService` method takes the runtime as its first argument and uses `rt.calendar/tasks/gmail/notes`, `rt.graph`, `thread_config(rt.user.id)` and `rt.lock` (per-user; no global lock). Weather coordinates and city are read and written through `rt.proactive` (`last_location`); `weather_city` comes from the user's `proactive_state` `weather_city` key (falls back to server `weather_city`).

- [ ] **Step 1: Write failing tests**: `test_app_endpoints_use_callers_google_clients` (two users with distinct fake clients; each GET returns only its own events/tasks/mail/notes), `test_note_of_other_user_404`, `test_chat_uses_user_thread_and_lock`, `test_two_users_chat_concurrently_not_serialised` (user A's step blocked on an event; user B's chat completes), `test_disabled_user_gets_401_on_next_request`, `test_dashboard_scoped`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.** Audit rows written by app writes use `rt.audit`. The shared teardown drain set `steps` stays process-wide.
- [ ] **Step 4: Run** full suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(tenant): app API, dashboard and chat per user`.

---

### Task 9: Telegram (owner) and voice per user

**Files:**
- Modify: `src/jarvis/channels/telegram.py`, `src/jarvis/voice/ws.py`, `src/jarvis/voice/protocol.py` (new error code), `src/jarvis/main.py`, `tests/test_telegram.py`, `tests/test_voice_*.py`, `tests/voice_helpers.py`, `tests/fakes.py`

**Interfaces:**
- Produces: `TelegramChannel(tenants, owner_user_id, owner_chat_id, deliver_actions, undo)` resolves the owner runtime per update and uses `thread_config(owner_id)` and `tenants.lock(owner_id)`. `VoiceService(tenants, devices, stt_factory=DeepgramSTT, tts_factory=DeepgramTTS)`; `handle(ws)` authenticates to `DeviceAuth`, loads `rt`, and builds STT/TTS from `rt.secrets["deepgram_key"]`. Without the key the socket closes with the new close code `4403` and `{"type":"error","code":"needs_deepgram"}` sent first; without an LLM key `needs_llm`. `VoiceSession` takes `graph`, `lock`, `cfg=thread_config(uid, voice=True)` from the runtime. `VoiceService.deliver(user_id, actions)` pushes only to that user's live session. One live session per device stays; a user disabled mid-session is closed on the next utterance (re-verify device on each graph step).

- [ ] **Step 1: Write failing tests**: `test_voice_uses_users_deepgram_key` (factory receives the key), `test_voice_without_deepgram_key_closes_4403`, `test_voice_without_llm_key_closes_4403_needs_llm`, `test_voice_sessions_of_two_users_use_own_threads`, `test_deliver_reaches_only_target_user`, `test_telegram_uses_owner_thread_and_lock`, `test_telegram_shared_thread_with_owner_voice` (owner Telegram + owner voice use the same `user:<id>` thread and lock), `test_disabled_user_session_closed`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement**; the owner user id for Telegram is the `is_owner` user from `ensure_owner`. Existing voice invariants (shielded steps, spoken-yes rules) are untouched.
- [ ] **Step 4: Run** full suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(tenant): voice per user keys and sessions, Telegram bound to owner`.

---

### Task 10: Proactive jobs per user

**Files:**
- Modify: `src/jarvis/proactive/scheduler.py`, `notify.py`, `brief.py` (call sites only), `main.py`, `tests/test_proactive_scheduler.py`, `tests/test_proactive_notify.py`, `tests/fakes.py`

**Interfaces:**
- Produces: `start_proactive(s, tenants, users: UserStore, bot, devices)`. `make_notifier(bot, chat_id, fcm_credentials_path, devices, user_id, telegram: bool)`: `telegram()` is a no-op unless `telegram` (owner); `push` sends to `devices.fcm_tokens(user_id)`. Jobs `mail` (every `mail_poll_minutes`) and `sweep` (every 5 min) loop over `users.enabled()`, each user in its own `try/except` so one failure never stops the others; users without an LLM key skip `mail` and `brief`; users without Google credentials are skipped. The brief is no longer a fixed cron: the 5-minute tick runs `brief_due(user, now) -> bool` (weekday in the user's tz, local time within `[brief_time, brief_time + 60 min]`) and `claim_alert(f"brief:{date}")` ensures once per day. The reauth/failure notice becomes a push (and Telegram for the owner) once per issue per day via `claim_alert(f"issue:{kind}:{date}")` with texts: "Jarvis needs Google access again. Open the app and sign in." / "Your LLM key was rejected. Open Settings in the app." / "Your LLM account is out of credit."

- [ ] **Step 1: Write failing tests**: `test_each_user_gets_own_brief_in_own_timezone`, `test_brief_once_per_day_and_survives_restart_within_window`, `test_user_without_llm_key_skipped_no_operator_fallback`, `test_one_user_failing_does_not_stop_others`, `test_friend_notice_is_push_only`, `test_issue_notice_once_per_day`, `test_mail_state_isolated_between_users`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.** Classify LLM failures via `llm_check` error codes mapped from the provider exception.
- [ ] **Step 4: Run** full suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(tenant): proactive jobs per user with push delivery`.

---

### Task 11: `users` CLI

**Files:**
- Modify: `src/jarvis/users.py`
- Create: `tests/test_users_cli.py`

**Interfaces:**
- Produces: `python -m jarvis.users add <email> [--owner] [--tz IANA]`, `list`, `disable <email>`, `enable <email>`, `delete <email> [--yes]`; `def run(argv: list[str], pool, s: Settings) -> str` (pure, returns the output text). `add --owner` calls `ensure_owner` semantics (errors if an owner exists). `disable` also calls `Devices.revoke_user`. `delete` prints what will be removed (counts per table) and requires `--yes`.

- [ ] **Step 1: Write failing tests**: `test_add_and_list`, `test_add_over_cap_errors`, `test_disable_revokes_devices`, `test_delete_requires_yes_and_reports_counts`, `test_delete_removes_checkpoint_thread`, `test_add_owner_twice_errors`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement** `run` + `main()` (mirror `voice/token.py` structure).
- [ ] **Step 4: Run** full suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(users): allowlist CLI`.

---

### Task 12: Cross-cutting isolation suite

**Files:**
- Create: `tests/test_isolation.py`

**Interfaces:**
- Consumes: everything above, via the real `create_app` with fake Google/LLM clients injected through `Tenants`.

- [ ] **Step 1: Write the tests** (they should already pass; any failure is a real leak to fix in the owning task's code): `test_token_a_never_reaches_user_b_clients`, `test_memories_notes_audit_mail_state_isolated_end_to_end`, `test_checkpoint_threads_distinct`, `test_secrets_absent_from_all_responses_logs_and_audit` (capture logs, scan audit rows and JSON bodies for the planted key strings), `test_unlisted_and_disabled_users_refused_on_every_route`, `test_operator_env_keys_unused_by_friend_requests`, `test_ssrf_suite_through_put_llm` (private, loopback, link-local, redirect, rebinding resolver).
- [ ] **Step 2: Run** `pytest tests/test_isolation.py -q`; fix leaks found in the earlier tasks' code.
- [ ] **Step 3: Run** full suite. Expect PASS.
- [ ] **Step 4: Commit** `test(tenant): cross-user isolation suite`.

---

### Task 13: Flutter onboarding wizard and settings

**Files:**
- Modify: `jarvis_app/lib/store.dart`, `config.dart`, `app.dart`, `settings_screen.dart`, `api.dart`, `main.dart`, `wake.dart` (guard), `jarvis_app/android/app/src/main/AndroidManifest.xml`, `jarvis_app/pubspec.yaml`, Kotlin entry points as needed
- Create: `jarvis_app/lib/auth.dart`, `onboarding_screen.dart`, `keys_screen.dart`; tests `jarvis_app/test/auth_test.dart`, `onboarding_test.dart`, `keys_screen_test.dart`; update `app_test.dart`, `settings_screen_test.dart`, `config_test.dart`

**Interfaces:**
- Consumes: backend `POST /auth/start|claim`, `GET /me`, `PUT /settings/llm|deepgram|tavily`, `DELETE`, `PUT /me`.
- Produces:
  - `ConfigStore.load()` returns a `Config` whose URL is the baked `JARVIS_URL` (via `--dart-define-from-file=jarvis.env.json`) and token is the stored sign-in token; no token baked into builds. `null` token means "show the wizard".
  - `class AuthClient`: `Future<SignInStart> start()` (generates a random verifier, sends `challenge = base64url(sha256(verifier))`, returns the Google URL), `Future<String> claim(String nonce)` (sends the stored verifier; saves the token).
  - Wizard (mockup chosen by the owner, 4 screens): (1) Sign in with Google (explains rejection: "not on the list, ask the owner"); (2) Connect Google (explains the "unverified app" warning: tap Advanced, then Continue); (3) LLM base URL + key (+ "Advanced" fold with fast/strong model ids), required, "Test and save" shows the backend's `error`/`model` in plain words, with how-to text for getting a key; (4) optional Deepgram and Tavily keys with how-to text and Skip. Chat and voice are locked until `/me.setup.llm` is true. `KeysScreen` in Settings reuses the same inputs and shows only `set` + last 4.
  - Deep link `jarvis://signin?nonce=...` registered in the manifest and handled by the app (use the `app_links` package; add it to `pubspec.yaml`). On return, the app calls `claim`.
  - The app sends the device timezone via `PUT /me` after sign-in.
  - Entry points (`WakeService.ensureRunning`, boot receiver, assistant services, push init) treat "no token yet" as not ready and do nothing instead of failing.

- [ ] **Step 1: Write failing tests**: `auth_test`: `start_sends_challenge_not_verifier`, `claim_sends_verifier_and_stores_token`; `onboarding_test`: `shows_sign_in_first_when_no_token`, `rejection_message_shown`, `llm_step_blocks_continue_until_save_succeeds`, `llm_error_names_model`, `optional_step_can_be_skipped`, `lands_on_home_after_finish`; `keys_screen_test`: `shows_last4_never_full_key`, `delete_optional_key`; `app_test`: `no_token_shows_wizard`, `token_goes_to_home`, `wake_service_not_started_without_token`.
- [ ] **Step 2: Run** `flutter test`; expect FAIL.
- [ ] **Step 3: Implement** per the interfaces; reuse the cocoa/blush/amber theme and `ScreenShell`.
- [ ] **Step 4: Run** `flutter test && flutter analyze`; then `flutter build apk --debug --dart-define-from-file=jarvis.env.json` (see memory note: always pass the env json). Expect PASS and a built APK.
- [ ] **Step 5: Commit** `feat(app): sign-in wizard, key management and no-token start`.

---

### Task 14: Docs, config, acceptance, Google console prerequisites

**Files:**
- Modify: `.env.example`, `docs/HANDOFF.md`, `ACCEPTANCE.md`, `docs/DEPLOY-RAILWAY.md`, `jarvis_app/lib/capabilities.dart` (only if the user-facing list changes), `docs/RUN_ON_PHONE.md`

- [ ] **Step 1:** `.env.example`: add `JARVIS_OWNER_EMAIL`; mark `JARVIS_OPENROUTER_API_KEY`, `JARVIS_DEEPGRAM_API_KEY`, `JARVIS_TAVILY_API_KEY` as "seeded into the owner's account once, then unused"; keep models as defaults for new users.
- [ ] **Step 2:** `docs/DEPLOY-RAILWAY.md`: ordered prerequisites the owner does by hand before testing: (1) add `<PUBLIC_BASE_URL>/auth/callback` as an authorized redirect URI on the web OAuth client; (2) add `openid` and `email` to the consent-screen scopes; (3) publish the app "In production" without verification and confirm Google's current limits (user cap, token lifetime) as spec decision 4 requires; (4) set `JARVIS_OWNER_EMAIL` and deploy so `ensure_owner` migrates; (5) `python -m jarvis.users add friend@gmail.com`; (6) send the APK built with `--dart-define-from-file=jarvis.env.json`.
- [ ] **Step 3:** `ACCEPTANCE.md`: new rows for owner migration (existing phone still works), friend sign-in on a fresh phone through the wizard, rejected unlisted email, bad key message, out-of-credit message, voice without Deepgram key, two phones at once, `users disable` kicks a live session, weekly token survival (token still valid after more than 7 days).
- [ ] **Step 4:** `docs/HANDOFF.md`: replace "single-user" statements with the tenant model, list new modules (`users.py`, `tenant.py`, `signin.py`, `keys.py`, `llm_check.py`), update the safety invariants (per-user lock/thread, no operator-key fallback) and open items.
- [ ] **Step 5:** Run the full Python and Flutter suites once more; commit `docs: multi-tenant deployment, acceptance and handoff`.
