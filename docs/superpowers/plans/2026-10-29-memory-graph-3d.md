# Memory Graph 3D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Memory screen's text list with a draggable, rotatable 3D radial graph in the app's cocoa/amber/blush look, with real entity-to-entity relations.

**Architecture:** Server adds a derived `relations` list to `GET /graph/entities`. The app gets a pure-Dart scene module (layout, projection, hit-test), a `CustomPainter` widget on top of it, and `MemoryScreen` hosts the graph by default with the old list as a toggle. `_PersonDetail` is untouched.

**Tech Stack:** Python (psycopg, pytest) for the server; Flutter/Dart (`CustomPainter`, `flutter_test`), no new packages.

**Spec:** `docs/superpowers/specs/2026-10-29-memory-graph-3d-design.md` (read it first, including "Settled after the builder check").

## Global Constraints

- No new Flutter package; draw with `CustomPainter` and own projection math.
- Colors only from `jarvis_app/lib/theme.dart` (`cocoa*`, `amber*`, `blush`, `cream`, `mutedText`, `darkPill`, `ink`); title font via `condensed(...)`.
- Kinds are fixed: `self`, `person`, `place`, `org`, `thing`. Hubs: People, Places, Orgs, Things; a hub appears only if it has entities; unknown kind falls back to its capitalised name.
- `_PersonDetail` and its forget/correct/delete flow are not changed. Me opens `_PersonDetail(self: true)`.
- Graph is the default view on every open; list toggle is not remembered; no performance fallback.
- Auto-rotate on idle, off when reduced motion is on (`MediaQuery.disableAnimationsOf`).
- Privacy: the audit log never carries names or values; do not add logging of entity names.
- Work on the current branch `feat/graph-only-memory`; the tree has unrelated uncommitted changes. Commit only the files each task names (`git add <paths>`), never `git add -A`.
- Flutter builds/runs use `--dart-define-from-file=jarvis.env.json`.

## Review Focus

- Zero entities: existing empty message, no graph, no crash.
- Only the self entity, or entities but no self row: graph still renders; a missing self shows a non-selectable "Me" at the center.
- Two entities with the same display name: relation derivation must not link an entity to itself or produce duplicate lines.
- A relation whose value names an entity that is not listed (e.g. forgotten): dropped, not an error.
- 100+ entities: layout stays finite (extra rings), taps still resolve to the front-most node.
- Pitch/zoom drags past the limits clamp, never flip or vanish the graph.
- Reduced-motion on: no ticker running (so `pumpAndSettle` and battery are fine).
- Overlapping nodes after rotation: tap selects the nearest-to-camera one.

---

### Task 1: Server derives `relations` in `entity_list`

**Files:**
- Modify: `src/jarvis/kg/views.py` (`entity_list`, lines 21-27)
- Test: `tests/test_appapi.py` (next to `test_graph_entity_shows_facts_history_pending_with_provenance`, ~line 536)

**Interfaces:**
- Produces: `GET /graph/entities` body `{"entities": [...unchanged...], "relations": [{"from": int, "to": int, "predicate": str}]}`. `from` = subject entity id, `to` = the entity whose normalised `display_name` equals the normalised fact value.

- [ ] **Step 1: Write failing tests** in `tests/test_appapi.py`, using the file's existing `kg_users` helper and `a(...)` caller pattern:
  - `test_graph_entities_relations_link_fact_values_to_entities`: create person Priya and org Acme (`kg.create_entity`), `kg.add_fact(priya.id, "employer", "acme", source_kind="user_stated")` (case differs on purpose). Assert `{"from": priya.id, "to": acme.id, "predicate": "employer"}` is in `body["relations"]`.
  - `test_graph_entities_relations_ignore_self_links_unlisted_and_duplicates`: an entity whose own fact value equals its own name gives no relation; a fact whose value matches no entity gives none; two facts yielding the same (from, to, predicate) give one entry; a second entity with the same name as the first yields no self-link.
  - Extend the existing user-isolation assertion at ~line 531 to expect `{"entities": [], "relations": []}`.
