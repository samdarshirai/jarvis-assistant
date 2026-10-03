# Jarvis proactive features — design (sub-project 5)

Date: 2026-10-03. Builds on sub-projects 1-4 (see `docs/HANDOFF.md`). PRD: "Jarvis — Personal AI Assistant PRD".

## Goal
Jarvis helps without being asked: a weekday morning brief, calendar conflict alerts, "leave now" reminders, and automatic email-to-calendar for bookings. Single user, one backend, no new channel.

Covered requirements: FR-23 (morning brief, default 07:30, configurable), FR-24 (conflict alerts, leave-now reminders for events with a location), FR-11 automatic half (scenario 4), scenario 8 (brief as a push that plays on tap).

## Decisions (made with the owner)
1. **Email-to-calendar auto-creates the event** and notifies with an Undo button (PRD wording), after a duplicate check. This is the first write outside the confirm gate and is a deliberate, bounded exception (see Safety).
2. **Brief is push + Telegram, played on tap.** No auto-play.
3. **Leave-now uses a fixed lead time** (config, default 30 min). No maps API.
4. **Brief text:** read-only job; plain code gathers data, one tool-less LLM call writes the spoken paragraph, templated fallback on LLM failure. It does not run through the graph and does not touch the `owner` thread.
5. **Mail detection by polling** every 5 minutes (no Gmail Pub/Sub).
6. **Scheduler:** APScheduler 3.x `AsyncIOScheduler` started/stopped in `lifespan`, jobs registered at startup, no persistent jobstore (all durable state is in Postgres).

