# Jarvis calendar reminders, invites, conflicts and shopping list — design (sub-project 6)

Date: 2026-10-03. Builds on sub-projects 1-5 (see `docs/HANDOFF.md`). PRD: "Jarvis — Personal AI Assistant PRD".

## Goal
Close the three functional requirements the sub-project 5 audit found unmet:
- **FR-7 (P1):** reminders and attendees on events; invites are sent only after the owner's confirmation.
- **FR-8 (P1):** creating or moving an event into a time the owner is already busy is flagged, with alternatives offered.
- **FR-13 (P1):** a dedicated Shopping task list.

Single user, same agent, same confirm gate. No new channel, no background job.

## Decisions (made with the owner)
1. **Conflicts warn, never block.** The check runs in plain code when the confirm card is built (`describe`), so the model cannot skip it and the warning is also spoken on voice. The owner can still confirm.
2. **Invitee addresses come from a Gmail header lookup** (`find_contact`), not from contacts and not only from explicit addresses. Guardrails: headers only (no message bodies), untrusted-wrapped, ambiguity asks, "never emailed by you" flag on the card, address validation, cap of 10 attendees.
3. **Shopping adds need no tap**, as a second narrow exception to the confirm gate, **conditional on no untrusted content** (email or web text read this turn or inside the history window). Completing items still needs a tap. Other task writes are unchanged.

## Structure

### Calendar (FR-7, FR-8) — `google/calendar.py`, `tools/calendar_tools.py`
- `create_event` and `update_event` accept `reminders: list[int] | None` (popup minutes before the start, each 0 to 40320, at most 5) and `attendees: list[str] | None` (at most 10, validated with `clean_recipients` from `google/gmail.py`). `update_event` attendees are add-only (existing attendees are kept); reminders replace the previous overrides. `sendUpdates="all"` is passed to Google only when attendees are present in the call, which can only happen after the Confirm tap.
- `CalendarClient.conflicts(start, end, exclude_id=None) -> list[dict]`: events from `list_for_proactive(start, end)` that are timed, not declined, busy, not `exclude_id`, and strictly overlap `[start, end)`.
- `CalendarClient` writes build the same body as today plus `reminders` (`useDefault: false`, popup overrides) and `attendees` (`[{"email": a}]`).
- `describe_create` / `describe_update` append lines, in this order, only when relevant: `Reminders: 1 day before, 1 hour before`; `Invites emailed to: a@x.com, b@y.com`; `Warning: conflicts with 'Standup' Tue 09:00-09:30. Free instead: <slot>, <slot>, <slot>`; `(could not check for conflicts)` when the lookup fails. Alternatives are the next 3 slots of the same duration, searched forward 7 days from the requested start between 08:00 and 20:00 in the configured time zone, using the existing `free_slots`.
- The conflict check runs only when a start or end is set or changed; when only the start of an update changes, the existing event's duration gives the end; a recurring series is checked at its first occurrence only (documented limit); `scope="all"` updates cannot change times, so they are never checked.

### Invitee lookup (FR-7) — `google/gmail.py`, `tools/gmail_tools.py`
- `GmailClient.find_contacts(name, limit=15) -> list[dict]`: one Gmail search `from:"name" OR to:"name" OR cc:"name"`, up to 15 messages fetched with metadata only (headers From, To, Cc and label ids; no body, subject or snippet is read or returned). Addresses are parsed with `email.utils.getaddresses`, kept when the display name or the local part contains the query (case-insensitive), grouped by address, and returned as at most 5 candidates `{name, address, you_emailed, seen}` where `name` is clipped to 60 characters with whitespace collapsed, `you_emailed` is true when the address is in To or Cc of a message carrying the `SENT` label, and `seen` is the message count. Order: `you_emailed` first, then `seen`.
- Tool `find_contact(name)` in the **gmail** domain, `needs_confirm=False`, `untrusted=True` (wrapped as `<untrusted_email>`, redacted in the audit log, and any write proposed afterwards carries the third-party-content warning). It lives in the gmail domain so voice keeps the strong model for it (voice fast tier excludes gmail and research). The router sends invite requests through gmail then calendar; the router prompt gets one hint line for this.
- Calendar domain prompt rule: never invent or guess an address; use only addresses typed by the owner or returned by `find_contact`; if the lookup returns none or several, ask which.
- Confirm card: every invitee address is listed (part of `describe`). The card does not trust `find_contact`'s output for the "never emailed" flag: `describe` calls `GmailClient.sent_to(address) -> bool` (one `in:sent to:address` search, `maxResults=1`, headers only) for each invitee and appends `(never emailed by you)` when it is false, or `(could not check)` when the lookup fails. `register_calendar_tools(registry, client, tz, sent_to=None)` receives that callable from `main.py`; with `sent_to=None` the flag is skipped.

