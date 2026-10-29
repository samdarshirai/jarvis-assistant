// Draggable, rotatable 3D knowledge-graph view. Math lives in graph3d.dart.
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'graph3d.dart';
import 'theme.dart';

const initialYaw = 0.0, initialPitch = 0.6;
const _autoSpeed = 0.15; // rad/s
const _idlePause = 3.0; // s after a touch before auto-rotate resumes

class MemoryGraph extends StatefulWidget {
  const MemoryGraph(
      {super.key, required this.scene, required this.selectedId, required this.onSelect, this.autoRotate = true});
  final GraphScene scene;
  final int? selectedId;
  final ValueChanged<int?> onSelect;
  final bool autoRotate;

  @override
  State<MemoryGraph> createState() => _MemoryGraphState();
}

class _MemoryGraphState extends State<MemoryGraph> with SingleTickerProviderStateMixin {
  late final Ticker _ticker = createTicker(_tick);
  double _yaw = initialYaw, _pitch = initialPitch, _zoom = 1;
  double _vYaw = 0, _vPitch = 0; // rad/s, decaying after release
  double _pause = 0; // seconds of auto-rotate pause left
  bool _touching = false, _pinched = false;
  Duration _last = Duration.zero;
  double _zoomStart = 1;
  Size _size = Size.zero;

  @override
  void initState() {
    super.initState();
    _syncTicker();
  }

  @override
  void didUpdateWidget(MemoryGraph old) {
    super.didUpdateWidget(old);
    _syncTicker();
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  bool get _moving => _vYaw.abs() > 0.02 || _vPitch.abs() > 0.02;

  void _syncTicker() {
    final need = widget.autoRotate || _moving;
    if (need && !_ticker.isActive) {
      _last = Duration.zero;
      _ticker.start();
    } else if (!need && _ticker.isActive) {
      _ticker.stop();
    }
  }

  void _tick(Duration now) {
    final dt = (now - _last).inMicroseconds / 1e6;
    _last = now;
    if (_touching) return;
    setState(() {
      _yaw += _vYaw * dt;
      _pitch = (_pitch + _vPitch * dt).clamp(pitchMin, pitchMax);
      final k = pow(0.04, dt).toDouble(); // ~96% gone per second
      _vYaw *= k;
      _vPitch *= k;
      if (!_moving) {
        _vYaw = _vPitch = 0;
        if (_pause > 0) {
          _pause -= dt;
        } else if (widget.autoRotate) {
          _yaw += _autoSpeed * dt;
        }
      }
    });
    _syncTicker();
  }

  void _tapUp(TapUpDetails d) {
    final items = project(widget.scene, yaw: _yaw, pitch: _pitch, zoom: _zoom, size: _size);
    widget.onSelect(hitTest(items, d.localPosition)?.node.entityId);
  }

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        _size = c.biggest;
        return GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTapUp: _tapUp,
          onScaleStart: (d) {
            _touching = true;
            _vYaw = _vPitch = 0;
            _zoomStart = _zoom;
            _pinched = false;
          },
          onScaleUpdate: (d) => setState(() {
            if (d.pointerCount > 1) _pinched = true;
            if (d.pointerCount == 1) {
              _yaw += d.focalPointDelta.dx * 0.01;
              _pitch = (_pitch - d.focalPointDelta.dy * 0.01).clamp(pitchMin, pitchMax);
            }
            _zoom = (_zoomStart * d.scale).clamp(zoomMin, zoomMax);
          }),
          onScaleEnd: (d) {
            _touching = false;
            if (!_pinched) {
              final v = d.velocity.pixelsPerSecond; // px/s -> rad/s at 0.01 rad/px
              _vYaw = v.dx * 0.01;
              _vPitch = -v.dy * 0.01;
            }
            _pause = _idlePause;
            _syncTicker();
          },
          child: CustomPaint(
            key: const Key('memory-graph'),
            size: _size,
            painter: _GraphPainter(widget.scene, _yaw, _pitch, _zoom, widget.selectedId),
          ),
        );
      });
}

// Ground rings (radius 1 hubs, radius 2 entities) as projected polylines.
final _rings = [
  for (final r in const [1.0, 2.0])
    GraphScene([for (var i = 0; i < 48; i++) GraphNode(label: '', kind: 'ring', x: r * cos(i * pi / 24), z: r * sin(i * pi / 24))], const [])
];

