# Conversation Extractor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a conversation ends, one background job reads only what the user said in it, asks the fast-tier LLM for facts worth remembering, and stores them as `fact_candidate` rows (with the user's own words as evidence). Nothing reaches the graph; project 4 reviews the candidates.

**Architecture:** A new table `fact_candidate` (in `KG_SCHEMA`) and two new `conversation` columns (`extracted_at`, `extract_attempts`). `kg/candidates.py` holds the DB side (`CandidateStore`: find due conversations, save results atomically, count failures, remembered-decisions lookup). `proactive/extract.py` holds the job: read human messages from the checkpoint, one LLM call, validate in code, filter against the graph, store. It runs on the existing APScheduler loop as a fourth job (`extract`), registered only when `JARVIS_EXTRACT_ENABLED` is true, and, unlike the other jobs, for users without Google.

**Tech Stack:** Python 3.11, FastAPI, APScheduler, LangGraph checkpointer, psycopg pool, pydantic, pytest (Postgres tests need `export TEST_DATABASE_URL=postgresql://localhost/jarvis_test`; without it every `pool` test skips). Python is `.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-11-03-conversation-extractor-design.md`

## Decisions (made while planning; change any before building)

1. **No `JARVIS_GRAPH_ENABLED` exists.** The spec says "behind a flag like `JARVIS_GRAPH_ENABLED`, requires the graph to be enabled", but the graph is always on (`UserRuntime.kg` is always built). Only `JARVIS_EXTRACT_ENABLED` (default false) is added; the job also requires `rt.kg` and `rt.graph`.
2. **No bulk backfill (spec non-goal), enforced three ways.** (a) The migration that adds `extracted_at` marks every existing conversation extracted. (b) `_import_legacy` inserts the legacy thread already marked extracted (it is the whole untrimmed pre-conversations history). (c) The job only looks at conversations with `last_at` in the last 7 days (`MAX_AGE`), so switching the flag on later does not process weeks of history.
3. **Resuming a conversation clears `extracted_at` and `extract_attempts`.** `ConversationStore.resume` reopens a closed conversation; without this, messages added after a resume are never read. Re-extraction is safe because step 6 drops anything already in the graph, already a candidate (any status) or already dismissed. "Once per conversation" therefore means once per closed period.
4. **Race guard on save.** Results are stored and `extracted_at` set in one transaction, and only if the conversation's `last_at` is unchanged since the messages were read. Otherwise the results are discarded and the conversation is retried next tick (not counted as a failed attempt).
5. **Key problems do not burn attempts.** A rejected key or empty account (`WatchedLLM.issue`) leaves the conversation untouched. The 3-attempt limit counts only other failures (timeout, provider error, unparseable output). Otherwise a bad key for 15 minutes would silently mark everything "extracted, zero candidates".
6. **Unparseable output is a failure (retry); parseable output with bad items drops those items.** The spec says both "invalid output yields zero candidates" and "parse failure retries"; this reconciles them. A valid `[]` is success with zero candidates.
7. **The "already saved by `remember_fact`" rule is covered by the active-duplicate rule.** Tool calls carry no conversation id and facts store `user:<uuid>` refs, so a direct per-conversation link does not exist. A fact the user saved in that conversation is an active fact with the same normalised value, so it is dropped as a duplicate. No schema change for it.
8. **The extractor must not see the `<graph>` block.** `rt.llm` is `GraphAwareProvider`, which adds the user's facts to the system prompt unless the call is tagged `router`. The job passes tag `no_graph` and `block.py` skips on `{"router", "no_graph"}`. (A graph-free prompt also means the model cannot "confirm" facts from the graph instead of from the user's words.)
9. **Project 4 hook, not a table.** "Records the count" = the rows themselves (`GROUP BY conversation_id`) plus an optional `on_candidates(conversation_id, count)` callback on the job, a no-op by default. Project 4 plugs its once-per-conversation push in there (`ProactiveStore.claim_alert(f"candidates:{conversation_id}")` already gives it idempotency).
10. **Forgetting also removes decided candidates that mention the person.** The spec deletes undecided candidates whose subject matches. A dismissed or approved row still holds the user's quote about someone they asked to forget, so `forget_entity` deletes every candidate whose subject matches the entity's names, and any whose quote or value mentions them (same matcher the forget code already uses). Suppression stops them coming back.

