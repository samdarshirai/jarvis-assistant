// Pure scene math for the 3D memory graph: layout, projection, hit-testing.
import 'dart:math';
import 'dart:ui' show Offset, Size;

const pitchMin = 0.2, pitchMax = 1.2, zoomMin = 0.6, zoomMax = 2.5;

class GraphNode {
  final int? entityId;
  final String label;
  final String kind;
  final int factCount;
  final bool isHub;
  final double x, z;
  const GraphNode({
    this.entityId,
    required this.label,
    required this.kind,
    this.factCount = 0,
    this.isHub = false,
    required this.x,
    required this.z,
  });
  bool get selectable => entityId != null;
}

class GraphEdge {
  final GraphNode a, b;
  final String? predicate;
  const GraphEdge(this.a, this.b, [this.predicate]);
  bool get isRelation => predicate != null;
}

class GraphScene {
  final List<GraphNode> nodes;
  final List<GraphEdge> edges;
  const GraphScene(this.nodes, this.edges);
}

const _kindOrder = ['person', 'place', 'org', 'thing'];
const _hubLabels = {
  'person': 'People',
  'place': 'Places',
  'org': 'Orgs',
  'thing': 'Things',
};
const _perRow = 12;

GraphScene buildScene(
  List<Map<String, dynamic>> entities,
  List<Map<String, dynamic>> relations,
) {
  Map<String, dynamic>? self;
  final byKind = <String, List<Map<String, dynamic>>>{};
  for (final e in entities) {
    final k = (e['kind'] ?? 'thing') as String;
    if (k == 'self') {
      self ??= e;
    } else {
      byKind.putIfAbsent(k, () => []).add(e);
    }
  }
  final kinds = [
    ..._kindOrder.where(byKind.containsKey),
    ...byKind.keys.where((k) => !_kindOrder.contains(k)),
  ];

  final me = GraphNode(
    entityId: self?['id'] as int?,
    label: (self?['name'] as String?) ?? 'Me',
    kind: 'self',
    factCount: (self?['fact_count'] as int?) ?? 0,
    x: 0,
    z: 0,
  );
  final nodes = <GraphNode>[me];
  final edges = <GraphEdge>[];
  final byId = <int, GraphNode>{if (me.entityId != null) me.entityId!: me};

  final n = kinds.length;
  for (var i = 0; i < n; i++) {
    final kind = kinds[i];
    final a = -pi / 2 + i * 2 * pi / n;
    final hub = GraphNode(
      label: _hubLabels[kind] ?? '${kind[0].toUpperCase()}${kind.substring(1)}s',
      kind: kind,
      isHub: true,
      x: cos(a),
      z: sin(a),
    );
    nodes.add(hub);
    edges.add(GraphEdge(me, hub));
    final list = byKind[kind]!;
    final width = 0.8 * 2 * pi / n;
    for (var j = 0; j < list.length; j++) {
      final row = j ~/ _perRow;
      final inRow = min(_perRow, list.length - row * _perRow);
      final slot = j % _perRow;
      final ang = a + width * ((slot + 0.5) / inRow - 0.5);
      final r = 2.0 + 0.6 * row;
      final e = list[j];
      final node = GraphNode(
        entityId: e['id'] as int,
        label: (e['name'] ?? '') as String,
        kind: kind,
        factCount: (e['fact_count'] as int?) ?? 0,
        x: r * cos(ang),
        z: r * sin(ang),
      );
      nodes.add(node);
      byId[node.entityId!] = node;
      edges.add(GraphEdge(hub, node));
    }
  }
  for (final r in relations) {
    final a = byId[r['from']], b = byId[r['to']];
    if (a != null && b != null) {
      edges.add(GraphEdge(a, b, r['predicate'] as String?));
    }
  }
  return GraphScene(nodes, edges);
}

class Projected {
  final GraphNode node;
  final Offset pos;
  final double radius;
  final double depth;
  const Projected(this.node, this.pos, this.radius, this.depth);
}

const _camDist = 6.0;
const _extent = 5.0; // view-space z range mapped onto depth 0..1

List<Projected> project(
  GraphScene s, {
  required double yaw,
  required double pitch,
  required double zoom,
  required Size size,
}) {
  final p = pitch.clamp(pitchMin, pitchMax).toDouble();
  final zm = zoom.clamp(zoomMin, zoomMax).toDouble();
  final cy = cos(yaw), sy = sin(yaw), cp = cos(p), sp = sin(p);
  final unit = size.shortestSide * 0.12;
  final c = Offset(size.width / 2, size.height / 2);
  final out = <Projected>[];
  for (final n in s.nodes) {
    final rx = n.x * cy + n.z * sy;
    final rz = -n.x * sy + n.z * cy;
    final vz = rz * cp; // toward camera
    final f = _camDist / (_camDist - vz);
    final pos = c + Offset(rx, rz * sp) * (unit * f * zm);
    final depth = ((vz / _extent + 1) / 2).clamp(0.0, 1.0).toDouble();
    final base = n.kind == 'self'
        ? 30.0
        : n.isHub
            ? 14.0
            : 9 + min(n.factCount, 20) * 0.6;
    out.add(Projected(n, pos, base * (0.7 + 0.5 * depth) * zm, depth));
  }
  out.sort((a, b) => a.depth.compareTo(b.depth));
  return out;
}

Projected? hitTest(List<Projected> items, Offset p, {double slop = 12}) {
  Projected? best;
  for (final it in items) {
    if (!it.node.selectable) continue;
    if ((it.pos - p).distance <= it.radius + slop &&
        (best == null || it.depth > best.depth)) {
      best = it;
    }
  }
  return best;
}
