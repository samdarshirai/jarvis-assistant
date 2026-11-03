# Relationship Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Relationships between people become real, stored links in the knowledge graph instead of text, so Jarvis can
follow them.

- **Today:** "Anna is married to Raj" is the text "Raj" on Anna, and the 3D view guesses a line by matching names.
- **After:** it is a link from Anna to Raj's own entry. Lines survive renames and duplicate names, and Jarvis can answer
  "who is Anna's husband?" and resolve "call my wife's brother" (you → wife → her brother).

**Architecture:**
- **Storage:** reuse the `fact.object_entity_id` column, which exists but is never written. A link is a fact whose
  predicate is a link type and whose `object_entity_id` points at the other entity. `object_value` keeps the other
  person's name at write time, for search, excerpts and forget's mention matching.
- **One stored direction:** each link is stored once and shown in both directions through an inverse-label table.
- **Write paths:** `remember_facts`, Learned-inbox approval and a new `POST /graph/links` route.
- **Read paths:** the prompt block, `graph_lookup`, `/graph/entities` relations, entity detail and the app.

**Tech Stack:** Python 3.11, psycopg, FastAPI, pytest (`export TEST_DATABASE_URL=postgresql://localhost/jarvis_test`),
Flutter.

**Builds on:** the knowledge graph (`2026-10-28-knowledge-graph-design.md`), the extractor and Learned inbox
(`2026-11-03-conversation-extractor-design.md`, `2026-11-03-candidate-review-design.md`), and memory tiers
(`2026-11-03-memory-tiers-design.md`).

## Decisions (made while planning; change any before building)

1. **A small, fixed vocabulary of link types.**
   - Symmetric: `spouse_of`, `partner_of`, `sibling_of`, `friend_of`, `colleague_of`, `relative_of`.
   - Directed: `parent_of` (inverse "child of") and `manager_of` (inverse "reports to").
   - "Child of" and "reports to" are written by swapping subject and object, so each pair has one stored form.
   - A closed list lets the code show the reverse direction and the extractor validate its output. Anything else
     ("neighbour", "landlord") stays a `note`.
2. **Neutral labels, never guessed gender.** Links show as "spouse of", "sibling of", "parent of". Jarvis never turns
   "spouse" into "husband" or "wife" from a name. The user's own word ("wife", "brother") is kept where they said it:
   - in the existing `relation_to_user` fact (to "me");
   - in the link's source excerpt.
3. **`friend_of` becomes a link type.** Its existing text facts keep working: they are converted where the name matches
   exactly one person (decision 6), and the rest stay as text and are still drawn by today's name matching.
4. **`relation_to_user` also becomes a link to "Me".** Existing and new kin facts ("Anna: relation_to_user = sister")
   get `object_entity_id` = the self entity. The kin word stays the label, so "my sister" still matches Anna by alias
   as today.
5. **Links are always multi-valued and keyed on the other entity, not the name.** Two links to two different Rajs are
   two facts. A repeated "Anna is married to Raj" confirms the existing link (adds a source) instead of duplicating it.
   A partial unique index enforces one active link per (subject, type, object).
6. **One-time conversion at startup, idempotent, exact matches only.** For each active `friend_of` fact whose value
   resolves to exactly one entity (`GraphStore.find_by_name`), set `object_entity_id`. For `relation_to_user`, set it to
   the self entity. Ambiguous or unknown names are left alone. Same pattern as `kg/migrate.py`.
7. **One hop in the prompt, capped.** When the message names Anna, the memory block lists Anna's links ("spouse of Raj
   Mehta; sibling of Rohan") and also pulls in the linked people's own facts, up to 4 extra entities. That is what makes
   "my wife's brother" resolvable without a tool call. No deeper traversal in the hot path.
8. **Ambiguous other person: ask, never guess.** A tool call or approval whose other name matches several entities
   writes nothing. The tool returns `ambiguous_other` with candidates, and the app's Learned inbox shows a "Which Raj?"
   picker, like the existing ambiguous-subject flow. A new name creates the person (`origin="user"` for user
   statements and approvals; extraction never creates entities on its own).