## Global Constraints

- Every query is scoped by `user_id`. `fact_candidate` and the new `conversation` queries never take a bare `id`.
- Never log message text, quotes, values or LLM output: log the exception type and counts only. Audit records counts only (`candidates`, `dropped`), like `forget_entity`.
- Input is `m.type == "human"` messages with `str` content from the checkpoint thread, nothing else. Skip messages over 1,000 characters. Use at most the newest 100 messages and 20,000 characters in total (cost cap; the legacy thread is untrimmed).
- Limits on LLM output, enforced in code: max 10 candidates; `subject` 1-100 chars, `value` 1-200 (the limits of `RememberFactArgs`), `quote` 8-300 chars; `predicate` in `PREDICATES`; `subject_kind` in `self|person|place|org|thing`; `relation` optional, max 50.
- `quote` must be a substring of one user message after `norm()` on both sides (casefold, collapsed whitespace), so a model that re-spaces or re-cases still passes but invents nothing.
- Subject words `me`, `i`, `self`, `myself` mean the self entity and are stored as subject `me`, kind `self`.
- At most 5 conversations per user per tick (the job shares the 120 s `USER_DEADLINE`); the rest wait for the next tick.
- No automatic approval, no graph write, no `pending` status (that word is already used by `kg/facts.py` `_add`; candidate statuses are `new|approved|dismissed|expired`).

## Review Focus

- Assistant, tool and email text never reaches the LLM input, including text that looks like a fact (Task 5 test with a mixed transcript).
- A message of exactly 1,000 characters is used; 1,001 is skipped (Task 5 boundary test).
- The migration marks existing conversations extracted and a second `init_schema` run changes nothing (Task 2 test).
- Two overlapping runs for one conversation store its candidates once (`UPDATE ... WHERE extracted_at IS NULL AND last_at = %s` returns no row for the loser; Task 4 test).
- A conversation resumed and extended while the LLM call is in flight is not marked extracted (Task 4 test).
- A rejected key leaves `extract_attempts` at 0; a timeout three times marks it extracted with zero candidates (Task 7 tests).
- A conversation that is open but idle for 30 min + 1 s is picked up; at exactly 30 min it is not (Task 4 test; same boundary as `ConversationStore.current`).
- `forget_entity` removes candidates for the forgotten name, including a dismissed one whose quote mentions it, and leaves unrelated ones (Task 3 test).
- User A's conversation never yields candidates for user B, and B's dismissed candidates do not filter A's (Task 8 test).
- `JARVIS_EXTRACT_ENABLED` unset: no job registered. No LLM key: no read, no mark, no call (Task 7 tests).

---

### Task 1: Settings

**Files:**
- Modify: `src/jarvis/config.py` (add `extract_enabled: bool = False`, `extract_poll_minutes: int = Field(default=5, ge=1)`)
- Modify: `.env.example` (under "Proactive features": `JARVIS_EXTRACT_ENABLED=false`, `JARVIS_EXTRACT_POLL_MINUTES=5`, one comment line)
- Test: `tests/test_config.py`
- Modify: `tests/test_proactive_scheduler.py` (`settings()` base gets `extract_enabled=False, extract_poll_minutes=5`, otherwise `build_scheduler` raises `AttributeError` on the new fields)

- [ ] **Step 1: Failing test:** defaults are `False` and `5`; `JARVIS_EXTRACT_ENABLED=true` parses; `JARVIS_EXTRACT_POLL_MINUTES=0` is rejected.
- [ ] **Step 2:** add the fields and env lines; run `tests/test_config.py`.
- [ ] **Step 3:** commit `feat(config): extractor flag`.

