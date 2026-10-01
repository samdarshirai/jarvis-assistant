# Jarvis sub-project 3: Pixel voice app

Source: Jarvis PRD (https://claude.ai/artifact/RV9NmxXNr4FJJGE9KZM5Vj), FR-14 to FR-19 and the voice latency target. Builds on `docs/superpowers/specs/2026-10-01-jarvis-agent-core-design.md` and `docs/superpowers/specs/2026-10-02-jarvis-gmail-design.md` (both merged on `main`).

## Goal and success criteria

Samdarshi says "Hey Jarvis" to a Pixel with the screen off and talks to the same agent that serves Telegram: spoken replies stream back, speech interrupts playback, side effects are confirmed by voice or tap, and phone actions (alarm, timer, navigation, message compose) run on the device.

Done when:
- "Hey Jarvis" wakes the app on-device with the screen off, the screen turns on with a listening overlay, and audio streams to the backend (FR-14, FR-15).
- Replies stream back as speech and the user can interrupt (FR-16).
- PRD scenarios 1, 2, 6 and 9 work by voice. Scenario 1: the 07:00 event is created on the server and the app sets the 06:00 alarm.
- A chat started in Telegram can continue by voice and the reverse (FR-1): voice uses the same thread.
- A side-effect action needs a confirmation. A spoken bare yes/no is accepted for calendar and task changes. `send_draft` accepts an on-screen tap only.
- The app is selectable as default digital assistant; a power-button long-press opens a session (FR-17).
- A push notification opens a session that plays its text aloud (scenario 8 mechanism only; the brief itself is sub-project 5).
- When the backend is unreachable the app says so aloud and on screen.

Decisions made with the user:
- One spec covers the server and the Flutter app. The Android parts cannot be verified in the build environment; they are covered by manual acceptance rows.
- Hosting stays on the laptop for now, reached through a Cloudflare Tunnel (public TLS hostname; the same hostname moves to the VPS later).
- Pipeline is server-orchestrated: one WebSocket, Deepgram streaming STT, the existing graph, Cartesia streaming TTS.
- Voice confirmations: spoken yes/no, except `send_draft`, which is tap-only.
- Extras in scope beyond P0: FR-19 (navigation and WhatsApp/SMS compose), FR-17 (default assistant), FCM push with tap-to-play. The morning brief content and scheduler belong to sub-project 5.

Out of scope: iOS, wake word other than "Hey Jarvis", on-device STT/TTS, multiple devices or users, call handling, the morning brief itself, notes and memory (sub-project 4), transcript retention (separate follow-up).

## Architecture

A new `voice` channel next to `telegram`, driving the same LangGraph graph and the same thread (`thread_id: "owner"`). No change to the graph's safety rules. Server pieces live in `src/jarvis/voice/`; the app lives in `jarvis_app/`.

### Server

- **Files:** `voice/ws.py` (the `/voice` endpoint and session loop), `voice/protocol.py` (frame types), `voice/stt.py` and `voice/tts.py` (Deepgram and Cartesia adapters behind small interfaces so tests use fakes), `voice/confirm.py` (strict yes/no matcher), `voice/devices.py` (token check, device store, FCM send), `voice/token.py` (CLI), `voice/client.py` (test client), `tools/phone_tools.py` (client-action tools). Edits to `main.py`, `config.py`, `db.py`, `agent/domains.py`, `agent/graph.py` only where noted.
- **Endpoint:** `WS /voice`. The device token is read from the `Authorization` header and checked in constant time against the stored hash. A bad or missing token closes the socket before any audio is read. One live voice session at a time: a new connection replaces the old one.
- **Frames:** binary frames carry 16 kHz mono PCM up (mic) and TTS audio down. Text frames carry JSON control messages:
  - up: `hello`, `cancel` (barge-in), `confirm` (`yes`|`no` plus interrupt id, from a tap), `speak` (text to voice aloud, for push), `bye`;
  - down: `state` (`listening`|`thinking`|`speaking`), `transcript`, `confirm_card` (summary, interrupt id, `tap_only`, `after_untrusted`), `client_actions`, `error`.
- **Turn:** Deepgram streaming STT with endpointing, then a text turn through `graph.ainvoke` exactly as Telegram does, then `turn_replies` split into sentences and fed to Cartesia streaming TTS. Audio starts on the first sentence. The voice turn uses the fast model tier for the router and each domain agent where a domain is not already pinned to the strong tier (Gmail stays strong); the voice-latency model choice is an open item.
- **Barge-in:** a `cancel` frame, or speech detected during playback, stops TTS and cancels the in-flight graph task. A tool call that already ran is not rolled back; the audit log stays truthful.
- **Confirmations:** when the graph interrupts, the server speaks the `describe` summary (and the untrusted-email warning when `after_untrusted`) and sends a `confirm_card`.
  - `voice/confirm.py` accepts only an utterance that is, as a whole, one of yes, yeah, confirm, do it, no, cancel, stop (after trimming and lowercasing). Anything else is treated as a new request and refused with "Confirm or cancel the pending action first", as the Telegram channel does. The matcher is plain code, not the LLM.
  - If any pending action is `send_draft`, the card is `tap_only`: a spoken yes is ignored and Jarvis says to tap Confirm.
  - Resume is `Command(resume=bool)` bound to the interrupt id; a stale or repeated id gets "Already handled".
  - A failed resume re-offers the pending confirmation, as in sub-project 1.
- **Phone tools** (domain `phone`, none confirm-gated, none untrusted): `set_alarm(hour, minute, label?)`, `set_timer(seconds, label?)`, `start_navigation(destination)`, `compose_message(app, contact, text)`. They do not call any service; they append a typed entry to `client_actions` (the state field already exists) and the tool result says it was queued for the phone. `compose_message` opens the message for the user to send; Jarvis never sends it. The server forwards `client_actions` as a frame after the turn. If no voice client is connected (a Telegram turn), the reply tells the user the phone action was not run. `parse_domains` and the router prompt gain `phone`; `test_wiring.py` pins the new tool names and tags.
- **Push:** `voice/devices.py` stores the device's FCM token (sent in `hello`) and exposes `send_push(text)` using Firebase Admin. Tapping the notification opens a session that sends `speak`, and the server voices the text through Cartesia. The brief content and its scheduler are sub-project 5.
- **Failures:** an STT or TTS outage sends `error`, speaks a fixed fallback line if TTS is up, and never ends the session silently. Frame size and per-utterance length (60 s) are capped.

### App (`jarvis_app/`, Flutter, Android only)

- **Packages:** `porcupine_flutter` (wake word), `web_socket_channel`, `record` (PCM capture), a PCM player, `firebase_messaging`, `flutter_secure_storage`.
- **Pairing:** first launch takes the backend URL and a device token, pasted or scanned. The token is created by `python -m jarvis.voice.token`, which prints it once and stores only its hash (`--revoke` deletes a device). The token and the Picovoice AccessKey are the only secrets in the app; no API keys.
- **Wake word (FR-14):** Porcupine runs in an Android foreground service (type `microphone`, persistent notification). It needs a Picovoice AccessKey and a custom "Hey Jarvis" keyword file trained in the Picovoice console; both are created by the user and the keyword file ships as an app asset.
- **On wake (FR-15):** the service releases the mic to a session, turns the screen on through a full-screen-intent activity showing the listening overlay (falling back to a heads-up notification if that permission is missing), opens `/voice` and streams mic audio. Porcupine resumes when the session ends.
- **Session UI:** state, live transcript, and a Confirm/Cancel card when a `confirm_card` arrives; a tap sends `confirm`. Spoken yes/no is matched on the server only.
- **Barge-in (FR-16):** capture continues during playback with Android's acoustic echo canceller on the stream; detected speech sends `cancel` and the player flushes at once.
- **Session end:** socket closes after about 8 s of silence when nothing is pending; while a confirmation is pending it waits longer.
- **Phone actions (FR-18, FR-19):** `client_actions` run through intents: AlarmClock `ACTION_SET_ALARM` and `ACTION_SET_TIMER`, a `google.navigation:` Maps URI, and a pre-filled WhatsApp or SMS compose that the user sends. Contact names are resolved on the phone from its own contacts, so the server never sees them. An unknown action type is ignored and reported with an `error` frame.
- **Default assistant (FR-17):** the manifest declares the assistant role and activity; a power-button long-press starts the same session path without the wake word.
- **Offline:** if the socket cannot connect, the app says "Jarvis is offline" aloud (Android built-in TTS) and on screen.
- **Reboot:** Android 14 blocks starting a microphone foreground service from boot, so after a reboot the app posts a "tap to resume Jarvis" notification (PRD risk table).

### Config and data

- New `Settings` (prefix `JARVIS_`): `deepgram_api_key`, `cartesia_api_key`, `cartesia_voice_id`, `fcm_credentials_path` (Firebase service-account JSON, outside the repo).
- New table `devices(id, token_hash, fcm_token, created_at, last_seen)`, created in `init_schema`.
- Cloudflare Tunnel is run with `cloudflared` outside the app; setup steps go in `ACCEPTANCE.md`.

### Privacy and security

- Audio is streamed and never written to disk; the server keeps no recordings. Transcripts reach the LLM provider through OpenRouter with `data_collection: "deny"`, as typed text does.
- Deepgram and Cartesia are new processors of raw audio and reply text. The training and retention opt-outs on those accounts are the user's to set; listed in `ACCEPTANCE.md`.
- Transcripts are not added to the audit log. Tool calls and confirmations are audited as before. The known gap carries over: no 30-day transcript retention on the Postgres checkpointer, and it now includes voice turns.
- `/voice` is the only new public surface. TLS comes from the tunnel; the backend opens no public port. The token is stored hashed; a frame-size cap, the utterance cap and the one-session rule bound abuse.
- Spoken or ambient text can carry instructions like any text. The graph gate still requires a confirmation for every side effect, and ambient audio cannot satisfy a `send_draft` confirmation because that needs a tap.

## Testing

- **Server (pytest):** fakes `FakeSTT` (scripted transcripts) and `FakeTTS` (tagged chunks); no network.
  - Auth: bad or missing token closes before audio is read; a second connection replaces the first.
  - Turn flow: audio, transcript, graph, ordered TTS chunks; first audio is sent before the full reply is finished; a scripted-provider test asserts no blocking step sits between the final transcript and the first TTS chunk.
  - Barge-in: `cancel` stops TTS and cancels the graph task; an executed write stays audited.
  - Confirmations on the real graph and Postgres checkpointer: a bare "yes" resumes; "yes, and also delete everything" does not; a spoken yes on a `send_draft` card does nothing and says to tap; a stale interrupt id gets "Already handled"; an `after_untrusted` card is spoken with the warning; text during a pending confirmation is refused.
  - Phone tools: each produces a `client_actions` entry that is forwarded; with no voice client the reply says it was not run. `test_wiring.py` pins the new tools, their confirm tags and the `phone` domain.
  - Token CLI stores only the hash; revoke works. STT or TTS failure sends `error` and does not hang.
- **Flutter (`flutter test`, run by the user):** protocol codec, session state machine (idle, listening, thinking, speaking, confirming), intent builders per client action, barge-in flushes the player.
- **Test client:** `python -m jarvis.voice.client` (WAV in, audio out) exercises the server end to end without the phone.
- **Manual acceptance on the Pixel** (extends `ACCEPTANCE.md`): wake word with the screen off and false accepts over a day; screen wake and overlay; service survival and battery cost; echo and barge-in on the real speaker; AlarmClock, Maps, WhatsApp and SMS intents; default-assistant long-press; FCM tap-to-play; reboot notification; p50 and p95 latency against the PRD targets; PRD scenarios 1, 2, 6 and 9 by voice; a spoken yes does not send an email.

## Open items

- Fast-tier model for voice, to be chosen by measuring scenarios 1, 2, 6 and 9.
- Morning brief auto-play versus tap-to-play is decided in sub-project 5; this sub-project builds tap-to-play only.
- Echo-cancellation quality on the Pixel speaker and mic can only be tuned on the device.
- The 8 s silence timeout and the 60 s utterance cap are initial values.
- Real Deepgram and Cartesia usage and cost to be checked after the first week against the PRD's under-€40 monthly total.
- PRD scenario 7 remains unclarified (carried over).
- Transcript retention remains a separate follow-up.
