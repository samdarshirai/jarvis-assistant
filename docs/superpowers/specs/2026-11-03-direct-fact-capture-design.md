# Direct fact capture: one statement becomes several graph items (design)

Date: 2026-11-03. Project 2 of 4. Independent of project 1 (conversations); projects 3 and 4 build on the same store.

## Why

`remember_fact` (`src/jarvis/tools/graph_tools.py`) saves one fact per call: `subject`, one `predicate` from a small fixed list, one `value`, optional `relation`. "My wife's name is Anna and she is a teacher" needs the model to make several correct calls in the right order (create Anna with relation wife, then her job), and there is no predicate for a job, so it falls back to a free-text `note`. Relations are also not first-class: the 3D graph only draws an edge when a fact's value equals another entity's display name (`kg/views.py` `entity_list`), so a kin relation ("wife") does not connect Anna to the "me" node.

## Goals

- When the user states facts about a person, place or organisation, Jarvis creates the node (if new) and saves everything said in one tool call: relation to the user, and the other facts.
- A new person introduced with a kin word is connected to the "me" node in the graph view.
- The user's own statements are saved immediately with user trust (no approval step). The approval flow belongs to project 3/4 and applies to inferred facts only.
- Failure is atomic: a statement either saves completely or not at all, and no half-created node is left behind.

## Non-goals

- No inference from conversations (project 3). Only what the user explicitly asks Jarvis to remember.
- No new store tables and no change to supersession/trust rules.
- No UI changes beyond the existing graph screen showing the new node and edge.
- No editing/merging of existing entities by voice beyond what `remember_fact` already does.

## Anti-goals

- Saving anything because an email, web page or note suggested it (the existing `refuse_after_untrusted` guard on `remember_fact` must apply to the new tool too).
- Creating duplicate nodes for a person who already exists (same name, or the kin word already mapped).
- A silent wrong merge when two people share a name: ambiguity must be returned to the model so it asks the user.

## Constraints

- Predicates are validated against `kg/predicates.py`; `SINGLE` predicates keep one active value per subject (DB unique index).
- The `fact` table already has `object_entity_id`, but `add_fact` takes only a string value and the view derives edges by name match. Keep that approach in v1 (no schema change); populating `object_entity_id` is a possible later upgrade, not part of this project.
- Must keep the memory-domain prompt in `agent/domains.py` consistent: it lists the graph tools and predicates and says never to save what email or web text suggests.

## Design

1. **New predicate(s):** add `occupation` (SINGLE) to `PREDICATES`. Anything else stays a `note`. Adding more predicates is the builder's call only if a clear gap shows up in tests of realistic sentences; keep the list short.
2. **New tool `remember_facts`** (replaces the model's need to chain `remember_fact` calls; `remember_fact` stays for simple cases): input is a list (max 8) of statements, each `{subject, kind?, relation?, facts: [{predicate, value}]}`. One call per user message about the people in it. Subject resolution reuses the existing rules (`me`/self words, name lookup, ambiguity returns `{"error": "ambiguous", "candidates": [...]}` for that subject, new entity otherwise).
3. **Atomic write:** all statements in a call are written in one DB transaction through the store; any failure rolls back everything, including newly created entities.
4. **Kin edge to "me":** a `relation_to_user` fact must produce an edge between that entity and the self entity in `entity_list`/`/graph/entities` (today it produces none). Implementation is the builder's choice (derive in the view, or also store a name-matching fact on the self entity); the visible result is a line from "me" to Anna labelled with the relation.
5. **Prompt:** the memory domain prompt tells the model to put all facts from one message in one `remember_facts` call, and to use `relation` when introducing a person.
6. **Tool flags:** same as `remember_fact`: `refuse_after_untrusted=True`, no confirmation step.

## Left to the builder

- Exact tool and argument names, how the transaction is exposed by the store (`_write` helper in `kg/facts.py`), the cap on statements, whether `remember_fact` is kept or folded into the new tool.
- How the self-edge is represented (see Design 4), as long as `forget_entity` still removes the node and its edge.

## Verification

- Test: "my wife Anna is a teacher" (as tool args) creates Anna, `relation_to_user: wife`, `occupation: teacher`, and `/graph/entities` returns an edge between self and Anna.
- Test: two statements where the second fails validation leave no new entity.
- Test: same name twice returns `ambiguous`, nothing written.
- Test: refused after untrusted text in the window (same behaviour as `remember_fact`).
- Isolation tests keep passing.