### Task 2: Schema

**Files:**
- Modify: `src/jarvis/db.py` (`conversation` gets `extracted_at TIMESTAMPTZ`, `extract_attempts INTEGER NOT NULL DEFAULT 0`; migration in `init_schema`)
- Modify: `src/jarvis/kg/schema.py` (`fact_candidate`)
- Modify: `src/jarvis/conversations.py` (`_import_legacy` inserts `extracted_at = now()`; `resume` sets `extracted_at = NULL, extract_attempts = 0`)
- Test: `tests/test_kg_schema.py`, `tests/test_conversations.py`

**Interfaces:**
- `fact_candidate`: `id SERIAL PK`, `{USER_REF}`, `conversation_id INTEGER NOT NULL`, `subject_name TEXT NOT NULL`, `subject_kind TEXT NOT NULL CHECK (subject_kind IN (<KINDS>))`, `relation TEXT`, `predicate TEXT NOT NULL`, `value TEXT NOT NULL`, `quote TEXT NOT NULL`, `subject_norm TEXT NOT NULL`, `value_norm TEXT NOT NULL`, `replaces_fact_id INTEGER`, `status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','approved','dismissed','expired'))`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `decided_at TIMESTAMPTZ`, `UNIQUE (user_id, id)`.
  - `FOREIGN KEY (user_id, conversation_id) REFERENCES conversation (user_id, id) ON DELETE CASCADE` (`conversation` already has `UNIQUE (user_id, id)`).
  - `FOREIGN KEY (user_id, replaces_fact_id) REFERENCES fact (user_id, id) ON DELETE SET NULL (replaces_fact_id)` (same pattern as `superseded_by`).
  - `CREATE INDEX fact_candidate_user_status ON fact_candidate (user_id, status, created_at DESC)`; `CREATE INDEX fact_candidate_seen ON fact_candidate (user_id, subject_norm, predicate, value_norm)`.
  - The two `_norm` columns are the dedup keys (computed with `kg.facts.norm`); the spec's column list is a minimum.
- Migration: `CREATE TABLE IF NOT EXISTS` will not add columns to an existing `conversation`. Follow the existing pattern in `init_schema` (it reads `information_schema.columns` into `have`): if `("conversation", "extracted_at")` is missing, `ALTER TABLE conversation ADD COLUMN extracted_at TIMESTAMPTZ`, then in the same transaction `UPDATE conversation SET extracted_at = now()` (decision 2a). `extract_attempts` is added the same way (no backfill needed). `SCHEMA` itself also declares both columns for fresh databases. The `UPDATE` must run only in the branch where the column was just added, so a re-run never touches rows.

- [ ] **Step 1: Failing tests:** (a) fresh schema has the columns and table; (b) old-shape database (create `conversation` without the columns, insert a row, run `init_schema`) ends with that row's `extracted_at` set and a second run leaves it unchanged; (c) `_import_legacy` row has `extracted_at` set; (d) `resume` on an extracted conversation clears it and the attempts; (e) deleting the conversation or user cascades to its candidates; (f) a candidate cannot point at another user's conversation (FK); (g) bad `status` / `subject_kind` is rejected.
- [ ] **Step 2:** implement; update `tests/conftest.py` only if the TRUNCATE list needs `fact_candidate` (it should not: `conversation ... CASCADE` reaches it; verify).
- [ ] **Step 3:** run `tests/test_kg_schema.py tests/test_conversations.py tests/test_db_guard.py`; commit `feat(db): fact_candidate and conversation.extracted_at`.

### Task 3: Suppression check and forget

