# Candidate review: the "Learned" inbox, badge and notification (design)

Date: 2026-11-03. Project 4 of 4. Depends on project 3 (the `fact_candidate` table and the extractor). Uses the existing Memory graph screen and the existing push channel.

## Why

The extractor (project 3) proposes facts it noticed in conversations. They only help if the user sees them, understands where they came from, and can accept or reject them in a couple of taps. Nothing inferred may enter the graph without that approval, and the user must notice that something is waiting without having to go looking.

## Goals

- A "Learned" section at the top of the Memory graph screen lists undecided candidates, newest first, grouped by the conversation they came from.
- Each candidate reads as a plain sentence ("Anna, your wife: works as a teacher"), shows the user's own words as evidence, and offers Approve, Edit and Dismiss. A group has Approve all and Dismiss all.
- Approving writes the fact into the graph with the right trust and provenance; dismissing records the decision so it is never proposed again.
- It is apparent when something needs action: a count badge on the memory pill, and one push notification per conversation that produced candidates.

## Non-goals

- No review of facts the user states directly (those are saved immediately, project 2).
- No new graph visualisation; the existing graph/list toggle stays.
- No editing of arbitrary graph facts here (the existing People/entity screens do that).
- No per-candidate push or repeated nagging.

## Anti-goals

- Anything entering the graph before an explicit tap (including "Approve all" without a visible count of what it applies to).
- A push that interrupts or speaks. The existing push channel plays text aloud on tap (`jarvis_app/lib/push.dart` `speak` payload); this one must only open the screen.
- Showing candidate text from untrusted sources: candidates only ever come from the user's own messages, but the quote is still rendered as plain text.

## Constraints

- Approve/dismiss are direct, audited owner actions, like the other app endpoints in `appapi.py` (the tap is the confirmation).
- Approving a candidate whose subject is ambiguous (several entities share the name) must ask which one, or "new person"; it never guesses.
- Existing flags: the whole feature is hidden when the graph or the extractor is disabled.

## Design

### Server

- `GET /graph/candidates`: undecided candidates grouped by conversation (conversation title and time, then items with subject, relation, predicate, value, quote, `replaces` current value if any). Also `GET /graph/candidates/count` (cheap, for the badge).
- `POST /graph/candidates/{id}/approve` with optional edits (subject, value, relation): resolves or creates the entity, adds the relation fact if needed, then `add_fact(..., source_kind="user_stated", ref="conversation:<id>", excerpt=<quote>)`; marks the candidate `approved`. All in one transaction. Trust is user-level because every candidate comes from the user's own words and the user confirmed it (author's call).
- `POST /graph/candidates/{id}/dismiss`: marks `dismissed`; dismissed values are remembered so the extractor skips them.
- `POST /graph/candidates/bulk` with `{ids, decision}` for the group buttons.
- Candidates with status `new` older than 30 days are marked `expired` by the scheduler (not remembered as dismissed).

### Notification

- When the extractor stores candidates for a conversation, the server sends one push to the user's registered devices via the existing sender (`src/jarvis/voice/push.py` / `proactive/notify.py`) with a distinct payload type (e.g. `{type: "candidates", count}`), title like "Jarvis learned 3 things", no `speak` field. At most one such push per 30 minutes (further candidates only raise the badge). Respect any quiet-hours handling the proactive notifier already has.
- `push.dart`: handle the new payload type by opening the Memory screen scrolled to "Learned" on tap; if the app is foreground, only refresh the badge.

### App (Flutter)

- `memory_screen.dart` / `memory_graph.dart`: a collapsible "Learned" section above the graph/list, empty state hidden. Cards with Approve / Edit (inline sheet) / Dismiss; group header with Approve all / Dismiss all and a confirmation line that states the count.
- The memory pill (home) shows a count badge when there are undecided candidates; the count refreshes on app resume, after a voice or chat session ends, and on the push.
- After approving, the graph refreshes and the new node/edge is visible; a short snackbar offers Undo for 5 seconds (undo = retract the created fact; the entity stays if it existed before).
- Accessible labels for the buttons and badge; no colour-only state.

## Left to the builder

- Visual design within the existing cocoa/blush/amber theme, exact copy, the Edit sheet fields, how the badge is polled.
- Whether bulk operations are one endpoint or several, and the exact shape of the group payload.

## Verification

- Server tests: approve creates entity/fact with provenance and marks the candidate approved; dismiss prevents re-proposal (with project 3's filter); ambiguous subject returns a choice and writes nothing; user A cannot read or decide user B's candidates; expiry job.
- Push test with a fake sender: one push per conversation, none within 30 minutes of the previous, payload has no `speak`.
- Widget tests: Learned section hidden at zero, visible with items, Approve/Dismiss call the right endpoints, badge count.
- Device acceptance rows (added to `ACCEPTANCE.md`): say a fact in a voice conversation, end it, receive the notification, open Learned, approve, see the node in the graph.
