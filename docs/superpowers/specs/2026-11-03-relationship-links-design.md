# Relationship links between people

Status: built 2026-11-03. Plan: `docs/superpowers/plans/2026-11-03-relationship-links.md`. Builds on the knowledge graph
(`2026-10-28-knowledge-graph-design.md`), whose `fact.object_entity_id` column was never written until now.

## Problem

A relationship between two people was text on one of them ("Anna: friend_of = Bob"), and the 3D view guessed the line by
matching that text to a name. Renaming Bob, or having two Bobs, broke or confused the line, and Jarvis could not follow a
relationship ("who is Anna's husband?", "call my wife's brother").

## Model

- A **link** is a fact whose predicate is a link type and whose `object_entity_id` points at the other entity.
  `object_value` keeps the other person's name at write time, so forget's mention matching and text search still work.
- **Types** (`kg/predicates.py` `LINKS`): `spouse_of`, `partner_of`, `sibling_of`, `friend_of`, `colleague_of`,
  `relative_of` (symmetric) and `parent_of`, `manager_of` (directed). The aliases `child_of` and `reports_to` are stored as
  the swapped `parent_of` / `manager_of`. Anything else stays a `note`.
- A link is **stored once and read from both ends** (`FactsMixin.links_for`: "spouse of Raj" from Anna, "spouse of Anna"
  from Raj; "parent of Kabir" / "child of Mira"). Labels are neutral: Jarvis never turns "spouse" into "husband" or "wife"
  from a name. What the user said ("my wife") stays in `relation_to_user` and the source excerpt.
- **No duplicates:** a repeat, or a symmetric link said from the other side, confirms the existing link (adds a source).
  A partial unique index (`fact_one_active_link`) backs it. Two different Rajs are two links. A link to oneself is refused.
- `friend_of` is also still a plain text predicate; `relation_to_user` stays text (the graph view draws the kin edge).

## Writing

| Path | How |
|---|---|
| Telling Jarvis | `remember_facts` statements take `links: [{type, other, other_kind}]`, in the same transaction as the facts, so a new "Raj" is created once. Still confirmed, and refused after untrusted text. Several people with the other name: `ambiguous_other` and nothing saved. The confirm card marks a new person "(new)". |
| Learned inbox | The extractor (`extract.py`) may propose a link: predicate = the type, value = the other person's name. `filter_candidates` drops a link the graph already holds (from either side, direction-aware). `review.approve` writes it; several matches answer `409 ambiguous_other` with a choice; `other_entity_id` / `new_other` resolve it. |
| By hand in the app | `POST /graph/links {from_id, type, to_id | to_name}`: 'me' is the user, one name match is used, none creates the person, several answer `409 ambiguous_other`. A link cannot be edited, only removed (`DELETE /graph/facts/{id}`). |
| Existing data | At startup `kg/migrate.py link_existing` links `friend_of` text where the name is exactly one other person. Idempotent. |

## Reading

- **Prompt block** (`kg/block.py`): the people the message names show their links ("spouse of Raj Mehta; parent of
  Rohan"), and the people they are linked to (one hop, at most 4) get a line with their own facts. This makes "where does
  Priya's brother live?" answerable without a tool call. A third query is added only when the message names someone. Only
  links whose best source is `user_stated` are shown; names are neutralised.
- **`graph_lookup`** returns `links`. **Graph view** (`views.entity_list`): edges come from links first, so a rename or two
  Rajs cannot confuse them; text facts still draw by name where there is no link. **Person detail** lists "Relationships"
  apart from facts and offers "Add relationship".
- **Forgetting** a person removes their links (the foreign key cascades, and the forget plan already counts facts that
  point at them).

## Not built

- History ("married since 2019"): `valid_from` / `valid_to` could carry it.
- Groups or projects as their own nodes, and relationships with details of their own.
- More than one hop in the prompt, and merging duplicate people.

## Not verified

- The extractor and digest prompts with a real fast model proposing link candidates.
- The Flutter changes (`memory_screen.dart`, `learned_section.dart`) before CI.