**Files:**
- Modify: `src/jarvis/kg/store.py` (new `GraphStore.mentions_suppressed(*texts: str) -> bool`)
- Modify: `src/jarvis/kg/forget.py` (`_plan` also returns the names/terms it already computes; `forget_entity` deletes candidates in its transaction; `preview_forget` counts include `candidates`)
- Test: `tests/test_kg_forget.py`, `tests/test_kg_store.py`

**Interfaces:**
- `mentions_suppressed(*texts)`: True if any text mentions a suppressed name or hard key. Implementation: `forget.grams(text)` -> `forget.hmacs` -> one `SELECT 1 FROM suppression WHERE user_id = %s AND key_hmac = ANY(%s)`. This is what `forget.redact` does for excerpts, as a boolean, so "Anna's new job" is caught and not only an exact subject match (`is_suppressed("name", subject)` would miss it). No secret configured: False.
- `forget_entity`: in the existing transaction, `DELETE FROM fact_candidate WHERE user_id = %s AND (subject_norm = ANY(%s) OR <quote or value mentions a term>)`. The mention test reuses `forget._mentions(text, terms)` in Python over the user's candidates, like the existing mentions scan (note the same `ponytail` comment: fine for a personal graph). Count goes into the returned dict as `candidates_deleted` (int, so `graph_tools.forget_entity` already passes it through to the audit).

- [ ] **Step 1: Failing tests:** `mentions_suppressed` true for a suppressed name inside a longer sentence, false for an unrelated one, false with no suppressions, user-scoped. Forgetting "Anna" deletes her `new` and `dismissed` candidates and a candidate about "Raj" whose quote says "Anna", keeps an unrelated "Priya" candidate; `preview_forget` reports the count and deletes nothing.
- [ ] **Step 2:** implement; run `tests/test_kg_forget.py tests/test_kg_store.py tests/test_kg_tools.py`.
- [ ] **Step 3:** commit `feat(kg): forget removes extractor candidates`.

### Task 4: `CandidateStore`

**Files:**
- Create: `src/jarvis/kg/candidates.py`
- Test: `tests/test_kg_candidates.py`

**Interfaces:**
- `MAX_ATTEMPTS = 3`, `MAX_AGE = timedelta(days=7)`. `CandidateStore(pool, user_id)`.
- `due(now, limit) -> list[Due]` where `Due = (id, thread_id, last_at)`:
  `SELECT id, thread_id, last_at FROM conversation WHERE user_id = %s AND extracted_at IS NULL AND last_at > %s AND (closed_at IS NOT NULL OR last_at < %s) ORDER BY last_at LIMIT %s` with `now - MAX_AGE` and `now - IDLE` (import `IDLE` from `jarvis.conversations`). Open-and-idle covers lazily closed chat/Telegram conversations.
- `save(conv_id, seen_last_at, rows: list[dict], now) -> bool`: one transaction. First `UPDATE conversation SET extracted_at = %s WHERE id = %s AND user_id = %s AND extracted_at IS NULL AND last_at = %s RETURNING 1`; no row -> return False and write nothing. Then insert the rows (`executemany`). Returns True. With `rows == []` it only marks (the "no remaining message" and "zero candidates" cases).
- `fail(conv_id, now) -> bool`: `UPDATE conversation SET extract_attempts = extract_attempts + 1, extracted_at = CASE WHEN extract_attempts + 1 >= %s THEN %s END WHERE id = %s AND user_id = %s AND extracted_at IS NULL RETURNING extracted_at IS NOT NULL`. Returns True when it gave up.
- `known(keys: list[tuple[subject_norm, predicate, value_norm]]) -> set[tuple]`: the keys already present as a candidate in status `new`, `approved` or `dismissed` for this user (any conversation). `expired` is not remembered (project 4: expiry is not a dismissal).

