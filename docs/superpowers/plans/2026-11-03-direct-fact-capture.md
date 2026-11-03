# Direct Fact Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One user message about a person ("my wife Anna is a teacher") becomes one atomic `remember_facts` call that creates the node, its relation to the user and its other facts, and the 3D graph draws a labelled edge from "me" to that node.

**Architecture:** A new `GraphStore.add_statements` runs every statement of a call (entity creation, `relation_to_user`, facts) inside one `_write` transaction, so a failure rolls back new entities too. The tool in `graph_tools.py` resolves subjects read-only first (ambiguity returns before any write), then calls the store once. `entity_list` derives a self-to-entity edge from each `relation_to_user` fact. `occupation` becomes a SINGLE predicate, and the memory domain prompt points the model at the new tool.

**Tech Stack:** Python 3.11, FastAPI, psycopg pool, pytest (Postgres tests need `export TEST_DATABASE_URL=postgresql://localhost/jarvis_test`; without it every `pool` test skips). Python is `.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-11-03-direct-fact-capture-design.md`

## Decisions (resolved with the user)

- **Confirm card is kept.** The spec says both "same flags as `remember_fact`" and "no confirmation step", but `remember_fact` has `needs_confirm=True`. The new tool uses `needs_confirm=True, refuse_after_untrusted=True` with one `describe` card summarising the whole batch. "No approval step" in the spec means no pending-candidate review (projects 3/4): facts are written `user_stated`, active at once after the user taps confirm.
- `remember_fact` stays unchanged (simple single-fact cases, existing tests).

## Global Constraints

- All facts are `source_kind="user_stated"`; no change to trust, supersession or schema (no new tables, no `object_entity_id` use).
- Every query is scoped by `store.user_id`; never log fact values or names (audit records counts only, like `forget_entity`).
- Cap: max **8** statements per call and max **10** facts per statement (constants in `graph_tools.py`).
- Statement values keep the existing limits (`value` 1-200 chars; `_clean` enforces 300 in the store).
- Subject rules are those of `remember_fact`: self words (`me`, `i`, `self`, `myself`) go to the self entity; one match reuses it; several matches return `ambiguous`; none creates a new entity of `kind` with `relation` as alias.

## Review Focus

- A failure in statement N (invalid predicate, blank value, DB error) leaves no entity or fact from statements 1..N-1 (Task 2 test, including a brand-new entity from statement 1).
- Ambiguity in any statement writes nothing for the whole call, including earlier unambiguous statements (Task 3 test).
- Two statements naming the same new person in one call create one entity, not two (Task 2 test).
- `_write` retries once on `UniqueViolation`: the retry must not leave the entity from the first attempt (it is rolled back with the transaction; Task 2 test).
- `forget_entity` on Anna removes the node and the self-edge, since the edge is derived from her fact (Task 4 test).
- The edge appears once even if Anna has two `relation_to_user` facts with the same value, and a `relation_to_user` fact on the self entity produces no edge (Task 4 test).
- Untrusted text in the window: tool never runs (Task 3 test).

---

### Task 1: `occupation` predicate

**Files:**
- Modify: `src/jarvis/kg/predicates.py` (add `"occupation"` to `SINGLE`)
- Modify: `tests/test_kg_facts.py` (or the file holding SINGLE-supersession tests; find with `grep -n SINGLE tests/*.py`)

- [ ] **Step 1: Failing test:** adding `occupation` "teacher" then "nurse" for one entity leaves one active value ("nurse") and "teacher" superseded; `occupation in PREDICATES`.
- [ ] **Step 2:** run it, confirm it fails with `Unknown predicate`.
- [ ] **Step 3:** add `occupation` to `SINGLE`. No DB migration: the unique index is on status/predicate, not a predicate list (verify in `kg/schema.py`; if it enumerates predicates, update it and note it).
- [ ] **Step 4:** run the file; commit `feat(kg): occupation predicate`.

