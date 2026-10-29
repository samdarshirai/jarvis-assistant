# Memory screen as a 3D graph: design

## Intent

The Memory screen shows what Jarvis knows as a plain list of texts. The user wants a modern, graph-like UI instead: something you browse by looking at it, and occasionally use to fix a wrong fact. Usage is "mostly browsing, sometimes fixing wrong facts".

## What the user decided

- **Layout: radial, grouped by kind** (mockup option B). "Me" (the `self` entity) sits in the center. One hub per entity kind (People, Orgs, Places, ...) on a first ring. Entities sit on an outer ring, near their kind's hub.
- **Look: the app's own design.** Cocoa radial gradient background, amber gradient "Me", blush entity nodes, dark translucent hub pills/spheres, cream labels, `condensed` title font. Use `theme.dart` constants; do not introduce new colors.
- **3D:** shaded spheres, soft ground shadows, perspective: far nodes smaller and dimmer, near nodes larger and brighter. **Draggable and rotatable in 3D**: drag rotates the whole graph; node positions and sizes update with perspective.
- **Real relations.** Draw lines between entities that are actually related (e.g. Mom works at Acme), in addition to the structural lines Me -> hub -> entity. This needs a small server change (below).
- **Fixing facts:** tapping an entity highlights it and shows a bottom blush pill ("Mom · 12 facts, tap to open"). Tapping the pill (or the node again) opens the existing `_PersonDetail` screen, so correct / delete / forget work exactly as today. That flow is not redesigned.

## Goals

- Opening the screen shows a graph that feels like part of this app.
- Rotating by drag feels smooth (target 60 fps with ~100 nodes on a mid phone).
- Every entity currently reachable through the list stays reachable.
- Real entity-to-entity relations are visible.

## Non-goals