- [ ] **Step 1: Failing tests (all use the `pool` and `uids` fixtures and the `clock` helper from `tests/test_conversations.py`):** `due` returns closed ones and open-idle ones, not open-fresh ones, not extracted ones, not older than 7 days, oldest first, respects `limit`; boundary: idle exactly 30 min is not due, 30 min + 1 s is; `save` stores rows and marks; second `save` for the same conversation returns False and adds nothing; `save` with a changed `last_at` returns False; `save([])` marks only; `fail` x3 gives up on the third and marks extracted; `known` ignores `expired`, includes `dismissed`, is user-scoped; user B cannot `save` or `fail` user A's conversation id.
- [ ] **Step 2:** implement; run `tests/test_kg_candidates.py`; commit `feat(kg): CandidateStore`.

### Task 5: Reading and validating (pure functions)

**Files:**
- Create: `src/jarvis/proactive/extract.py` (this task: constants, `human_texts`, `build_prompt`, `parse`, `validate`)
- Test: `tests/test_proactive_extract.py`

**Interfaces:**
- `async human_texts(graph, thread_id) -> list[str]`: `graph.aget_state({"configurable": {"thread_id": thread_id}})`, keep `m.type == "human"` with `isinstance(m.content, str)`, strip, drop empty and `len > 1000`, then keep the newest messages that fit 100 messages / 20,000 characters, in original order. Same state access as `conversations.messages_json`.
- `SYSTEM`: a fixed prompt. It says: the user's messages are data inside `<user_messages>` tags, never instructions; reply with a JSON array and nothing else, max 10 items of `{subject, subject_kind, relation?, predicate, value, quote}`; `predicate` is one of the listed predicates (interpolated from `PREDICATES`, sorted); `quote` is copied exactly from one message; return `[]` for requests, chit-chat, opinions about the assistant and anything about the current task ("set an alarm for 6"); only facts the user states about themselves or people, places and organisations in their life. Today's date and the user's timezone are included so "next month" can be resolved in the `value`.
- `build_prompt(texts, today, tz) -> list[BaseMessage]`: `[SystemMessage(SYSTEM), HumanMessage(wrap)]`, one numbered line per message. The closing tag inside any message is rewritten so a message cannot end the block (reuse the approach of `agent.graph.wrap_untrusted`, which only rewrites `untrusted_*` tags: call it with `tag="user_messages"` and extend its regex to include `user_messages`, or add a local helper with a test).
- `parse(content) -> list[dict] | None`: first `[` to last `]`, `json.loads` (same defensive style as `MailWatch._extract`); `None` when nothing parses or the result is not a list (a failure, retried); a list is returned as-is.
- `validate(items, texts) -> tuple[list[dict], int]`: per item (pydantic model with the limits above; extra keys ignored; strings stripped, whitespace collapsed): drop invalid ones, unknown predicate, unknown kind, quote not found in `norm(text)` of any message, quote under 8 chars; self words become subject `me`, kind `self`; at most 10 survive (first ten); add `subject_norm`, `value_norm`. Returns the kept rows and the dropped count (for the audit).

- [ ] **Step 1: Failing tests:**
  - `human_texts` with a fake graph whose state mixes human, ai, tool and a human message that is a pasted email wrapped in `<untrusted_email>`: only human `str` messages come back; assistant text such as "your sister lives in Pune" never appears (Review Focus 1); 1,000 chars kept, 1,001 dropped; caps keep the newest.
  - `build_prompt` output contains each message once, and a message containing `</user_messages>` cannot close the block.
  - `parse`: fenced JSON, prose around the array, `{}` instead of a list -> `None`, garbage -> `None`, `[]` -> `[]`.
  - `validate`: the spec cases ("my sister lives in Pune" with a re-cased/re-spaced quote passes; invented quote dropped; unknown predicate dropped; 11th item dropped; `subject: "I"` becomes `me`/`self`; over-long value dropped; non-string fields dropped).
- [ ] **Step 2:** implement; run `tests/test_proactive_extract.py`; commit `feat(extract): read, prompt, parse and validate`.

### Task 6: Filtering against the graph