- [ ] **Step 2: Run** `pytest tests/test_appapi.py -k "graph_entities or graph_entity" -v`. Expected: new tests FAIL (`KeyError: 'relations'`).
- [ ] **Step 3: Implement** in `entity_list`: after listing entities, one user-scoped query for `subject_id, predicate, object_value` over `fact` where `status = 'active'` and `object_value IS NOT NULL`; build `{norm(display_name): [ids]}` from the listed entities (import `norm` from `jarvis.kg.facts`); emit a relation for each fact whose `norm(object_value)` maps to ids other than the subject; dedupe on the (from, to, predicate) tuple; sort by that tuple for stable output. Return `{"entities": ..., "relations": ...}`.
- [ ] **Step 4: Run** `pytest tests/test_appapi.py tests/test_isolation.py -k "graph" -v` and then `pytest tests/test_appapi.py -q`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add src/jarvis/kg/views.py tests/test_appapi.py
git commit -m "feat(kg): derive entity relations in the graph entity list"
```

---

### Task 2: Pure scene math (`graph3d.dart`)

**Files:**
- Create: `jarvis_app/lib/graph3d.dart`
- Test: `jarvis_app/test/graph3d_test.dart`

**Interfaces:**
- Produces (all in `graph3d.dart`, no Flutter widgets, only `dart:math` and `dart:ui` `Offset`/`Size`):
  - `class GraphNode { final int? entityId; final String label; final String kind; final int factCount; final bool isHub; final double x, z; bool get selectable => entityId != null; }` — `x,z` are ground-plane coordinates (y is up and always 0). `kind == 'self'` for Me.
  - `class GraphEdge { final GraphNode a, b; final String? predicate; bool get isRelation => predicate != null; }`
  - `class GraphScene { final List<GraphNode> nodes; final List<GraphEdge> edges; }`
  - `GraphScene buildScene(List<Map<String, dynamic>> entities, List<Map<String, dynamic>> relations)` — entities have `id,name,kind,fact_count`; relations have `from,to,predicate`.
  - `class Projected { final GraphNode node; final Offset pos; final double radius; final double depth; }` — `depth` in 0 (far) .. 1 (near).
  - `List<Projected> project(GraphScene s, {required double yaw, required double pitch, required double zoom, required Size size})` — returned sorted far-to-near (paint order).
  - `Projected? hitTest(List<Projected> items, Offset p, {double slop = 12})` — front-most selectable node whose circle (radius + slop) contains `p`.
  - Constants: `const pitchMin = 0.2, pitchMax = 1.2, zoomMin = 0.6, zoomMax = 2.5;`

Layout rules to implement (spec "Client behavior"): Me at (0,0) (synthesised with `entityId: null` if no `self` entity). Hubs, one per non-self kind present, evenly spaced on a ring of radius 1.0 starting at angle -π/2, in order person, place, org, thing, then unknown kinds. Entities of a kind sit on ring radius 2.0 inside an angular sector of width `0.8 * 2π / hubCount` centred on their hub's angle; more than 12 in a sector start another row at radius +0.6 per row. Hub label: People/Places/Orgs/Things. Edges: Me-hub and hub-entity (predicate null), plus one per relation whose both ends exist (predicate set). Projection: rotate (x,z) by yaw about the vertical axis, tilt by pitch, perspective with camera distance 6 units; `radius = (9 + min(factCount, 20) * 0.6) * (0.7 + 0.5 * depth) * zoom` for entities, 30 for Me, 14 for hubs (same depth/zoom scaling). Pitch and zoom are clamped to the constants inside `project`.

- [ ] **Step 1: Write failing unit tests** in `graph3d_test.dart`:
  - `buildScene` with no entities: only a non-selectable Me node (`entityId == null`), no hubs, no edges.
  - `buildScene` with self(id 5) + 2 persons + 1 org: nodes = Me(5, selectable), hubs "People" and "Orgs", 3 entity nodes; edges: Me-People, Me-Orgs, 3 hub-entity; a relation `{from: personA, to: org}` adds one `isRelation` edge with that predicate; a relation naming an unknown id is dropped.
  - 30 persons: all node `(x,z)` pairs are finite and distinct (extra rows used).
  - `project`: with `yaw: 0`, a node's screen `pos` differs from the same scene at `yaw: pi/2`; far nodes (`depth` smaller) have smaller `radius` than near nodes of equal `factCount`; list is sorted by `depth` ascending; `pitch: 99` gives the same output as `pitch: pitchMax`, `zoom: 99` the same as `zoomMax`.
  - `hitTest`: two overlapping nodes at the same screen point return the one with larger `depth`; a hub (non-selectable) is never returned; a point outside every circle+slop returns null.
- [ ] **Step 2: Run** `cd jarvis_app && flutter test test/graph3d_test.dart`. Expected: FAIL (file/symbols missing).
- [ ] **Step 3: Implement** `graph3d.dart` exactly to the interface above.
- [ ] **Step 4: Run** `cd jarvis_app && flutter test test/graph3d_test.dart`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add jarvis_app/lib/graph3d.dart jarvis_app/test/graph3d_test.dart
git commit -m "feat(app): pure 3D scene layout, projection and hit-test for the memory graph"
```

---

### Task 3: `MemoryGraph` widget (painter + gestures)

**Files:**
- Create: `jarvis_app/lib/memory_graph.dart`
- Test: `jarvis_app/test/memory_graph_test.dart`