- No editing in the graph itself (no add/remove nodes, no drag-to-link).
- No redesign of `_PersonDetail`.
- No new Flutter package (builder's call, see below).
- No web/desktop-specific interaction work beyond what Flutter gives for free.

## Anti-goals (failure even if it "works")

- Looks like a generic graph widget, not this app.
- Becomes a hairball: unreadable with a realistic number of entities.
- Rotation hides or loses nodes so some entity cannot be tapped.
- Any entity becomes unreachable, including the "Forgotten" (suppressions) list and its Lift button.

## Constraints

- Flutter app in `jarvis_app/`; screen is `lib/memory_screen.dart` (`MemoryScreen`, `_PersonDetail`). Tests in `test/memory_screen_test.dart`; keep them passing and extend them.
- Server: `src/jarvis/kg/views.py` `entity_list`, exposed via `appapi.graph_entities` at `GET /graph/entities`. Today it returns `{"entities": [{id, name, kind, fact_count}]}` only.
- Relations in the DB: `fact` rows with `object_entity_id IS NOT NULL` and `status = 'active'` (subject -> predicate -> object entity). Ignore facts with `object_value` only.
- Privacy invariant from the knowledge-graph work: audit rows carry ids and counts only, never names or values. `GET /graph/entities` is a read and already returns names, so adding ids + predicate there is fine; do not log them.
- Branch context: `feat/graph-only-memory` is mid-change (old memory/people screens deleted, `memory_screen.dart` already modified). Build on the working tree as it is; do not revert those changes.
- Build and run the app with `--dart-define-from-file=jarvis.env.json`.

## Server change

`entity_list` returns an extra key:

```json
"relations": [{"from": 12, "to": 7, "predicate": "works_at"}]
```

- **Source (amended during planning):** nothing in the code writes `fact.object_entity_id`; facts only store text in `object_value`. So a relation is derived at read time: an active fact whose `object_value`, normalised with `kg.facts.norm`, equals the normalised `display_name` of another listed entity (not the subject itself). Example: Priya `works_at` "Acme" plus an entity named "Acme" gives `{from: priya, to: acme, predicate: "works_at"}`. Facts that also have `object_entity_id` set are covered by the same rule.
- Only include relations whose both ends are in the returned `entities` list.
- Deduplicate identical (from, to, predicate).
- Existing keys unchanged; old clients ignore the new key.
- Ceiling: exact name match only (no aliases, no fuzzy match). A written-but-unlinked name (e.g. "Acme Corp" vs entity "Acme") draws no line. Fine to widen later.
- Tests next to the existing `entity_list` test in `tests/test_appapi.py`.

## Client behavior

- **Scene:** custom painter (`CustomPainter`) with a hand-rolled projection: nodes have (x, y, z) on a flat ground plane; a rotation about the vertical axis from horizontal drag plus a limited pitch from vertical drag; perspective divide for position and size. Painter's algorithm (sort by depth) for draw order.
- **Layout:** Me at origin. Kind hubs evenly around ring 1. Entities of a kind spread in an arc on ring 2 around their hub's angle; if a kind has too many for one arc, add further rings. Node radius grows with `fact_count` (clamped).
- **Edges:** structural (Me-hub, hub-entity) faint; real relations slightly stronger, with the predicate shown as a small label when either end is selected. Lines depth-fade like nodes.
- **Interaction:** drag to rotate with a little inertia; pinch to zoom (clamped); tap selects the nearest projected node within a touch slop (front-most wins). Tapping empty space clears selection. Selected node gets an amber ring and its relations highlight.
- **Idle:** gentle slow auto-rotate until the first touch; off when the OS reduced-motion setting is on.
- **Labels:** always for Me and hubs; entity labels fade with depth and hide when far/small.
- **Lists kept:** a toggle (app-bar action) switches to the existing list view. It is also the accessibility path (give nodes a semantic order via the list, since a canvas has no semantics). The "Forgotten" section lives in the list view and is also reachable from the graph screen (button or bottom area, builder's call) so Lift stays reachable.
- **States:** loading spinner, error with `ErrorRetry`, and the existing empty message ("Nothing remembered yet...") when there are no entities.
- Refresh on return from `_PersonDetail`, as today (`_load()`).

## Calls I made (not yet confirmed by the user)

- Draw with `CustomPainter` and own projection math, no 3D / graph package. Reason: no new dependency, small scene, matches the app's code style.
- Rotation about the vertical axis plus limited pitch, not free tumbling, so "Me" and the ground plane stay readable.
- Auto-rotate on idle, off with reduced motion.
- Keep the list as a toggle instead of deleting it.
- Cap very large graphs by growing extra outer rings rather than clustering/collapsing. Revisit if real data is much larger than ~100 entities.
- Relations carry only `{from, to, predicate}`; no relation detail screen.

## Settled after the builder check (my calls, marked as such)

- **Kinds are a fixed set** (`kg/predicates.py` `KINDS`): `self`, `person`, `place`, `org`, `thing`. Hubs: People, Places, Orgs, Things; a hub only appears if it has entities. A hardcoded label map is fine; an unknown future kind falls back to its capitalised name.
- **No `self` row:** still draw a "Me" node at the center, not selectable (nothing to open). 0 entities: show the existing empty message instead of the graph.
- **Me is selectable** like any node and opens `_PersonDetail(self: true)` (forget stays blocked there, as today).
- **Relations:** no server cap; dedupe on (from, to, predicate) as specified. Client draws all relation lines faint, and highlights the selected node's relations with predicate labels. Multiple predicates between one pair show as one line, labels stacked, only when selected.
- **Direction:** lines are undirected; when a node is selected, its relation labels read from -> to (e.g. "works at Acme"). No arrowheads.
- **Forgotten:** a button on the graph screen opens a bottom sheet with the suppressions list and Lift buttons (same behavior as the list view section).
- **Default view:** graph on every open; the toggle is not remembered; no performance fallback.
- **Tests:** put projection, layout and hit-testing in pure functions (unit tested). List-based widget tests switch to the list view first.

## Left to the builder

Exact ring radii, sizes, shading gradients, auto-rotate speed, inertia constants, hit-test slop, node cap before rings stack, whether the Forgotten entry is a button or a footer, label-fade curve. Verify on a device or simulator with real data, including an account with 0, 1 and 100+ entities.