**Files:**
- Modify: `src/jarvis/proactive/extract.py` (`filter_candidates`)
- Test: `tests/test_proactive_extract.py` (Postgres section)

**Interfaces:**
- `filter_candidates(kg: GraphStore, cands: CandidateStore, rows) -> tuple[list[dict], int]`, run via `asyncio.to_thread` by the job (it does blocking DB reads). In order, per row:
  1. `kg.mentions_suppressed(row["subject_name"], row["value"])` -> drop.
  2. Resolve the subject read-only: `me` -> `kg.self_entity()`; else `kg.find_by_name(subject_name)`; exactly one match is "known", zero or several are "unresolved" (kept; project 4 asks which one).
  3. Known subject: if an active fact of that predicate has the same `norm` value -> drop (this covers facts the user stated directly in this conversation, decision 7). If the predicate is in `SINGLE` and an active fact has a different value -> keep and set `replaces_fact_id`.
  4. `cands.known([...])` for all remaining keys in one query -> drop those present (dismissed, already approved, already waiting).
  5. Drop duplicates inside the batch (same key twice).
- Returns the rows to store (each with `replaces_fact_id`, `conversation_id` added by the job) and the dropped count.

- [ ] **Step 1: Failing tests:** existing active fact (same subject, predicate, differently cased/spaced value) -> none; a different value for `city` -> kept with `replaces_fact_id` = that fact id; a different value for a MULTI predicate -> kept without it; dismissed candidate -> not re-proposed; `expired` one -> proposed again; suppressed name (including inside the value) -> none; unknown subject -> kept; two entities share the name -> kept; a fact only visible to user B does not filter user A's candidates; duplicate rows in one batch -> one.
- [ ] **Step 2:** implement; run the file; commit `feat(extract): filter candidates against the graph`.

### Task 7: The job and scheduler wiring

**Files:**
- Modify: `src/jarvis/proactive/extract.py` (`run_once`)
- Modify: `src/jarvis/kg/block.py` (skip the graph block when the call is tagged `no_graph` as well as `router`)
- Modify: `src/jarvis/proactive/scheduler.py` (job `extract`, registration, no-Google users)
- Test: `tests/test_proactive_extract.py`, `tests/test_proactive_scheduler.py`, `tests/test_kg_block.py`

**Interfaces:**
- `async run_once(rt, llm, now, on_candidates=None)`:
  1. Return immediately if `llm is None`, `rt.graph is None` or `rt.kg is None` (no key: no read, no mark, nothing logged).
  2. `due = await to_thread(CandidateStore(...).due, now, MAX_PER_TICK=5)`.
  3. For each conversation: `texts = await human_texts(rt.graph, thread_id)`. No texts -> `save(conv, last_at, [])`. Otherwise `msg = await llm.get("fast").ainvoke(build_prompt(...), config={"tags": ["proactive", "no_graph"], "run_name": "fact_extract"})`; `parse` -> `None` is a failure; `validate`; `filter_candidates`; add `conversation_id`; `save(...)`.
  4. Exceptions per conversation (except `ReauthRequired`, which does not apply but must not be swallowed if raised): if `llm.issue` was set during the call, stop this user's run without counting an attempt (decision 5); otherwise `fail(conv_id, now)` and log `type(e).__name__` only. One conversation's failure never stops the next.
  5. After a successful `save` that stored rows: `rt.audit.record("proactive", "extract", result={"candidates": n, "dropped": d})` (counts only) and `on_candidates(conv_id, n)` when given (errors from the hook are logged by type and ignored).
- `per_user_jobs(s)` gets `extract(rt, notifier, llm, tz, now)` calling `run_once`. `build_scheduler` adds `IntervalTrigger(minutes=s.extract_poll_minutes), id="extract"` only `if s.extract_enabled`. `run_for_users` skips users without Google; add a `NO_GOOGLE = {"extract"}` set so `start_proactive.each` passes a `has_google` that is always true for `extract` (tests call `run_for_users` directly with `google=` to cover both).
- `block.py`: `if {"router", "no_graph"} & set(config.get("tags") or [])`.