**Interfaces:**
- Consumes: everything in Task 2's `graph3d.dart`; theme constants from `theme.dart`.
- Produces: `class MemoryGraph extends StatefulWidget { const MemoryGraph({super.key, required this.scene, required this.selectedId, required this.onSelect, this.autoRotate = true}); final GraphScene scene; final int? selectedId; final ValueChanged<int?> onSelect; final bool autoRotate; }` — `onSelect(null)` on tap of empty space. The interactive surface is a `CustomPaint` with `Key('memory-graph')`. Tests compute node positions with `project` from Task 2: with the exported constants `initialYaw = 0.0`, `initialPitch = 0.6` (declare these two consts in `memory_graph.dart`).

Behavior: horizontal drag changes yaw (about 0.01 rad/px), vertical drag changes pitch (clamped by `project`), release keeps a decaying velocity (inertia ends by itself); pinch scales zoom; idle auto-rotate at about 0.15 rad/s when `autoRotate` is true, paused while touching and for 3 s after (a `Ticker` that is stopped, not merely idle, when `autoRotate` is false and no inertia is running); tap uses `hitTest` on `project(...)` results. Painting, back to front from `project`: ground ellipses for the two rings, edges (structural faint `mutedText` ~0.25 alpha; relations ~0.5; selected node's relations amber and labelled `predicate` with underscores as spaces at the edge midpoint), ground shadows (dark ellipse under each node), spheres with radial gradients (Me: `amberLight`->`amberDeep`; entities: white->`blush`->darker blush; hubs: `cocoaTop`->`darkPill` with amber rim), selected node amber ring, depth fade (alpha and label opacity from `depth`), labels in `cream` (Me and hubs always; entities hidden when `depth` is low). Respect `disableAnimations`: pass `autoRotate` down from the screen (Task 4); the widget itself only reads its own parameter.

- [ ] **Step 1: Write failing widget tests** in `memory_graph_test.dart` (size the surface with `t.binding.setSurfaceSize(const Size(400, 700))`, always `autoRotate: false`):
  - `taps a node and reports its entity id`: build a scene via `buildScene` (self id 5, person id 1 named Priya); compute the person's position with `project(scene, yaw: initialYaw, pitch: initialPitch, zoom: 1, size: <size of the CustomPaint>)`; `t.tapAt` the global position (top-left of the `Key('memory-graph')` finder plus `pos`); expect `onSelect` called with 1.
  - `tapping empty space reports null`: tap a corner; expect `onSelect(null)`.
  - `tapping a hub does nothing selectable`: tap the People hub position; expect `onSelect(null)`.
  - `dragging rotates`: record `project` positions before; `t.drag(find.byKey(const Key('memory-graph')), const Offset(100, 0))`, `pumpAndSettle` (inertia must end); tap where the node used to be and expect it no longer selects node 1 (or select where it is now and expect 1). Pick whichever is robust; the point is that a drag changes where taps land.
  - `autoRotate false leaves no running ticker`: `await t.pumpAndSettle()` completes without timing out.
  - `renders with zero entities and with 120 entities without exceptions` (`expect(t.takeException(), isNull)`).
- [ ] **Step 2: Run** `cd jarvis_app && flutter test test/memory_graph_test.dart`. Expected: FAIL (file missing).
- [ ] **Step 3: Implement** `MemoryGraph` and its painter in `memory_graph.dart` per the behavior above; keep painter fields to `scene, yaw, pitch, zoom, selectedId` and compute `project(...)` once per `paint`.
- [ ] **Step 4: Run** `cd jarvis_app && flutter test test/memory_graph_test.dart test/graph3d_test.dart`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add jarvis_app/lib/memory_graph.dart jarvis_app/test/memory_graph_test.dart
git commit -m "feat(app): draggable rotatable 3D memory graph widget"
```

---

### Task 4: Wire the graph into `MemoryScreen`

**Files:**
- Modify: `jarvis_app/lib/memory_screen.dart` (state, `_load`, `build`, `_body`; keep `_PersonDetail` as is)
- Modify: `jarvis_app/test/memory_screen_test.dart`

**Interfaces:**
- Consumes: `MemoryGraph`, `buildScene` (Tasks 2-3); `GET /graph/entities` now also returns `relations`.
- Produces: `MemoryScreen({required api, onAskJarvis, bool autoRotate = true})`. The screen's rotation param is `autoRotate && !MediaQuery.disableAnimationsOf(context)`.

Behavior:
- State adds `_relations` (list of maps via `_maps`, tolerant of a missing key), `_showList` (false by default), `_selected` (entity id or null).
- `ScreenShell.trailing` is a `RoundButton` (tooltip `'List view'` / `'Graph view'`) toggling `_showList`.
- Graph mode: the graph fills the area; a Forgotten button (`Key('forgotten-button')`, label "Forgotten", hidden when there are none) opens `showModalBottomSheet` with the suppressions as blush tiles with the existing `Lift` buttons (`ValueKey('lift-<hmac>')`), reusing `_lift`. When `_selected` is set, a blush pill at the bottom (`Key('open-selected')`) shows `'<name> · <n> facts'` (`Me` for self) and opens `_PersonDetail` (same args as the list tile; `_load()` afterwards).
- Tapping an already-selected node opens its detail too (select once, tap again).
- Empty list: show the existing "Nothing remembered yet..." text (no graph). Loading and error states unchanged.
- List mode renders the existing `_body()` unchanged.

- [ ] **Step 1: Update existing tests, then write new ones** in `memory_screen_test.dart`:
  - `pump(...)` helper gains `{bool list = false}`: pumps `MemoryScreen(api: api, autoRotate: false)` and, when `list` is true, taps the `'List view'` button and settles.
  - Existing tests that use list rows (`'list, provenance, history, pending'`, `'edit and delete a fact'`, `'forget dialog ...'` x2, `'history rows can be deleted'`, `'a failed history delete ...'`, `'self entity is listed first ...'`) call `pump(t, api, list: true)`. The `'lift suppression'` test opens the Forgotten sheet via `Key('forgotten-button')` first. `'empty and error'` stays on the default (graph) pump.
  - New `'graph is the default and tapping a node twice opens its detail'`: pump default, compute Priya's position with `project`/`buildScene` (as in Task 3's test), tap once, expect the `Key('open-selected')` pill with text `Priya · 2 facts`, tap the node again, `pumpAndSettle`, expect `find.text('works_at: Acme')`.
  - New `'tapping the pill opens the detail'` and `'Me opens detail with no forget'` (self entity from the existing self test data; expect `find.text('Forget person'), findsNothing`).
  - New `'works without a self row'`: entities without a self kind still render and the Me node is not selectable (tapping the center keeps `Key('open-selected')` absent).
  - New `'relations from the server are tolerated when missing'`: routes with no `relations` key render without exception; with a relation present the selected node's predicate label path does not throw.
  - New `'toggle switches to list and back'`.
- [ ] **Step 2: Run** `cd jarvis_app && flutter test test/memory_screen_test.dart`. Expected: new tests FAIL, updated list tests FAIL until the toggle exists.
- [ ] **Step 3: Implement** the behavior above in `memory_screen.dart`.
- [ ] **Step 4: Run** `cd jarvis_app && flutter test test/memory_screen_test.dart test/memory_graph_test.dart test/graph3d_test.dart && flutter analyze lib/memory_screen.dart lib/memory_graph.dart lib/graph3d.dart`. Expected: all PASS, analyzer clean.
- [ ] **Step 5: Commit**

```bash
git add jarvis_app/lib/memory_screen.dart jarvis_app/test/memory_screen_test.dart
git commit -m "feat(app): memory screen opens on the 3D graph, list kept as a toggle"
```

---

### Task 5: Run it for real

**Files:** none changed unless a defect is found (then fix in the owning file and re-run its tests).

- [ ] **Step 1: Full suites.** `pytest -q` and `cd jarvis_app && flutter test`. Expected: PASS (or only failures already present on `main`; report any, with output).
- [ ] **Step 2: Run the app** on a simulator/device with `flutter run --dart-define-from-file=jarvis.env.json` against a server whose account has relations (create a person and an org with matching `employer` value if needed). Check by eye: cocoa look matches the other screens; drag rotates smoothly with inertia; pinch zooms within limits; auto-rotate stops on touch and is off with reduced motion; tap selects, second tap opens the detail; relation line and label show on selection; Forgotten sheet lifts a suppression; list toggle works; try 0, 1 and 100+ entities.
- [ ] **Step 3: Report** what was checked and any visual issues; do not claim device behavior that was not run.

---

## Self-review notes

- Spec coverage: radial layout (T2), app look + shaded spheres/shadows/depth (T3), drag/rotate/pinch/inertia/auto-rotate/reduced motion (T3, T4), real relations (T1; derivation amended in the spec), pill + detail reuse (T4), Me selectable (T4), Forgotten sheet (T4), list toggle (T4), empty/error states (T4), pure functions for tests (T2). Not covered by design: editing in the graph, new packages, remembered toggle.
- Names used across tasks: `buildScene`, `project`, `hitTest`, `GraphScene`, `GraphNode`, `Projected`, `MemoryGraph`, `initialYaw`, `initialPitch`, `autoRotate`, `Key('memory-graph')`, `Key('open-selected')`, `Key('forgotten-button')`.