## Structure
New package `src/jarvis/proactive/`; none of it goes through the graph.
- `scheduler.py` — registers jobs: brief (cron mon-fri at `brief_time`, misfire grace 1 h: covers a paused/slept process; a restart after brief_time skips that day's brief (no catch-up)), mail watch (every `mail_poll_minutes`), calendar sweep (every 5 min; conflicts and leave-now share one calendar read). Jobs never raise: failures are logged and audited.
- `brief.py` — gather today's events, overdue tasks, unread important mail; summarise; deliver.
- `mailwatch.py` — poll, prefilter, extract, dedupe, write, notify.
- `alerts.py` — conflict detection and leave-now selection (pure functions plus a thin sweep).
- `notify.py` — `Notifier.telegram(text, buttons=None)` and `Notifier.push(text)`; push reuses `voice/push.py` and `Devices.fcm_tokens()`.
- Telegram channel gains one callback handler for Undo.

Delivery:
| Event | Telegram | Push |
|---|---|---|
| Morning brief | yes | yes (plays on tap) |
| Leave-now | yes | yes |
| Conflict alert | yes | no |
| Event added from email | yes (Undo button) | no |
| Google re-consent needed | yes, once per day | no |

## Postgres (new tables, created in `init_schema`)
- `mail_seen(message_id pk, outcome, event_id, at)`
- `alerts_sent(key pk, at)` — dedupe across sweeps and restarts
- `auto_events(event_id pk, message_id, at)` — Undo whitelist and daily cap
- `proactive_state(key pk, value)` — the mail-poll cursor (a Unix timestamp; first run starts from now).
Rows older than 90 days are purged with the audit purge.

## Config (pydantic-settings, `.env.example` updated)
`brief_enabled=true`, `brief_time="07:30"`, `leave_lead_minutes=30`, `mail_poll_minutes=5`, `auto_event_cap=5` (rolling 24 h). Time zone is the existing `timezone` setting.

## Flows

### Email auto-detect
1. Every poll, list inbox mail newer than the last check; skip any `message_id` in `mail_seen`. First run starts from "now" (no backfill).
2. **Prefilter** (plain code): keyword match on subject and snippet (no attachment check; flight, booking, reservation, appointment, invitation, itinerary, ticket). No match: record `skipped`, no LLM.
3. **Extract:** one fast-tier LLM call, no tools, body wrapped as `<untrusted_email>`. Output is JSON for a fixed schema (kind, title, start, end, location). Code validates: start in the future and within one year, end after start, title and location length-capped, kind in an allowed set. Invalid: record `invalid`, no write.
4. **Dedupe:** skip if the message id was processed; skip if the calendar already has an event whose time overlaps start ±2 h and whose title is similar or whose location matches; events Jarvis creates store the message id in private `extendedProperties`.
5. **Write:** plain code creates the event. Flights: reminders at 24 h (check-in) and about 3 h (leave for airport). Other kinds: 1 day and 1 hour. Refuse when `auto_event_cap` is reached within the rolling 24 h (record `capped`, notify once).
6. **Notify** on Telegram: "Added: <title>, <when>" with **Undo**. Record `mail_seen` and `auto_events`, and write an audit row.
7. **Undo** deletes only an event present in `auto_events` and removes its row. A stale or repeated tap answers "Already handled." Audited.

A message that fails (LLM error, bad data) is recorded as `error` and not retried; at most 20 new messages are examined per poll.

Audit trail: `auto_event_attempt` (before the insert), then `auto_event_created` on success, `auto_event_undone` on Undo, `mail_error` on failure. If a crash or timeout left an event created by this same message (whitelisted id, or a 409 on our own deterministic id), the next poll treats it as created: it records it, audits it and sends the Undo notice. A 409 on a foreign event stays a silent duplicate.

### Morning brief
Each source (events, tasks, mail) is fetched in its own try/except; a failed source becomes "X unavailable" in the text. One tool-less LLM call writes about 60-80 spoken words (scenario 2: under 20 s of speech); email subjects and snippets are wrapped untrusted. On LLM failure, a templated list is used. Send via Telegram and push.

### Alerts
- **Conflict:** timed, non-declined, non-all-day events in the next 48 h that overlap. Key = sorted event ids + starts.
- **Leave-now:** timed events with a location where `start - now <= leave_lead_minutes` and `start > now`. Key = event id + start. A 5-minute sweep means the alert can be up to 5 minutes later than the exact lead time.
- Both check `alerts_sent` first; a moved event gets a new key and may alert again.

### Errors
`ReauthRequired` from Google: one Telegram notice per day, then skip the job's Google work. Scheduler jobs catch everything; one failing job never stops the others.

## Safety
The confirm-gate invariants are unchanged for everything the agent does. This sub-project adds one exception, the automatic calendar write from email, with these limits:
- The LLM only extracts fields into a validated schema and never calls a tool or names a tool argument.
- Plain code performs the write. The event description is a fixed string plus the subject, never body text; no attendees, no invites, so nothing leaves the owner's own calendar.
- Dedupe, a per-day cap, an audit row for every create and Undo, and a visible notice with Undo.
- Residual risk, accepted: a malicious email can create a visible, undoable event on the owner's own calendar, up to the daily cap.
- Email text in the brief is untrusted-wrapped; the brief has no tools, so nothing in it can trigger a write.
- The scheduler does not use the `owner` thread or its lock; the Undo handler deletes directly and does not run a graph step.

## Testing (pytest; fakes added to `tests/fakes.py`)
- Pure logic: extraction validator, dedupe matcher, conflict and leave-now selection (window boundaries, all-day, declined, moved events), cap.
- Jobs with fake Google, LLM and Notifier: brief with each source failing and the LLM fallback; mail watch end to end (duplicate email, injection text naming a tool, over-cap, Undo of an unlisted event refused); alert dedupe across two sweeps.
- Postgres tables via the existing `TEST_DATABASE_URL` tests.
- Scheduler wiring: jobs and triggers registered (weekday cron, intervals), no real-time waiting.
- Telegram: Undo callback including a stale tap.

## Acceptance (manual, `ACCEPTANCE.md` rows 49+)
Test brief at a near-future time reaching Telegram and the phone and playing on tap; a self-sent flight-confirmation email creating one event with reminders; the same email again creating nothing; Undo; a deliberate overlap giving a conflict alert; a location event giving leave-now. `RUN_ON_PHONE.md` and `HANDOFF.md` updated.

## Dependencies
`apscheduler` 3.x. No new Google scopes (`gmail.readonly` and the Calendar scope suffice).

## Out of scope
Maps-based travel time, Gmail push (Pub/Sub), brief auto-play, quiet hours, multiple calendars, auto-creating tasks from email, 30-day transcript retention (separate project), PRD scenario 7 clarification.
