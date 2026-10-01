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
