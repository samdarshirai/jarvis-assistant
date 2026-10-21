# Jarvis multi-tenant, bring-your-own-keys — design

Status: draft for approval. Date: 2026-10-20. Owner: Samdarshi.

## Intent and why

Jarvis today is a single-owner assistant: one Google token, one OpenRouter key from `.env`, one conversation thread (`"owner"`), one Telegram chat, one set of memories and notes. The owner wants a handful of friends (hand-picked, not the public) to use the same hosted backend and the Android app, each on their own Google account and **their own API keys**, without the owner paying for their LLM, voice or search usage.

Success looks like: a friend installs the app on a fresh phone, signs in with Google, pastes their keys, and within about five minutes is chatting with Jarvis about *their* calendar and mail. The owner's existing data and behaviour are unchanged. No user can ever see or affect another user's data.

## Goals

- Sign in with Google (this is also the Calendar/Gmail/Tasks consent). Only allowlisted emails may sign in.
- Per-user secrets, stored encrypted: an OpenAI-compatible LLM endpoint (base URL + API key; OpenRouter is the default), a required Deepgram key (voice) and Tavily key (web search).
- Every LLM, Deepgram, Tavily and Google call for a user uses that user's credentials. Never fall back to the operator's keys for a friend.
- All user data is scoped to a user: memories, notes, audit log, mail-watch state, alerts, auto-events, proactive state, conversation thread, devices, Google token.
- In-app onboarding wizard (mockup chosen: wizard, one step per screen).
- Proactive features (morning brief, mail-to-calendar, leave-now/conflict alerts) run per user, delivered by push notification.
- The owner's current data becomes user #1 with no loss.
- Tests that prove isolation between two users.

## Non-goals

- Public signup, billing, quotas, rate limiting per user, an admin web UI, password login, iOS, email/SMS login.
- Telegram for friends. Telegram stays as the owner's own channel (tied to user #1), unchanged.
- Friends bringing their own Firebase/FCM project: push stays on the operator's FCM credentials (push carries no secrets beyond notification text).
- Google app verification / CASA assessment. See "Google consent mode".
- The 30-day conversation retention item from HANDOFF stays a separate open item.

## Anti-goals (failure even if it technically works)

- Any request path where user A's credentials, data, or conversation can be used by or shown to user B (including via a shared cache, a shared lock, a global "current user", or the scheduler).
- Silently using the operator's OpenRouter/Deepgram/Tavily key for a friend who has not supplied one.
- A friend's API key appearing in logs, audit rows, error messages, API responses (return only "set" + last 4 characters), or the checkpointer.
- Weakening the confirmation gate or the untrusted-text handling (see HANDOFF "Safety invariants"); they apply unchanged per user.

## Decisions made with the owner

