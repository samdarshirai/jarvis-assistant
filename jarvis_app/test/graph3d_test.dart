import 'dart:math';
import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/graph3d.dart';

Map<String, dynamic> ent(int id, String kind, [int fc = 1]) =>
    {'id': id, 'name': 'e$id', 'kind': kind, 'fact_count': fc};

void main() {
  test('empty scene has only non-selectable Me', () {
    final s = buildScene([], []);
    expect(s.nodes.length, 1);
    expect(s.nodes.first.entityId, isNull);
    expect(s.nodes.first.selectable, isFalse);
    expect(s.edges, isEmpty);
  });

  test('self + persons + org', () {
    final ents = [ent(5, 'self'), ent(1, 'person'), ent(2, 'person'), ent(3, 'org')];
    final s = buildScene(ents, [
      {'from': 1, 'to': 3, 'predicate': 'works_at'},
      {'from': 1, 'to': 99, 'predicate': 'x'},
    ]);
    expect(s.nodes.length, 1 + 2 + 3);
    expect(s.nodes.first.entityId, 5);
    expect(s.nodes.first.selectable, isTrue);
    expect(s.nodes.where((n) => n.isHub).map((n) => n.label), ['People', 'Orgs']);
    expect(s.edges.where((e) => !e.isRelation).length, 2 + 3);
    final rel = s.edges.where((e) => e.isRelation).toList();
    expect(rel.length, 1);
    expect(rel.first.predicate, 'works_at');
  });

  test('30 persons all distinct and finite', () {
    final s = buildScene([for (var i = 1; i <= 30; i++) ent(i, 'person')], []);
    final pts = s.nodes.map((n) => '${n.x},${n.z}').toSet();
    expect(pts.length, s.nodes.length);
    expect(s.nodes.every((n) => n.x.isFinite && n.z.isFinite), isTrue);
  });

  group('project', () {
    final scene = buildScene(
        [for (var i = 1; i <= 6; i++) ent(i, 'person', 5)], []);
    const size = Size(400, 400);
    List<Projected> proj(double yaw, double pitch, double zoom) =>
        project(scene, yaw: yaw, pitch: pitch, zoom: zoom, size: size);

    test('yaw moves nodes', () {
      final a = proj(0, 0.7, 1), b = proj(pi / 2, 0.7, 1);
      Offset at(List<Projected> l) =>
          l.firstWhere((p) => p.node.entityId == 1).pos;
      expect(at(a), isNot(at(b)));
    });

    test('far smaller than near, sorted ascending', () {
      final l = proj(0, 0.7, 1);
      for (var i = 1; i < l.length; i++) {
        expect(l[i].depth >= l[i - 1].depth, isTrue);
      }
      final ents = l.where((p) => p.node.entityId != null).toList();
      expect(ents.first.depth < ents.last.depth, isTrue);
      expect(ents.first.radius < ents.last.radius, isTrue);
    });

    test('pitch and zoom clamp', () {
      final a = proj(0.3, 99, 99), b = proj(0.3, pitchMax, zoomMax);
      expect(a.map((p) => p.pos), b.map((p) => p.pos));
      expect(a.map((p) => p.radius), b.map((p) => p.radius));
    });
  });

  group('hitTest', () {
    GraphNode node(int? id, {bool hub = false}) => GraphNode(
        entityId: id, label: 'n', kind: 'person', isHub: hub, x: 0, z: 0);
    test('front-most selectable wins; hub never; miss null', () {
      final far = Projected(node(1), const Offset(50, 50), 10, 0.2);
      final near = Projected(node(2), const Offset(50, 50), 10, 0.8);
      final hub = Projected(node(null, hub: true), const Offset(200, 200), 14, 1);
      final items = [far, near, hub];
      expect(hitTest(items, const Offset(52, 50))?.node.entityId, 2);
      expect(hitTest(items, const Offset(200, 200)), isNull);
      expect(hitTest(items, const Offset(300, 300)), isNull);
    });
  });
}
