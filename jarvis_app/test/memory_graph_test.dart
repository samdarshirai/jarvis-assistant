import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/graph3d.dart';
import 'package:jarvis_app/memory_graph.dart';

GraphScene _scene() => buildScene([
      {'id': 5, 'name': 'Me', 'kind': 'self', 'fact_count': 1},
      {'id': 1, 'name': 'Priya', 'kind': 'person', 'fact_count': 3},
    ], []);

Future<List<int?>> _pump(WidgetTester t, GraphScene scene) async {
  await t.binding.setSurfaceSize(const Size(400, 700));
  addTearDown(() => t.binding.setSurfaceSize(null));
  final taps = <int?>[];
  await t.pumpWidget(MaterialApp(
      home: Scaffold(
          body: MemoryGraph(scene: scene, selectedId: null, onSelect: taps.add, autoRotate: false))));
  return taps;
}

Offset _at(WidgetTester t, GraphScene s, int id, {double yaw = initialYaw, bool hub = false}) {
  final f = find.byKey(const Key('memory-graph'));
  final p = project(s, yaw: yaw, pitch: initialPitch, zoom: 1, size: t.getSize(f))
      .firstWhere((e) => hub ? e.node.isHub : e.node.entityId == id);
  return t.getTopLeft(f) + p.pos;
}

void main() {
  fitTests();
  testWidgets('taps a node and reports its entity id', (t) async {
    final s = _scene();
    final taps = await _pump(t, s);
    await t.tapAt(_at(t, s, 1));
    expect(taps, [1]);
  });

  testWidgets('tapping empty space reports null', (t) async {
    final taps = await _pump(t, _scene());
    await t.tapAt(const Offset(5, 5));
    expect(taps, [null]);
  });

  testWidgets('tapping a hub does nothing selectable', (t) async {
    // No self entity: Me is not selectable, so only the hub sits under the tap (it overlaps Me otherwise).
    final s = buildScene([
      {'id': 1, 'name': 'Priya', 'kind': 'person', 'fact_count': 0},
    ], []);
    final taps = await _pump(t, s);
    // Nudge toward Me, away from the entity that sits behind the hub.
    await t.tapAt(_at(t, s, 0, hub: true) + const Offset(0, 8));
    expect(taps, [null]);
  });

  testWidgets('dragging rotates', (t) async {
    final s = _scene();
    final taps = await _pump(t, s);
    final f = find.byKey(const Key('memory-graph'));
    final before = _at(t, s, 1);
    // Hold still before lifting so there is no fling; ~0.01 rad per px.
    final g = await t.startGesture(t.getCenter(f));
    await g.moveBy(const Offset(50, 0));
    await g.moveBy(const Offset(50, 0));
    await t.pump(const Duration(milliseconds: 500));
    await g.up();
    await t.pumpAndSettle();
    await t.tapAt(before);
    expect(taps, [null]);
    final p = project(s, yaw: initialYaw + 1.0, pitch: initialPitch, zoom: 1, size: t.getSize(f))
        .firstWhere((e) => e.node.entityId == 1);
    await t.tapAt(t.getTopLeft(f) + p.pos);
    expect(taps.last, 1);
  });

  testWidgets('autoRotate false leaves no running ticker', (t) async {
    await _pump(t, _scene());
    await t.pumpAndSettle();
  });

  testWidgets('renders with zero entities and with 120 entities', (t) async {
    await _pump(t, buildScene([], []));
    expect(t.takeException(), isNull);
    final many = [
      for (var i = 0; i < 120; i++) {'id': i + 1, 'name': 'P$i', 'kind': i % 3 == 0 ? 'org' : 'person', 'fact_count': i % 25},
    ];
    final rel = [
      {'from': 1, 'to': 2, 'predicate': 'works_with'}
    ];
    await t.pumpWidget(MaterialApp(
        home: Scaffold(
            body: MemoryGraph(scene: buildScene(many, rel), selectedId: 1, onSelect: (_) {}, autoRotate: false))));
    expect(t.takeException(), isNull);
  });
}

void fitTests() {
  test('120 persons stay on canvas at every pitch', () {
    final s = buildScene([for (var i = 0; i < 120; i++) {'id': i + 1, 'name': 'P$i', 'kind': 'person', 'fact_count': 1}], []);
    const size = Size(400, 700);
    for (final pitch in [initialPitch, pitchMin, pitchMax]) {
      for (final p in project(s, yaw: 0.7, pitch: pitch, zoom: 1, size: size)) {
        expect(p.pos.dx.abs() < 800 && p.pos.dy.abs() < 1400, isTrue, reason: '${p.node.label} ${p.pos}');
      }
    }
  });

  test('small scene has fit 1', () {
    expect(sceneFit(_scene()), 1.0);
    final big = buildScene([for (var i = 0; i < 30; i++) {'id': i + 1, 'name': 'P$i', 'kind': 'person', 'fact_count': 1}], []);
    expect(sceneFit(big), lessThan(1.0));
  });

  test('two predicates between one pair get distinct label offsets', () {
    final s = buildScene([
      {'id': 1, 'name': 'A', 'kind': 'person', 'fact_count': 1},
      {'id': 2, 'name': 'B', 'kind': 'person', 'fact_count': 1},
    ], [
      {'from': 1, 'to': 2, 'predicate': 'works_with'},
      {'from': 1, 'to': 2, 'predicate': 'friend_of'},
    ]);
    final byNode = {for (final p in project(s, yaw: 0, pitch: initialPitch, zoom: 1, size: const Size(400, 700))) p.node: p};
    final l = relationLabels(s, byNode, 1);
    expect(l.length, 2);
    expect(l[1].at - l[0].at, const Offset(0, 12));
    expect(l[0].text, 'works with B');
    expect(relationLabels(s, byNode, 2)[0].text, 'A works with');
  });
}