1. **Login = Sign in with Google.** Same Google web OAuth client already used by "Reconnect Google"; add `openid email` scopes to the four existing ones. One consent covers identity and data access.
2. **Few friends, added by hand.** Allowlist of emails managed by a CLI (`python -m jarvis.users add|list|disable|delete <email>`); no admin UI. Hard cap 100 users (Google's unverified-app limit).
3. **App only for friends.** Chat, voice, screens in the Flutter app. Friends get proactive alerts as push only.
4. **Google consent mode: published "In production" but unverified.** Friends see Google's "unverified app" warning once (Advanced -> Continue); refresh tokens do not expire after 7 days as they do in Testing mode. Full verification (paid annual security assessment for Gmail scopes) is out of scope. The builder should re-check Google's current rules on unverified production apps (user cap, token lifetime) and report if they differ.
5. **Friends bring all keys:** LLM, Deepgram and Tavily are all required before the Flutter app opens, for both new and existing installs. Provider fallback behavior on the backend is unchanged.
6. **Onboarding = wizard**, five labeled screens: Connect Google → AI key → Deepgram key → Tavily key → Ready. Connect Google explains Calendar, Gmail and Tasks access, the unverified-app warning and all three required API keys upfront; rejection keeps the owner-add hint and cancellation allows retry. The AI form defaults to OpenRouter, allows editing the base URL and model ids under Advanced, and requires a successful “Test and save.” Deepgram and Tavily each explain their role and key creation, with a required save; they say “Saved,” without claiming provider verification. There is no Skip. Key screens support Back, inline errors, visibility toggles, and loading states with disabled actions. Inputs scroll with the keyboard and support large text. Cocoa backgrounds, cream text, rounded cards and amber buttons follow the app theme. Use the dashboard’s Unbounded wordmark, Barlow Condensed display headings and Instrument Sans body text. Blush connection summaries contrast with dark key forms; labeled segmented progress and completed connections appear above the step. Keep Continue and Back in a bottom action area while form content scrolls, with a 560px maximum content width. Ready shows Google connected and all three keys saved; “Start using Jarvis” rechecks setup before opening the app.

   `/me.setup` is authoritative. After sign-in or restart, resume the first missing key and preserve already saved credentials. All three key flags (and Google connected) must be true to enter the app. Existing users missing any required key return to setup. Removing a required key in Settings immediately restores the gate and stops voice entry points. Setup and Settings reuse the same forms for key replacement; clear secret inputs after successful saves and show only masked saved status. A failed setup check blocks entry with a retry. “Upload keys” means paste into secure text fields. Reuse `/settings/llm`, `/settings/deepgram` and `/settings/tavily`; no new public endpoints.

## Constraints

- Stack stays: FastAPI, LangGraph + Postgres checkpointer, psycopg pool, Fernet for secrets (`JARVIS_FERNET_KEY`), Flutter Android app. Backend is a long-running single process (Railway/Caddy); no new infrastructure.
- Existing safety invariants in `docs/HANDOFF.md` remain true per user, including the confirm gate in the graph, one live voice session per device, device tokens as bearer hashes.
- User-supplied base URL means the server makes outbound requests to a user-chosen host. Treat as SSRF-sensitive: require `https`, resolve and refuse non-global addresses on every request, reuse the pinned-IP logic already written for `fetch_page` in `web.py`. Do not allow redirects to non-global addresses.
- OpenRouter-only request options (`provider: {data_collection, require_parameters, sort}`, `usage.include`) are sent only when the base URL host is `openrouter.ai`; other endpoints get a plain OpenAI-compatible request. Token/cost audit fields stay best-effort.

## What changes (shape, not code)

**Identity.** New `users` table (id, email unique, google subject, timezone, is_owner, disabled, created_at). Device tokens map to a user (`devices.user_id`); every authenticated endpoint and the voice WebSocket resolve device token -> user and pass a user context down. No process-global "current user" anywhere.

**Sign-in flow.** App starts consent via a backend endpoint (no device token yet) -> Google -> backend callback verifies the ID token/email against the allowlist, stores the encrypted Google credentials for that user, creates a device token, and returns it to the app (suggested: deep link back with a short-lived single-use code the app exchanges for the token, so the token never sits in a URL or browser history). Re-sign-in on an existing user replaces the stored Google credentials and may add a new device. The existing owner-only reconnect flow becomes the same flow.

**Secrets.** New per-user encrypted secrets store: LLM base URL, LLM key, optional fast/strong model ids (defaults from server config; editable under an "Advanced" fold, because model ids differ between endpoints), Deepgram key, Tavily key. A "test" endpoint makes one tiny completion against the supplied endpoint/model and returns a clear reason on failure ("rejected key", "out of credit", "model not found", "unreachable"). Secrets API never returns values, only set/not-set and last 4 characters.

**Data scoping.** Add `user_id` to every user-data table (`oauth_tokens` becomes `(user_id, provider)`; `mail_seen`, `alerts_sent`, `auto_events`, `proactive_state`, `memories`, `notes`, `audit_log`, `devices`) with the necessary key/index changes; all stores and queries take the user. Conversation thread id becomes per user; the shared asyncio lock becomes per user. Migration: existing rows get the owner's `user_id`; the owner's Google token, devices and the `JARVIS_OPENROUTER_API_KEY` env value are moved into user #1's records, and the owner's existing conversation history should be preserved if it is cheap (builder decides; losing it is acceptable if not).

**Per-user services.** Google clients, tool registry, LLM provider and (as needed) the graph are resolved per user, cached with a bounded size and invalidated when that user's credentials or keys change (the current process-global epoch cache in `main.py` is the thing to replace). Voice builds Deepgram STT/TTS per session from the user's key; no key means the app shows "add your Deepgram key to enable voice".

**Proactive.** The scheduler iterates enabled users for brief, mail watch and alert sweep, isolating failures per user. Notifications go to that user's devices via push; Telegram only for the owner. "Reauth required" notices become a push to that user. Brief time and timezone are per user (default from server config; the app sends the device timezone at sign-in). Weather city/coords are per user.

**Operator keeps:** the Google web OAuth client, FCM credentials, Fernet key, database, optional shared defaults for model ids. The operator's own `JARVIS_OPENROUTER_API_KEY`, Deepgram and Tavily env values stop being used for anyone except being copied into user #1 at migration.

**Removal.** `users disable` blocks sign-in, device tokens and scheduler runs for that user. `users delete` removes the user's rows and checkpoint thread (and should say what it will delete before doing it).

## Isolation verification (required)

Automated tests with two users proving: each user's tools, stores, dashboard/app endpoints, chat thread, scheduler jobs and voice sessions only touch their own data and credentials; a request with user A's device token never reaches user B's Google/LLM clients; a disabled or non-allowlisted user is refused; secrets never appear in responses, logs or audit rows; the SSRF checks reject private, loopback and link-local base URLs including via redirect.

## Left to the builder

Exact schema/migration steps, how per-user graph/registry resolution is wired, cache sizes, deep-link vs polling for returning the device token to the app, wording of onboarding copy, the test-call implementation, Flutter state handling, and the CLI's output. Update `ACCEPTANCE.md`, `docs/HANDOFF.md`, `.env.example` and the capabilities list as part of the work.

## Calls I made (not yet confirmed by the owner)

- Model ids are per user with server defaults, since other OpenAI-compatible endpoints do not share OpenRouter's ids.
- Telegram stays owner-only; friends get push only.
- Per-user timezone taken from the device at sign-in; weather and brief time per user.
- Cap of 100 users enforced by the CLI.
- `users delete` is a hard delete of that user's data.
- The key "test" makes one tiny completion call against **each** configured model (fast and strong); the wizard blocks on any failure and names the model that failed.
- **Owner identity.** `python -m jarvis.users add --owner <email>` (or an owner-email env var on first migration) creates user #1. The Google subject is filled in at the owner's first sign-in; sign-in matches by allowlisted email, so nobody else can claim user #1. The owner's existing device tokens, and so the existing install, stay valid; the migration needs no re-sign-in. Google credentials already stored are copied to user #1 as they are.
- **Failing keys in proactive jobs.** A user with a missing, rejected or out-of-credit LLM key, or a revoked Google token, is skipped for that job run. They get at most one push notice per issue per day ("your OpenRouter key was rejected, open Settings"), reusing the existing daily claim mechanism. No retry storms, no operator-key fallback.
- **App distribution.** One APK sideloaded to friends, built with the backend URL baked in (via the existing `--dart-define-from-file`) and no device token baked in. First run with no stored token shows the wizard; a stored token goes straight to the app. Wake-word, boot receiver and assistant services must treat "no token yet" as not-ready rather than failing. The sign-in deep-link scheme is registered in the Android manifest.
- **Google Cloud manual prerequisites (owner does these; the plan lists them as steps before testing):** add the sign-in redirect URI to the web OAuth client, add `openid email` to the consent screen scopes, set the publishing status to "In production" (unverified). Sign-in `state` becomes a per-attempt random nonce stored server-side and bound to the app's request, replacing today's constant Fernet blob.
- **Conversation thread.** The owner's thread stays shared across Telegram, app and voice (as today). Thread id becomes per user (e.g. `user:<id>`); the owner's checkpoint rows are renamed on migration, or abandoned if that proves costly.
