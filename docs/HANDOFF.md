# Jarvis — handoff note (state at 2026-10-02, `main` @ 5657ba5)

Read this first in a new session, then the spec/plan of whatever you work on next.

## What Jarvis is
A single-user personal AI assistant (owner: Samdarshi) managing Google Calendar, Gmail, Tasks and notes, reachable by Telegram chat and a Pixel voice app ("Hey Jarvis"). Product spec: the claude.ai artifact "Jarvis — Personal AI Assistant PRD" (https://claude.ai/artifact/RV9NmxXNr4FJJGE9KZM5Vj, a Claude Docs document; read it through the Claude Docs connector, FR-1..FR-24, scenarios 1-9, NFRs). The work is split into 5 sub-projects, each with its own spec -> plan -> subagent-driven build -> review -> merge cycle.

| # | Sub-project | Status |
|---|---|---|
| 1 | Agent core, Telegram, Calendar, Tasks | DONE, merged |
| 2 | Gmail tools | DONE, merged |
| 3 | Pixel voice app (server + Flutter) | DONE, merged (device-only behaviour untested, see below) |
| 4 | Memory, notes, research (FR-20..22, scenario 5 and 9) | DONE, merged |
| 5 | Proactive features (morning brief, alerts, email-to-calendar FR-11 auto-detection, FR-23/24) | NOT STARTED |

Specs and plans (all committed): `docs/superpowers/specs/2026-10-01-jarvis-agent-core-design.md`, `...-02-jarvis-gmail-design.md`, `...-02-jarvis-voice-design.md`; plans in `docs/superpowers/plans/` with the same names. `ACCEPTANCE.md` is the manual acceptance table (rows 1-48) and all setup steps.

## Stack and layout
Python 3.11+ (host 3.14), FastAPI, LangGraph (+ Postgres checkpointer), langchain-openai against OpenRouter (`data_collection: "deny"`, `require_parameters: true`), python-telegram-bot 22 long-polling, Postgres 16 (audit log, encrypted Google OAuth token, `devices`, checkpointer), Google API client, Fernet, pydantic-settings, pytest + pytest-asyncio. Voice: Deepgram STT, Cartesia TTS over WebSocket, FCM push via HTTP v1 (httpx). App: Flutter, Android only, package `com.jarvis.jarvis_app`.

- `src/jarvis/agent/graph.py` — parent graph: router -> agent -> gate -> tools/reject -> agent ... -> advance. Domains: calendar, tasks, gmail, phone, memory, notes, research, chat (`agent/domains.py`). `voice` config flag (`config["configurable"]["voice"]`) switches every domain except gmail and research to the fast model tier.
- `src/jarvis/tools/` registry + calendar/task/gmail/phone tools. `Tool.needs_confirm` defaults True; `untrusted` tools (search_emails, read_email, web_search, fetch_page) are wrapped in `<untrusted_email>` or `<untrusted_web>` per tool and redacted in the audit log. Phone tools only queue `client_actions`.
- `src/jarvis/channels/telegram.py`, `src/jarvis/voice/` (`ws.py` VoiceService/VoiceSession, `stt.py`, `tts.py`, `confirm.py`, `protocol.py`, `devices.py`, `token.py`, `push.py`, `client.py`), `src/jarvis/main.py` (lifespan wiring, `/health`, `/voice`).
- `jarvis_app/` Flutter app (`lib/session.dart` SessionController with ports; `wake.dart`; `app.dart` AppHost with a pending-start slot; Kotlin assistant services under `android/app/src/main/kotlin/`).
- `src/jarvis/memory.py`, `notes.py`, `web.py`, `tools/{memory,note,research}_tools.py`; the memory block is injected in every domain.
- `tests/` 448 Python tests (fakes in `tests/fakes.py`, voice helpers in `tests/voice_helpers.py`); `jarvis_app/test/` 65 Flutter tests.

## Safety invariants (do not break)
- Every side effect goes through the graph's `interrupt()` confirmation gate (in the graph, not the prompt). A mixed step (confirm-gated + non-gated call) never interrupts: the gated call is refused "propose it again by itself".
- Confirm taps/buttons carry the interrupt id; stale/bare taps get "Already handled."; new text while a confirmation is pending is refused (Telegram re-offers the card with buttons; voice re-presents it).
- Voice may resume a confirmation only by a bare yes/no (`voice/confirm.py`, plain code) AND only for the interrupt that session itself presented (`VoiceSession.offered`); a spoken "yes" never confirms `send_draft` (tap only; a spoken "no" still cancels).
- One `asyncio.Lock` shared by Telegram and voice serialises "read pending -> decide -> ainvoke" on the single thread `thread_id: "owner"`. A voice graph step is shielded: barge-in/disconnect/replacement abandon waiting but never cancel a running step (so a confirmed write is always audited).
- Untrusted wrapper is per-tool (`untrusted_email` / `untrusted_web`); research output is redacted in the audit log; `fetch_page` refuses non-global addresses on every hop and pins the connection to the checked IP; `remember` is gated so a page cannot plant a memory silently.
- Untrusted email text is data: wrapped, redacted in audit, and a write proposed after reading email carries a visible warning (also spoken by voice). Audit log purged at 90 days; no audio/transcripts are stored or audited.
- Device token: sent as `Authorization: Bearer`, checked in constant time against a SHA-256 hash, connection closed before accept on a bad token; one live voice session (new connection replaces the old).

## Environment and commands
- `python` is not on PATH: `cd /Users/ronalisenapati/Ronali/jarvis && . .venv/bin/activate`.
- Postgres container `jarvis-db-1` on localhost:5432 (`docker compose up -d db`). Tests: `TEST_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis_test pytest -q` (the DB name must end in `_test`; created automatically; DB tests skip otherwise). Flutter: `cd jarvis_app && flutter test && flutter analyze`; `flutter build apk --debug` works here (Android SDK installed, needs platform 37; a symlink `~/Library/Android/sdk/platforms/android-37 -> android-37.0` was created outside the repo during the build — undo with `rm` if unwanted).
- Config in `.env` (see `.env.example`: OpenRouter key and model lists, Telegram token/owner chat id, Fernet key, Deepgram/Cartesia/FCM settings, optional `JARVIS_TAVILY_API_KEY` for web search). Google OAuth: `client_secret.json` + `python -m jarvis.google.auth` (scopes: calendar, tasks, gmail.readonly, gmail.compose; re-consent once after the Gmail sub-project).
- Voice tools: `python -m jarvis.voice.token` (create/`--revoke` a device token), `python -m jarvis.voice.client URL TOKEN file.wav` (server test client), `python -m jarvis.voice.push "text"` (tap-to-play push).
- Gitignored user artefacts the app needs: `jarvis_app/android/app/google-services.json` (Firebase; the google-services plugin is applied only if it exists).
- Wake word: Porcupine was replaced by sherpa-onnx keyword spotting (merged to `main`). Model and "Hey Jarvis" keyword are bundled in `jarvis_app/assets/kws/`, no key. `keywordsThreshold` in `wake.dart` is untuned on a real device; speech right after the wake word may be clipped (no ring buffer).
- No git remote; everything is on local `main`. Commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and a `Claude-Session:` trailer. Some early commits carry a "Claude Haiku 4.5" trailer (cosmetic).

## How the work has been run (workflow to repeat)
`/superpowers:brainstorming` (one question at a time, then a written spec in `docs/superpowers/specs/`, user approves) -> `/superpowers:writing-plans` (plan in `docs/superpowers/plans/`, user reviews and picks the execution method; the user has always chosen "Subagent-driven") -> `/superpowers:subagent-driven-development` (feature branch, ledger in `.superpowers/sdd/<plan>/progress.md`, fresh implementer + spec/quality reviewer per task with up to 5 fix rounds, final whole-branch review on the most capable model, ONE fix wave + one scoped re-review) -> `/superpowers:finishing-a-development-branch` (the user picks "merge back locally"). Rulings are recorded as `Ruling: ... — ... — costs: ...` in the ledger and reported at the end.

## Open items
- Sub-project 5 not started. Known limits of 4: notes are plain text with keyword search only (no embeddings); no automatic memory extraction; `fetch_page` does not support non-ASCII (IDN) hostnames yet; the confirm card for a note shows only the first 200 characters of its text and states the size; spoken yes can save note text beyond that preview; `fetch_page` opens only URLs returned by `web_search` (exfiltration guard; a pasted link must be searched for first); `read_note` output is not wrapped as untrusted even if the note was saved from web text (the save was owner-confirmed). For 5: the brief content, the scheduler and push delivery of it (the push CLI/app path already exists) depend on 5; automatic FR-11 detection belongs to 5.
- 30-day conversation-transcript retention is NOT implemented (the Postgres checkpointer keeps full conversations, including email bodies, with no retention). Needs its own design; more significant now that voice turns land there too.
- PRD scenario 7 ("evening in India" and "before 09:00 CET") is internally inconsistent; its acceptance check is held until the PRD owner clarifies.
- Manual acceptance has NOT been run by the user: `ACCEPTANCE.md` rows 1-19 (Telegram, Gmail), 20-38 (Pixel: wake word, screen wake, barge-in/echo, intents, assistant long-press, push, latency vs PRD targets, battery) and 39-48 (memory, notes, research; needs `JARVIS_TAVILY_API_KEY`). Device-only behaviour is unverified: wake word, full-screen-intent wake, assistant role, FCM tap, Kotlin services, echo cancellation.
- Known limits: search queries (sent to Tavily) and indexed URL variants (?d=a, ?d=b) are low-bandwidth leak channels that the fetch_page allowlist does not close.
- Known limits: the voice reply is sent to TTS only after the graph turn completes (if measured p50 misses 1.5 s, stream the final agent tokens into TTS; ACCEPTANCE row 33); Telegram graph steps are not shielded; an abandoned voice step that fails is only logged.
- Deferred minors: `PcmPlayer` never calls `closePlayer`; `PairingScreen` accepts empty/invalid URL and token; hard-coded `compileSdk = 37` needs platform 37 on every build machine; stop + quick restart can stop a newer session's mic (narrow race); unknown phone actions are shown on screen only (the protocol has no upstream error frame); Telegram `deliver_actions` raising after the reply; pubspec description placeholder; revoking a device does not end its live session and a 4401 shows as "Jarvis is offline"; raw JSON is read aloud for a gated tool without a `describe`; search_emails makes up to 21 sequential Gmail calls; `update_draft` lacks reply-subject normalisation; Bcc is dropped on `update_draft`; the 403 mapping matches "insufficient" anywhere in the error text.
- Deliberate deviations from the voice spec (documented, safe): FCM via the HTTP v1 API with httpx instead of Firebase Admin; paste-only pairing (no QR); the voice fast tier applies to every domain except Gmail and research (calendar and tasks included; revisit after measuring scenarios 1, 2, 6, 9); a spoken "no" still cancels a `send_draft` card.

## Suggested next step
Either run the manual acceptance (rows 1-48) on the Pixel and laptop first (it will surface real-world latency/echo/permission issues before more is built on top), or start sub-project 5 with `/superpowers:brainstorming` (decide whether the morning brief auto-plays or plays on tap; the tap-to-play path is already built).
