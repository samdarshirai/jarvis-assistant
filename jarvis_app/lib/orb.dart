import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'theme.dart';

const _orbColors = [Color(0xFFFFE29A), amber, Color(0xFFF08A00), amberLight];

/// The design's orb: 5 rings of 9 ellipses each, counter-rotating, breathing, with an amber glow.
/// `speed` scales both rotation and breathing (listening 1, speaking 1.6, thinking 3).
class AnimatedOrb extends StatefulWidget {
  const AnimatedOrb({super.key, required this.size, this.speed = 1});
  final double size, speed;

  @override
  State<AnimatedOrb> createState() => _AnimatedOrbState();
}

class _AnimatedOrbState extends State<AnimatedOrb> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: const Duration(seconds: 120))..repeat();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ExcludeSemantics(
        child: RepaintBoundary(
          child: AnimatedBuilder(
            animation: _c,
            builder: (_, _) => CustomPaint(
                size: Size.square(widget.size), painter: _OrbPainter(_c.value * 120, widget.speed)),
          ),
        ),
      );
}

class _OrbPainter extends CustomPainter {
  _OrbPainter(this.t, this.speed); // t: seconds
  final double t, speed;

  @override
  void paint(Canvas canvas, Size s) {
    final size = s.width, c = Offset(size / 2, size / 2);
    final breathe = 0.94 + 0.11 * (0.5 - 0.5 * math.cos(2 * math.pi * t * speed / 5));
    canvas.save();
    canvas.translate(c.dx, c.dy);
    canvas.scale(breathe);
    canvas.translate(-c.dx, -c.dy);
    canvas.drawCircle(
        c,
        size * 0.25,
        Paint()
          ..shader = RadialGradient(colors: [amber.withValues(alpha: 0.18), Colors.transparent])
              .createShader(Rect.fromCircle(center: c, radius: size * 0.25)));
    for (var l = 0; l < 5; l++) {
      final period = (10 + l * 4) / speed;
      final spin = (l.isOdd ? -1 : 1) * 2 * math.pi * t / period;
      canvas.save();
      canvas.translate(c.dx, c.dy);
      canvas.rotate(spin);
      for (var k = 0; k < 9; k++) {
        canvas.save();
        canvas.rotate((l * 37 + k * 2.2) * math.pi / 180);
        final rx = size * (0.3 + l * 0.018) + k * size * 0.006, ry = size * (0.2 + (l % 3) * 0.03) + k * size * 0.007;
        canvas.drawOval(
            Rect.fromCenter(center: Offset.zero, width: rx * 2, height: ry * 2),
            Paint()
              ..style = PaintingStyle.stroke
              ..strokeWidth = size > 100 ? 0.9 : 0.6
              ..color = _orbColors[(l + k) % 4].withValues(alpha: 0.35 + (k % 3) * 0.15));
        canvas.restore();
      }
      canvas.restore();
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(_OrbPainter o) => o.t != t || o.speed != speed;
}
