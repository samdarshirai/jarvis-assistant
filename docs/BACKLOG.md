# Jarvis — feature backlog

Ideas not yet built (added 2026-10-29). Each needs its own spec -> plan -> build cycle. Tick when merged, and add the tool/job to `jarvis_app/lib/capabilities.dart`.

## Cheap — existing APIs and scopes

- [ ] **Follow-up tracker** — flag sent mails with no reply after N days, nudge. Gmail read + `sent_to`. *(recommended next)*
- [ ] **Meeting prep card** — ~15 min before an event: attendees, recent threads, graph facts about them. Reuses `alerts.py` sweep + graph + `search_emails`. *(recommended next)*
- [ ] **Evening wrap-up** — done / slipped / tomorrow's first event. One more cron in `scheduler.py`. *(recommended next)*
- [ ] **Commitment extraction** — "I'll send it Friday" in sent mail -> offer a task. Mailwatch pattern on sent mail.
- [ ] **Weekly review** — overdue tasks, unanswered mail count, next week's load, flag weeks with no focus time.
- [ ] **Birthday / anniversary nudges** — dates from graph + Google Contacts. Needs `contacts.readonly`.
- [ ] **Recurring-chore memory** — "water plants every 3 days", adaptive reminders. Google Tasks lacks recurrence, needs a small store table.

## Medium — one new integration each

- [ ] **Bills and subscriptions** — parse receipt/invoice mail, track renewals, warn before trial converts. Untrusted input.
- [ ] **Package and travel tracking** — flight/hotel/delivery confirmations -> calendar blocks, check-in reminder, leave-for-airport alert. Gate like FR-11.
- [ ] **Weather in context** — "rain at 6, your run is 6:30". Needs a weather API.
- [ ] **Location reminders** — "buy milk when near the store". Android geofencing in the Flutter app.
- [ ] **Draft replies** — reply in owner's style, saved as Gmail draft, never sent. Needs `gmail.compose`, confirm-gated.
- [ ] **Smart scheduling** — "find 30 min with Ana next week": propose slots from freebusy, send invite.
- [ ] **Document Q&A** — Drive files and PDF attachments. Needs `drive.readonly` + chunked retrieval.

## Bigger — spec first

- [ ] **Habit and goal tracking** — streaks and weekly check-ins on top of the graph.
- [ ] **Health and finance summaries** — Google Fit, bank CSV exports. Sensitive, review per-tenant encryption first.
- [ ] **Shared household mode** — two tenants share a shopping list or calendar. Breaks per-user isolation invariant, careful design.
- [ ] **Smart home** — Home Assistant webhooks ("lights off").
- [ ] **Delegated actions** — reservations/bookings via browser automation. High risk and maintenance, only if real pull.