class _GraphPainter extends CustomPainter {
  _GraphPainter(this.scene, this.yaw, this.pitch, this.zoom, this.selectedId);
  final GraphScene scene;
  final double yaw, pitch, zoom;
  final int? selectedId;

  @override
  bool shouldRepaint(_GraphPainter o) =>
      o.scene != scene || o.yaw != yaw || o.pitch != pitch || o.zoom != zoom || o.selectedId != selectedId;

  void _label(Canvas canvas, String text, Offset at, double opacity, {double size = 11, bool above = false}) {
    if (opacity <= 0.02) return;
    final tp = TextPainter(
      text: TextSpan(text: text, style: TextStyle(color: cream.withValues(alpha: opacity), fontSize: size)),
      textDirection: TextDirection.ltr,
      maxLines: 1,
      ellipsis: '…',
    )..layout(maxWidth: 110);
    tp.paint(canvas, at - Offset(tp.width / 2, above ? tp.height / 2 : 0));
  }

  @override
  void paint(Canvas canvas, Size size) {
    final items = project(scene, yaw: yaw, pitch: pitch, zoom: zoom, size: size);
    final byNode = {for (final p in items) p.node: p};

    for (final ring in _rings) {
      final pts = project(ring, yaw: yaw, pitch: pitch, zoom: zoom, size: size);
      final order = {for (final p in pts) p.node: p.pos};
      final path = Path()..addPolygon([for (final n in ring.nodes) order[n]!], true);
      canvas.drawPath(
          path,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1
            ..color = mutedText.withValues(alpha: 0.12));
    }

    for (final e in scene.edges) {
      final a = byNode[e.a]!, b = byNode[e.b]!;
      final sel = e.isRelation && selectedId != null && (e.a.entityId == selectedId || e.b.entityId == selectedId);
      final depth = (a.depth + b.depth) / 2;
      final base = sel ? 0.9 : e.isRelation ? 0.5 : 0.25;
      canvas.drawLine(
          a.pos,
          b.pos,
          Paint()
            ..strokeWidth = sel ? 2 : 1
            ..color = (sel ? amber : mutedText).withValues(alpha: base * (0.4 + 0.6 * depth)));
      if (sel) _label(canvas, e.predicate!.replaceAll('_', ' '), (a.pos + b.pos) / 2, 1, size: 10, above: true);
    }

    for (final p in items) {
      final n = p.node;
      final fade = 0.35 + 0.65 * p.depth;
      final r = p.radius;
      canvas.drawOval(
          Rect.fromCenter(center: p.pos + Offset(0, r * 1.5), width: r * 1.8, height: r * 0.5),
          Paint()..color = ink.withValues(alpha: 0.28 * fade));

      final List<Color> colors = n.kind == 'self'
          ? [amberLight, amberDeep]
          : n.isHub
              ? [cocoaTop, darkPill]
              : [cream, blush, mutedText];
      final rect = Rect.fromCircle(center: p.pos, radius: r);
      canvas.drawCircle(
          p.pos,
          r,
          Paint()
            ..shader = RadialGradient(
                    center: const Alignment(-0.35, -0.4),
                    colors: [for (final c in colors) c.withValues(alpha: fade)])
                .createShader(rect));
      if (n.isHub) {
        canvas.drawCircle(
            p.pos,
            r,
            Paint()
              ..style = PaintingStyle.stroke
              ..strokeWidth = 1.5
              ..color = amber.withValues(alpha: fade));
      }
      if (n.entityId != null && n.entityId == selectedId) {
        canvas.drawCircle(
            p.pos,
            r + 4,
            Paint()
              ..style = PaintingStyle.stroke
              ..strokeWidth = 2.5
              ..color = amber);
      }
      if (n.kind == 'self' || n.isHub) {
        _label(canvas, n.label, p.pos + Offset(0, r + 4), fade, size: n.isHub ? 11 : 13);
      } else if (p.depth > 0.35 || n.entityId == selectedId) {
        _label(canvas, n.label, p.pos + Offset(0, r + 4), fade);
      }
    }
  }
}