- [ ] **Step 1: Failing tests (fake LLM = `FakeProvider({"fast": [AIMessage(...)]})` from `tests/fakes.py`; real `pool` fixtures; a fake graph object with `aget_state`):**
  - "my sister lives in Pune" -> one candidate whose quote is a substring of the message and whose conversation is marked extracted; "set an alarm for 6" with the model returning `[]` -> none, still marked; a conversation with no human text -> marked with no LLM call (assert the fake was not invoked).
  - Runs once: a second `run_once` makes no call and adds nothing; an open fresh conversation is not read; a closed conversation is.
  - Failure handling: model raises -> `extract_attempts == 1`, not extracted, retried next tick; third failure -> extracted, zero candidates; unparseable output counts the same; key rejected (`llm.issue` set) -> attempts stay 0; the other due conversations in the same tick are still processed after an ordinary failure.
  - Race: messages read, then the conversation is resumed (`last_at` moves) before the save -> nothing stored, still unextracted, no attempt counted.
  - No-ops: `llm=None`; `rt.graph=None`; `JARVIS_EXTRACT_ENABLED` false -> `build_scheduler` has no `extract` job and true -> interval trigger from `extract_poll_minutes`.
  - Scheduler: a user without Google is processed for `extract` and still skipped for `brief`; one user's failure does not stop the next user.
  - The call is tagged `no_graph` and `GraphAwareProvider.get(...)` does not add a block for it (`tests/test_kg_block.py`).
  - Logging: caplog contains no message text, quote or value for any failure path.
  - `on_candidates` receives `(conversation_id, n)` once, and not when nothing was stored.
- [ ] **Step 2:** implement; run `tests/test_proactive_extract.py tests/test_proactive_scheduler.py tests/test_kg_block.py`; commit `feat(extract): conversation-end extractor job`.

### Task 8: Isolation, end to end, docs

**Files:**
- Test: `tests/test_isolation.py` (add the extractor cases), `tests/test_proactive_extract.py` (end-to-end)
- Modify: `docs/HANDOFF.md` (new section), `docs/BACKLOG.md` if it lists the extractor

- [ ] **Step 1: Tests:**
  - Isolation: users A and B each have a closed conversation saying a fact; one `run_once` per user stores each user's candidate under the right `user_id` and `conversation_id`; B's dismissed candidate does not filter A's; `due` for A never returns B's conversation.
  - End to end through `start_proactive`/`run_for_users` with the real `CandidateStore`, a fake graph and `FakeProvider`: voice conversation closed -> candidate appears -> `forget_entity` on the subject removes it.
- [ ] **Step 2:** HANDOFF section: what the flag does, the 7-day window, the resume behaviour (decision 3), counts-only logging, and that project 4 uses `CandidateStore.known/rows` and the `on_candidates` hook. Note not verified live: a real model's JSON discipline on the fast tier (run once on the owner's account with a real conversation before enabling for friends).
- [ ] **Step 3:** run the full suite with `TEST_DATABASE_URL` set (`.venv/bin/python -m pytest -q`); commit `docs: conversation extractor handoff`.

## Not in this plan

- Wiring the push: the scheduler's `extract` job calls `run_extract(rt, llm, now)` WITHOUT `on_candidates`, so no notification is sent today. Project 4 must pass a hook there (the job already receives `notifier`) that claims `candidates:{conversation_id}` and applies its own 30-minute push limit.
- The review screen, badge, push and approve/dismiss endpoints (project 4: it adds `decide`/`list` methods to `CandidateStore` and the 30-day expiry job).
- Short follow-ups ("yes", "the other one"): ignored, as the spec's default says.
- Extraction from email, calendar, notes or web text; any bulk backfill.
