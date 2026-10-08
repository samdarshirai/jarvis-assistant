# Jarvis app dashboard and visual redesign

Date: 2026-10-08. Status: draft, awaiting owner review.

## Goal

The Pixel app opens to a polished home dashboard instead of a bare voice screen. It shows what Jarvis manages: today's calendar events, pending tasks, unread email, recent notes, the morning brief and the next alarm. Voice ("Talk") stays and becomes a pinned bottom bar.

## Decisions (from brainstorming)

- Read-only view. No editing or completing items from the app.
- Android/Pixel only. Same device-token auth as `/voice`.
- Look: dark, glassy. Near-black navy gradient, translucent rounded cards with a thin light border, one accent hue per card. Material 3 dark. Light mode is out of scope.
- Layout: scrolling cards plus a pinned bottom voice bar.
- Alarms: only the phone's next alarm, read from Android (`AlarmManager.getNextAlarmClock()`). No alarm list, no backend alarm log.
- Implementers MUST use the `frontend-design` skill for the visual work (theme, cards, voice bar), staying inside the decisions above.

## Backend

New `src/jarvis/dashboard.py`, route `GET /dashboard` wired in `create_app` (`src/jarvis/main.py`).

- Auth: `Authorization: Bearer <device token>`, verified with `Devices.verify` exactly like `/voice`. Missing or bad token: 401.
- Response JSON:
  - `events`: today's events (`CalendarClient.list_events`, local day, same shape as the brief).
  - `tasks`: pending tasks (`TasksClient.list_tasks`), each with `due` and an `overdue` flag.
  - `unread`: `{count, items: [{from, subject}]}` top 5 from `GmailClient.search_emails("is:unread", ...)`. `count` is the number of messages returned; a result set that hits the fetch cap is shown as "N+" by the app.
  - `notes`: 5 most recent (`NoteStore.recent`): id, title, first line of body.
  - `brief`: text built by `brief.render_facts` from the same gathered facts. No LLM call, so refresh is free. Speaking the real LLM brief stays on the existing tap path.
  - `reauth`: true if any Google source raised `ReauthRequired`.
- Each source runs in `asyncio.to_thread` and fails alone: a failed source is `null`, logged, and the rest still return. The endpoint returns 200 unless auth fails.
- Reuse `brief.gather` for events/tasks/mail where it fits; extend or add small helpers rather than duplicating Google calls. Unread uses its own query (`is:unread`, not the brief's important-only filter).
- Email subjects and senders are third-party text. The endpoint returns them as plain data for display; it never feeds them to the agent.

## Android: next alarm

A MethodChannel in the existing Kotlin code returns `AlarmManager.getNextAlarmClock()?.triggerTime` (epoch ms) or null. Dart wrapper in `phone.dart` style. Card text: "Next alarm 06:30 · Tomorrow", or "No alarm set". Channel failure is treated as "No alarm set".

## App

- `lib/theme.dart`: dark glass `ThemeData` and a `GlassCard` widget (BackdropFilter, translucent fill, hairline border, accent). No new packages.
- `lib/dashboard.dart`: `Dashboard` model (parsed with tolerant fromJson, null per section allowed), `DashboardService` (GET `/dashboard` with bearer token, derived from `Config.url`), and the card widgets: Brief, Calendar, Tasks, Mail, Notes, Alarm.
- `lib/ui.dart`: `SessionScreen` becomes the home: greeting and date, brief card with a Play button (starts a session via the existing `AppHost.startSession(speakText:)`/brief path), then the cards. Pull-to-refresh. Refresh on app resume and when a voice session ends (so a spoken "add task" shows up).
- Bottom voice bar: mic orb with glow and phase label (existing `Phase` labels). During a session it expands upward to show transcript, the untrusted-content warning and the Confirm/Cancel card. Confirm/cancel behaviour and warning text are unchanged.
- Pairing and capabilities screens use the new theme.
- `app.dart`: swap `ThemeData` for the new theme.

## Errors and states

- Fetch fails: keep last good data, show a small "offline" chip. First load fails: empty state with Retry.
- Section `null`: that card shows "unavailable". `reauth` true: a banner "Google needs re-consent".
- Empty lists: friendly empty text per card ("No events today").

## Testing

- Backend pytest: auth (401), full payload, one source failing, reauth flag, no LLM call.
- Flutter widget tests with a fake dashboard fetcher: loaded, partial failure, empty, offline-with-cache; bottom bar expands with a confirm card; existing session tests keep passing.
- Visual check and next-alarm behaviour verified by the owner on the Pixel (cannot be run here); add rows to `ACCEPTANCE.md`.

## Out of scope

Editing or completing items from the app, alarm list or backend alarm log, light theme, tab navigation, push-refresh of the dashboard, new Dart packages.