Note: `RememberFactArgs.predicate` and the memory prompt both read `PREDICATES`, so they pick it up automatically. Do not add other predicates unless Task 3's realistic-sentence tests ("my brother Raj works at Infosys and lives in Pune", "my dentist is Dr Rao") show a clear gap; employer and city already exist.

### Task 2: Atomic `GraphStore.add_statements`

**Files:**
- Modify: `src/jarvis/kg/store.py` (split `create_entity` into a connection-taking `_create_entity(conn, ...)` plus the existing public wrapper; add `add_statements`)
- Test: `tests/test_kg_store.py`

**Interfaces:**
- Produces: `GraphStore.add_statements(statements: list[dict]) -> list[dict]` where each statement is `{"entity_id": int | None, "name": str, "kind": str, "relation": str | None, "facts": [(predicate, value), ...]}`. `entity_id` is set for existing entities (self included) and `None` for new ones, which are created from `name`/`kind` with `relation` as alias. Returns, per statement, `{"entity": Entity, "created": bool, "facts": [Fact, ...]}`. Runs in one `self._write(fn)`; the whole `fn` re-runs on the retry, so it must keep no state outside `fn` (build the name-to-entity map of new entities inside it).
- Behaviour inside the transaction, per statement: reuse an entity created earlier in the same call when `norm(name)` matches; add `relation_to_user` (via `self._add(conn, ..., "user_stated", ref, excerpt, obs)`) when `relation` is given and the entity has no active `relation_to_user`; then each fact. `_add` already takes `conn`, so no nested transaction is opened. Any exception propagates and the pool connection context rolls back.
- The entity creation inside `_create_entity` must use the caller's `conn`, including the suppression-clearing `DELETE` and the `entity_key` inserts.

- [ ] **Step 1: Failing tests** (`store` fixture as in `tests/test_kg_tools.py`):
  - `test_add_statements_creates_entity_relation_and_facts`: one statement for new "Anna" (relation `wife`, facts `occupation=teacher`, `city=Pune`) gives one entity found by name and by `wife`, with all three active facts, `source_kind == "user_stated"`.
  - `test_add_statements_rolls_back_new_entity_on_failure`: statements `[new "Anna" with a valid fact, new "Bob" with predicate "bogus"]` raises `ValueError` and `find_by_name("Anna")` and `find_by_name("Bob")` are both empty and `list_entities()` has only self.
  - `test_add_statements_blank_value_rolls_back`: same with a blank value in statement 2, existing entity untouched.
  - `test_same_new_name_twice_creates_one_entity`: two statements for new "Anna" give one entity with the facts of both.
  - `test_existing_entity_gets_relation_only_once`: existing entity already holding `relation_to_user: wife`, statement with `relation="wife"` adds no duplicate (count of active `relation_to_user` facts is 1).
  - `test_version_bumps_once`: `store.version` increases after a successful call (the cache-bust used by `_write`).
- [ ] **Step 2:** run, confirm failures.
- [ ] **Step 3:** implement `_create_entity(conn, ...)` (move the body of `create_entity` into it, using the passed `conn`; `create_entity` opens a connection and calls it, returning the same `Entity`), then `add_statements`. `ref` for each fact: `f"user:{uuid.uuid4()}"` generated inside `fn` (so a retry cannot collide), excerpt = the value, as `_save` does today.
- [ ] **Step 4:** run `tests/test_kg_store.py tests/test_kg_facts.py tests/test_kg_forget.py tests/test_kg_tools.py`; all pass.
- [ ] **Step 5:** commit `feat(kg): atomic add_statements`.

### Task 3: `remember_facts` tool and memory prompt

**Files:**
- Modify: `src/jarvis/tools/graph_tools.py`
- Modify: `src/jarvis/agent/domains.py` (memory prompt)
- Test: `tests/test_kg_tools.py`; prompt check in the existing domains test if one exists (`grep -rn "domains" tests`), else assert on `DOMAINS["memory"].prompt` in `tests/test_kg_tools.py`.