9. **Manual add in the app is included, but small.** On a person's detail: "Add relationship" (pick a type, pick a person
   or type a new name), and delete on each link. It reuses `DELETE /graph/facts/{id}`.
10. **Same trust rules as every graph write.** Links from tools are `user_stated`, confirmed, and refused after untrusted
    text. Extracted links go only through the Learned inbox. `forget_entity` already deletes facts whose
    `object_entity_id` points at the forgotten person (FK `ON DELETE CASCADE`, and `_plan` already counts them).

## Global Constraints

- Every query is scoped by `user_id`; cross-table references use the composite `(user_id, id)` foreign keys.
- Never log names or values: types and counts only.
- Every stored string shown in the prompt goes through `kg/block.py` `_s` (neutralised).
- No schema change breaks old rows. Facts with `object_entity_id IS NULL` behave exactly as today.

---

### Task 1: Vocabulary, schema and store

**Files:** `src/jarvis/kg/predicates.py`, `src/jarvis/kg/schema.py`, `src/jarvis/kg/facts.py`, `src/jarvis/kg/store.py`;
tests `tests/test_kg_links.py` (new).

- [ ] `predicates.py`: add the link vocabulary and its inverse labels, and make `PREDICATES` include `LINKS`:

  ```python
  LINKS = {"spouse_of": ("spouse of", "spouse of"), "partner_of": ("partner of", "partner of"),
           "sibling_of": ("sibling of", "sibling of"), "friend_of": ("friend of", "friend of"),
           "colleague_of": ("colleague of", "colleague of"), "relative_of": ("relative of", "relative of"),
           "parent_of": ("parent of", "child of"), "manager_of": ("manager of", "reports to")}  # (forward, inverse)
  ALIASES = {"child_of": "parent_of", "reports_to": "manager_of"}  # written by swapping subject and object
  SYMMETRIC = frozenset(k for k, (f, b) in LINKS.items() if f == b)
  ```

  `friend_of` moves from `MULTI` to `LINKS`, and `relation_to_user` stays in `MULTI` (decision 4).
- [ ] `schema.py`, appended to `KG_SCHEMA`:
  - `CREATE INDEX IF NOT EXISTS fact_object_idx ON fact (user_id, object_entity_id) WHERE object_entity_id IS NOT NULL`
  - `CREATE UNIQUE INDEX IF NOT EXISTS fact_one_active_link ON fact (user_id, subject_id, predicate, object_entity_id) WHERE status = 'active' AND object_entity_id IS NOT NULL`
- [ ] `facts.py`: pull the source upsert and `fact_source` link out of `_add` into `_source(conn, kind, ref, excerpt, obs)`
  and `_link_source(conn, fid, src)`, reused by the new path. Add
  `_add_link(conn, subject_id, predicate, object_id, kind, ref, excerpt, obs) -> Fact`:
  - Resolve `ALIASES` by swapping subject and object.
  - For a symmetric type, also look for an existing active link in the reverse direction.
  - Refuse a link to oneself (`ValueError`).
  - Lock the subject (same `FOR NO KEY UPDATE` as `_add`), confirm an existing active link, or insert a new active fact
    with `object_entity_id` and `object_value` set to the object's current display name.
- [ ] `facts.py`: add `links_for(entity_id) -> list[Link]`, where `Link = (fact_id, label, other_id, other_name, kind,
  direction)`. It returns both directions, with labels from `LINKS` (forward when the entity is the subject, inverse
  when it is the object) and the other entity's current `display_name`.
- [ ] `store.py`: add `add_link(subject_id, predicate, object_id, *, source_kind, ref=None, excerpt="")`, wrapping `_write`.
  `add_statements` accepts an optional `links: [(predicate, other_entity_id | None, other_name, other_kind)]` per
  statement, creating unknown others in the same transaction (reusing the `made` dict, so "Raj" mentioned twice is one
  entity), and returns `links` per statement.
- [ ] `add_fact` with a `LINKS` predicate and no object raises `ValueError` ("use add_link"), so text-only links are not
  created by new code.
