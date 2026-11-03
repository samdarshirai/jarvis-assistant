# Conversation-end extractor: candidate facts from what the user said (design)

Date: 2026-11-03. Project 3 of 4. Depends on project 1 (conversations: a closed conversation is the unit of work). Project 4 reviews what this produces. Project 2 handles facts the user states directly.

## Why

Only what the user explicitly asks to remember reaches the knowledge graph. Facts that come up naturally in conversation ("my sister moves to Pune next month", "I switched to the Munich office") are lost. The user wants Jarvis to notice them, but not to write them silently: inferred facts are proposed as candidates and the user approves them (project 4).

## Goals

- When a conversation ends, one background job reads what the user said in it and stores zero or more candidate facts, each with the user's own words as evidence.
- Candidates use the graph's existing vocabulary (predicates, entity kinds) so approving one is a normal graph write.
- Candidates never include anything that came from third-party text.
- Cheap and quiet: one fast-tier LLM call per conversation, no latency added to any turn, no spoken or visible side effects.

## Non-goals

- No UI (project 4) and no notification (project 4 sends it; this project only records candidates).
- No extraction from email, calendar, notes or web text (those are separate planned ingestion paths with their own trust ranks).
- No automatic approval, ever.
- No re-extraction of old conversations in bulk (a one-off backfill is out of scope).

## Anti-goals

- A prompt-injection path: text written by others (pasted emails, web pages, tool results) turning into saved facts.
- Re-proposing facts the user dismissed, or facts already in the graph.
- Proposing facts about someone the user asked Jarvis to forget (the suppression list must be honoured).
- Logging message text or LLM output (counts and types only).

## Constraints

- Trigger: a conversation counts as ended when `closed_at` is set (voice) or it has been idle for more than 30 minutes (chat/Telegram, per project 1: closed lazily, so a periodic job must also catch idle ones). Runs on the existing proactive scheduler (`src/jarvis/proactive/scheduler.py`) or an equivalent loop; once per conversation, tracked by a new `extracted_at` column on `conversation`.
- Behind a flag like `JARVIS_GRAPH_ENABLED` (new flag `JARVIS_EXTRACT_ENABLED`, default off); requires the graph to be enabled.
- Uses the user's own LLM key through the runtime's `llm` provider, fast tier. If the user has no key, the job skips silently.
- The graph's `pending` fact status already means "lower-trust fact waiting behind a higher-trust active one" (`kg/facts.py` `_add`). Do not reuse it for candidates.

## Design

1. **Input:** the conversation's human messages only (from the checkpoint thread). Assistant replies, tool results, email/web/notes text are excluded. Messages longer than 1,000 characters are skipped as likely pasted text. Conversations with no remaining message are marked extracted with no LLM call.
2. **LLM call:** a fixed system prompt asks for a strict JSON list (max 10) of `{subject, subject_kind, relation?, predicate, value, quote}`. `predicate` must be one of the graph's predicates; `quote` must be a substring of one of the user's messages (checked in code; candidates with a non-matching quote are dropped). The prompt says to skip requests, chit-chat, opinions about the assistant, and anything about the current task ("set an alarm"). The output is parsed defensively; invalid output yields zero candidates.
3. **Filtering in code:** drop candidates that duplicate an active fact (same subject, predicate, normalised value); that match a previously dismissed candidate; whose subject or value is on the suppression list (`store.is_suppressed`); or that were already saved directly in that conversation by `remember_fact`/`remember_facts`. A candidate that would replace an existing active single-value fact is kept and marked `replaces_fact_id` so the review screen can show "currently: X".
4. **Storage:** new table `fact_candidate`: `id`, `user_id`, `conversation_id`, `subject_name`, `subject_kind`, `relation`, `predicate`, `value`, `quote`, `replaces_fact_id` (nullable), `status` (`new` | `approved` | `dismissed` | `expired`), `created_at`, `decided_at`. Every query is scoped by `user_id`. Forgetting an entity (`forget_entity`) also deletes undecided candidates whose subject matches its names.
5. **Output event:** after storing at least one candidate, the job records the count so project 4 can badge and notify (a simple "candidates created for conversation X" row/hook; the notification itself is project 4).
6. **Failure handling:** an LLM or parse failure leaves the conversation unmarked and retries on the next run, at most 3 attempts, then marks it extracted with zero candidates. Never raises into a user-facing path.

## Left to the builder

- Prompt wording, the JSON schema library, the scheduler interval, the exact dedup normalisation (reuse `kg/facts.py` `norm`).
- Whether short follow-ups ("yes", "the other one") need the preceding assistant message as context. Default in this spec: no, they are ignored.

## Verification

- Test with a fake LLM: a conversation with "my sister lives in Pune" yields one candidate with a quote that is a substring of the message; one with only "set an alarm for 6" yields none.
- Test: assistant/tool/email text containing a fact-like sentence never reaches the LLM input.
- Test: dismissed candidate is not re-proposed; suppressed name yields none; existing active fact yields none.
- Test: quote not found in user text is dropped; malformed JSON yields zero candidates and retries.
- Test: runs once per conversation; flag off or no LLM key makes it a no-op.
- Isolation: user A's conversation never produces candidates for user B.