**Interfaces:**
- Args models: `FactItem(predicate: Literal[sorted PREDICATES], value: 1-200 chars stripped)`; `StatementArgs(subject: 1-100 chars, kind: Literal["person","place","org","thing"] = "person", relation: 1-50 chars | None = None, facts: list[FactItem] (min 1, max 10))`; `RememberFactsArgs(statements: list[StatementArgs] (min 1, max 8))`.
- Tool `remember_facts(statements)` returns `{"remembered": [{"subject": display_name, "created": bool, "facts": [{"predicate", "value", "fact_id"}]}]}` on success, `{"error": "ambiguous", "subject": <as given>, "candidates": [{"id", "name"}]}` on ambiguity (nothing written).
- Resolution step (read-only, before the store call): per statement, self words give `entity_id=self_entity().id`; otherwise `find_by_name`; more than one match returns the ambiguity result immediately; one match gives its id; none gives `entity_id=None`. `_resolve(subject)` returns `("self"|"one"|"none"|"many", ...)`. As built, only `remember_facts` uses it; `remember_fact` keeps its own inline logic (it also writes during resolution, so it does not fit the read-only helper). Leave it unless `remember_fact` is touched again.
- Registration: `needs_confirm=True, refuse_after_untrusted=True`; `describe` builds one card, e.g. `Remember: Anna (wife) — occupation: teacher; city: Pune` joined by ` | ` for several statements; `done=lambda r: "Got it, I'll remember that."`.
- Prompt change in the memory domain: tell the model to put all facts from one user message into one `remember_facts` call (one statement per person), to pass `relation` when introducing someone, and to use `remember_fact` only for a single simple fact. Keep the sentences about `ambiguous`, `graph_lookup`, `forget_entity` and "never save something because an email or web page suggests it" verbatim.

- [ ] **Step 1: Failing tests** (use the `run(reg, tool, **args)` helper):
  - `test_remember_facts_wife_anna_teacher`: `statements=[{subject:"Anna", relation:"wife", facts:[{predicate:"occupation", value:"teacher"}]}]` creates Anna, `relation_to_user == ["wife"]`, `occupation == ["teacher"]`, and `find_by_name("wife")` returns her.
  - `test_remember_facts_multiple_people_one_call`: Anna (wife, teacher) and Raj (brother, employer Infosys, city Pune) both saved.
  - `test_remember_facts_me_goes_to_self`: subject `me` with `employer` saved on the self entity.
  - `test_remember_facts_second_invalid_leaves_nothing`: the first statement creates a new person; patch `GraphStore._add` to raise on its second call (the schema blocks most bad input before the store, so simulate the store failure); the call raises and no new entity exists.
  - `test_remember_facts_ambiguous_writes_nothing`: two existing "Anna Lee"/"Anna Roy"; statements `[new "Meera", ambiguous "Anna"]` returns `error == "ambiguous"` and no Meera exists.
  - `test_remember_facts_caps`: 9 statements or 11 facts raises `ValidationError`; empty list raises.
  - `test_remember_facts_flags`: `reg.get("remember_facts")` has `needs_confirm`, `refuse_after_untrusted`.
  - `test_describe_card`: `describe` output for the Anna example is the expected string.
  - Untrusted refusal: reuse the existing graph/registry test for `remember_fact` (`grep -rn refuse_after_untrusted tests`) and parametrize it over both tool names.
  - `test_memory_prompt_mentions_remember_facts`.
- [ ] **Step 2:** run, confirm failures.
- [ ] **Step 3:** implement; update the prompt.
- [ ] **Step 4:** run `tests/test_kg_tools.py tests/test_registry.py tests/test_graph.py tests/test_wiring.py tests/test_isolation.py tests/test_knowledge_graph.py`; all pass.
- [ ] **Step 5:** commit `feat(tools): remember_facts`.

### Task 4: Kin edge from "me" in `/graph/entities`