- [ ] **Tests:**
  - A symmetric link is stored once and seen from both people. "Child of" and "reports to" store the swapped form.
  - A repeat confirms rather than duplicates. A self-link is refused.
  - Two Rajs give two links. A renamed person shows the new name.
  - Forgetting the other person removes the link (cascade).
  - User isolation: user B cannot link to user A's entity (FK plus `user_id`).

### Task 2: Startup conversion

**Files:** `src/jarvis/kg/migrate.py` (add `link_existing(pool)`), `src/jarvis/main.py` (call it after `migrate_memories`);
tests `tests/test_kg_migrate.py`.

- [ ] For each user, set `object_entity_id = self` on `relation_to_user` facts where it is NULL. For each active
  `friend_of` fact with a NULL object, resolve the value with the same normalised name/alias lookup as `find_by_name`.
  Exactly one match sets it; otherwise leave it.
- [ ] Idempotent: a second run changes nothing. It only updates rows with `object_entity_id IS NULL`, so it never touches
  a link written by Task 1. It runs in one transaction per user and logs counts only.
- [ ] **Tests:** converted, ambiguous left alone, unknown left alone, second run is a no-op, and other users are untouched.

### Task 3: Telling Jarvis (tools and prompt)

**Files:** `src/jarvis/tools/graph_tools.py`, `src/jarvis/agent/domains.py` (memory prompt); tests `tests/test_kg_tools.py`.

- [ ] `StatementArgs` gets
  `links: list[LinkItem] = []` with `LinkItem{type: Literal[LINKS | ALIASES], other: str (1..100), other_kind = "person"}`.
  "Anna is married to Raj" becomes `{subject: "Anna", links: [{type: "spouse_of", other: "Raj"}]}`.
- [ ] `remember_facts`:
  - Resolve each `other` with the existing `_resolve`. A match on several people returns
    `{"error": "ambiguous_other", "other": ..., "candidates": [...]}` and writes nothing. A new name is created with the
    statement.
  - `remember_facts_describe` shows "Anna — spouse of Raj (new)", so the confirm card says who will be created.
- [ ] `graph_lookup` adds `"links": [{"relation": "spouse of", "other": "Raj Mehta", "other_id": 7}]`.
- [ ] Memory-domain prompt: use `links` for relationships between two people, keep `relation` for how someone relates to
  the user, use the neutral types, and never infer gender. Two-hop requests ("my wife's brother") follow the memory
  block's links, or `graph_lookup` on the first person.
- [ ] **Tests:** one call writes facts and links in one transaction; an ambiguous other writes nothing; a new other is
  created; refused after untrusted text (existing gate); the lookup shows both directions.

### Task 4: Reading links (prompt block, graph view, detail)

**Files:** `src/jarvis/kg/block.py`, `src/jarvis/kg/views.py`; tests `tests/test_kg_block.py`, `tests/test_appapi.py`.

- [ ] `block_for`:
  - After the name match, add up to 4 entities linked to the matched ones (one hop, decision 7).
  - Each person line gets their links: `- Anna Roy (person): city: Pune [told …]; spouse of Raj Mehta; sibling of Rohan`.
  - Linked-only people get their own short line.
  - All of it stays inside the existing `CAP` and the people section's place in the priority order.
- [ ] `views.entity_list` relations:
  - Edges from links first (`object_entity_id`, labelled forward), then today's name-matching fallback only for facts
    without an object, so unconverted rows still draw.
  - Kin edges come from the converted `relation_to_user` links.
- [ ] `views.entity_detail` adds `"links"` from `links_for`, with the fact id for delete.
- [ ] **Tests:**
  - "Call my wife's brother": wife matched by her kin alias, and the brother's line is present through her link.
  - Hop cap of 4. Neutralised names.
  - Graph edges survive a rename. Two Rajs draw two distinct edges.

### Task 5: Learned inbox (extracted links)

**Files:** `src/jarvis/proactive/extract.py` (prompts and validation), `src/jarvis/kg/review.py`,
`src/jarvis/appapi.py`, `src/jarvis/main.py`; tests `tests/test_proactive_extract.py`, `tests/test_memory_digest.py`,
`tests/test_kg_review.py`.