### Shopping (FR-13) — `google/tasks.py`, `tools/task_tools.py`
- `TasksClient` methods accept an optional `tasklist` id (default `@default`, so every existing call is unchanged). `TasksClient.shopping_list_id()` finds a list titled `Shopping` (case-insensitive) or creates it, and caches the id on the client.
- New tools in the tasks domain: `add_shopping_items(items: list[str])` (1 to 20 items, each whitespace-collapsed and at most 100 characters; duplicates of open items are skipped case-insensitively; returns `{added, skipped}`; `needs_confirm=False`, `confirm_after_untrusted=True`), `list_shopping()` (read-only), `complete_shopping_item(task_id)` (confirm, with a `describe` naming the item). Removal is completion; there is no delete tool. `list_tasks` never returns Shopping items (separate list).
- Router prompt and tasks domain prompt mention shopping so "add milk and eggs" routes to the tasks domain.

### Graph (gate) — `tools/registry.py`, `agent/graph.py`
- `Tool.confirm_after_untrusted: bool = False`. In the gate, a call is "pending confirmation" when `registry.needs_confirm(name)` is true OR the tool has `confirm_after_untrusted` and `state.get("read_untrusted") or untrusted_in_window(state["messages"])`. The `tools` node uses the same predicate so approval state and execution agree. Nothing else about the gate changes (mixed steps, stale taps, voice confirm rules).
- A shopping add proposed in the same step as `find_contact` is not gated: the model emitted both before seeing the result, so the result cannot have influenced it; on the following step it is gated.

## Safety
- Confirm gate unchanged for calendar and invites. Invites go out only after the tap; the card (and the raw tool arguments the gate always shows) lists every address.
- Recipients cannot be steered by email body text: `find_contact` reads headers only, is wrapped untrusted, and the card flags unknown addresses. Residual risk, accepted by the owner: a look-alike display name or address in a header can still be proposed; the owner sees the address and the third-party warning before confirming.
- The shopping-add exception is conditional (see Decisions) and audited like any tool call with confirmation `not_required`.
- Conflict warnings never block and never write; failures of the check are shown, not hidden.
- Audit, retention, one lock on thread `owner`, voice confirm rules: unchanged.

## Errors
- Invalid attendee address, more than 10 attendees, bad reminder minutes, empty or oversized shopping items: rejected before any Google call with a plain error to the model; nothing is sent or written.
- Google failure on the conflict lookup: the card says so. Google failure on the write: plain error to the model.
- `ReauthRequired` follows the existing handling.

## Testing (pytest; fakes extend `tests/fakes.py` where needed)
- Conflict selection and alternatives (overlap vs back-to-back, declined/free/all-day, excluded id, window and 7-day bounds, DST day).
- Card text for each combination (reminders, invitees, conflict, never-emailed, lookup failure).
- `CalendarClient` bodies: reminder overrides, attendees, `sendUpdates` only with attendees, existing calls unchanged.
- `find_contacts` parsing and ranking, headers-only (assert no body/subject fetched), hostile display name clipped, grouping and `you_emailed`.
- Shopping: list find-or-create and caching, dedupe, caps, default list untouched, `list_tasks` excludes Shopping.
- Gate: `confirm_after_untrusted` ungated when clean, gated after an untrusted read in the window or this turn, same-step mixed call, existing gate tests unchanged.
- Router/domain prompt text pins (shopping routes to tasks; invites route gmail then calendar).

## Acceptance (manual, `ACCEPTANCE.md` rows 57+)
Conflict on create shows warning and alternatives; moving into a conflict; reminder on create; invite with a known contact (card lists the address, invite arrives only after Confirm); unknown or ambiguous name makes Jarvis ask; never-emailed address flagged; shopping add with no tap; shopping add right after reading an email now gets a tap; completing a shopping item needs a tap. HANDOFF (layout, invariants, limits) and RUN_ON_PHONE updated.

## Out of scope
Removing attendees, RSVP handling, attendee free/busy, Google Contacts scope, Meet links, quantities on shopping items, other named lists, conflict checks beyond a series' first occurrence, a block-on-conflict mode.