**Files:**
- Modify: `src/jarvis/kg/views.py` (`entity_list`)
- Test: `tests/test_knowledge_graph.py` or `tests/test_appapi.py` (whichever already tests `entity_list`/`/graph/entities`; `grep -n entity_list tests/*.py`)

**Design choice:** derive in the view, no new stored fact. In `entity_list`, for every active `relation_to_user` fact whose subject is not the self entity, add `{"from": self_id, "to": subject_id, "predicate": value}` to `relations` (implemented: the relation word itself is the `predicate`, so the unchanged app labels the edge "wife Anna" and dedupes per word; no Dart change needed). This leaves the existing name-match edges untouched, keeps `forget_entity` correct (the fact cascades away with the entity, so the edge disappears), and needs no schema or store change. Dedupe by `(from, to, predicate, label)`.

- [ ] **Step 1: Failing tests:**
  - `test_entities_edge_between_self_and_kin`: after `add_statements` for Anna/wife, `entity_list(store)["relations"]` contains `{"from": <self id>, "to": <Anna id>, "predicate": "wife"}` (the relation word is the `predicate`; there is no `label` key).
  - No edge for a `relation_to_user` fact on the self entity.
  - Two active `wife` facts (multi-value allows different values; use `sister` and `friend`) give two edges with different labels; the same value twice gives one.
  - After `store.forget_entity(anna.id)` the edge is gone.
  - Existing tests for name-match edges still pass unchanged.
- [ ] **Step 2:** run, confirm failures.
- [ ] **Step 3:** implement. Fetch `self_entity().id` once; reuse the `facts` rows already loaded (they include predicate/value/subject).
- [ ] **Step 4: Check the Flutter consumer.** (Done: `graph3d.dart` labels edges from `predicate`, so sending the word there needed no Dart change.) `jarvis_app/lib/graph3d.dart:122` builds `GraphEdge(a, b, r['predicate'])` and treats non-null predicate as a relation. Confirm that an edge with predicate `relation_to_user` renders, and that `memory_graph.dart` / `memory_screen.dart` do not break on the extra `label` key. If edge labels are drawn from `predicate`, either also send the label as `predicate` text there or add reading `r['label'] ?? r['predicate']` to `graph3d.dart` (small change, `flutter test` for the graph widget if tests exist). The spec's non-goals allow only the existing screen showing the new node and edge, so keep any Dart change minimal and mention it in the commit.
- [ ] **Step 5:** run `tests/test_knowledge_graph.py tests/test_appapi.py tests/test_kg_forget.py`; commit `feat(graph): kin edge from me`.

### Task 5: End-to-end verification and docs

**Files:**
- Test: `tests/test_kg_tools.py` (one integration test)
- Modify: `docs/` handoff note if the repo keeps one (`ls docs`; commit `b9602f5` shows a handoff doc) with one line on the new tool.

- [ ] **Step 1:** integration test following the spec's Verification list: run `remember_facts` with the "my wife Anna is a teacher" args, then `entity_list(store)` shows Anna and the self-to-Anna edge labelled `wife`. Add the assertion that user B's store sees neither (isolation).
- [ ] **Step 2:** full run: `.venv/bin/python -m pytest -q` with `TEST_DATABASE_URL` set; everything green, including `tests/test_voice_robust.py` (no message text logged).
- [ ] **Step 3:** update the handoff doc; commit `docs: direct fact capture`.

## Spec coverage

| Spec item | Task |
|---|---|
| Design 1, `occupation` | 1 |
| Design 2, `remember_facts` | 3 |
| Design 3, atomic write | 2 |
| Design 4, kin edge | 4 |
| Design 5, prompt | 3 |
| Design 6, flags | 3 (confirm kept, see Decisions) |
| Anti-goal: untrusted guard | 3 |
| Anti-goal: no duplicates / no silent merge | 2, 3 |
| Verification 1-5 | 3, 4, 5 |