- [ ] The extractor prompts (`SYSTEM`, `DIGEST_SYSTEM`) list the link types: for a link, `predicate` is the type and
  `value` is the other person's name. `validate` accepts `LINKS` and `ALIASES`, and `filter_candidates` drops links
  already active between the same two resolved people.
- [ ] `review.approve`:
  - For a link candidate, resolve the value like the subject.
  - Several matches raise `Ambiguous(..., which="other")`, which becomes `409 {"error": "ambiguous_other", "choice": [...]}`
    and writes nothing.
  - An `other_entity_id` / `new_other` edit picks or creates. The link is then written with `_add_link` in the same
    transaction.
- [ ] `CandidateApproveIn` gets `other_entity_id` and `new_other`.
- [ ] **Tests:** "my sister Anna is married to Raj" yields a link candidate; approval links them; an ambiguous Raj asks;
  a dismissed link is not proposed again.

### Task 6: Add relationships in the app

**Files:** `src/jarvis/main.py`, `src/jarvis/appapi.py`, `src/jarvis/kg/views.py` (`link_create`);
`jarvis_app/lib/memory_screen.dart` (`_PersonDetail`), `jarvis_app/lib/learned_section.dart`;
tests `tests/test_appapi.py`, `tests/test_isolation.py` (`ROUTES`), `jarvis_app/test/memory_screen_test.dart`,
`jarvis_app/test/learned_test.dart`.

- [ ] Route `POST /graph/links {from_id, type, to_id | to_name}`:
  - `user_stated`, audited with ids only; `to_name` creates the person.
  - 409 `ambiguous_other` when a name matches several entities; 404 for foreign or unknown ids; 400 for a self-link or
    an unknown type.
  - Add the route to `test_isolation.ROUTES`.
- [ ] `_PersonDetail`: a "Relationships" section lists links ("spouse of Raj Mehta" → tap opens Raj; delete through
  `DELETE /graph/facts/{id}`). "Add relationship" opens a sheet: type dropdown (neutral labels, including "child of"
  and "reports to"), then a person picker over `/graph/entities` with a "New person" option.
- [ ] `learned_section.dart`: `learnedSentence` renders link candidates ("Anna: spouse of Raj"). The approve flow handles
  `ambiguous_other` with the existing picker, sending `other_entity_id` / `new_other`.
- [ ] **Tests:** the route (create, ambiguous, foreign, self-link); widget tests for listing, adding, deleting and the
  "Which Raj?" picker.

### Task 7: Docs

- [ ] Spec `docs/superpowers/specs/2026-11-03-relationship-links-design.md`: the decisions above.
- [ ] `HANDOFF.md`: replace "`object_entity_id` is never written" and "relations by exact name match" with the link model.
- [ ] `ACCEPTANCE.md` rows: tell Jarvis "Anna is married to Raj" and see the link in Memory and the 3D line; "who is
  Anna's spouse?"; "call my wife's brother"; rename Raj and the line stays; forget Raj and the link goes; approve an
  extracted link; add and delete a link in the app.

## Verification

- **Backend:** run `pytest -q` against the local Postgres. All new tests listed per task must pass, plus the existing
  graph, forget, isolation, extractor and memory suites.
- **App:** `flutter analyze` and `flutter test` in CI (no SDK in the dev container).
- **End to end** with the owner's key, on the deployed backend:
  1. Say "my wife Priya's brother is Rohan, he lives in Delhi".
  2. Confirm. Memory shows Priya → sibling of Rohan, and the 3D view draws Me–Priya ("wife") and Priya–Rohan ("sibling of").
  3. "Where does my wife's brother live?" answers Delhi with no tool call (one-hop block).
  4. Rename Rohan in the app. The line and the answer still work.
  5. Forget Rohan. The link and line are gone.

## Out of scope

- Relationship history ("married since 2019", "used to work with"). The `valid_from` / `valid_to` columns could carry it
  later.
- Groups or teams as nodes ("Project Phoenix"). Relationships as their own nodes was the option not chosen.
- Two or more hops in the prompt.
- Merging duplicate people.
