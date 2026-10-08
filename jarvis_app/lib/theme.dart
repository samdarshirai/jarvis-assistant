import 'dart:ui';

import 'package:flutter/material.dart';

const bgTop = Color(0xFF0B1020), bgBottom = Color(0xFF05070D);
const mutedText = Color(0xFF9AA4B8);
const accentBrief = Color(0xFF8B7CFF),
    accentCal = Color(0xFF4FC3F7),
    accentTasks = Color(0xFF7CE0A3),
    accentMail = Color(0xFFFFB86B),
    accentNotes = Color(0xFFF48FB1),
    accentAlarm = Color(0xFFFFD54F);

ThemeData jarvisTheme() => ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorScheme: ColorScheme.fromSeed(seedColor: accentBrief, brightness: Brightness.dark),
      scaffoldBackgroundColor: bgBottom,
      appBarTheme: const AppBarTheme(backgroundColor: Colors.transparent, elevation: 0, scrolledUnderElevation: 0),
    );

Widget _glow(Color c, double size) => IgnorePointer(
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
            shape: BoxShape.circle, gradient: RadialGradient(colors: [c.withValues(alpha: 0.22), Colors.transparent])),
      ),
    );

/// Static gradient backdrop with two soft glows (violet top-right, cyan bottom-left);
/// wrap each screen body in it. No animation.
class GlassBackground extends StatelessWidget {
  const GlassBackground({super.key, required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) => DecoratedBox(
        decoration: const BoxDecoration(
            gradient: LinearGradient(begin: Alignment.topCenter, end: Alignment.bottomCenter, colors: [bgTop, bgBottom])),
        child: Stack(children: [
          Positioned(top: -120, right: -80, child: _glow(accentBrief, 320)),
          Positioned(bottom: -160, left: -120, child: _glow(accentCal, 360)),
          Positioned.fill(child: child),
        ]),
      );
}

/// Translucent rounded card: light-to-dark white sheen, hairline border, accent-coloured header.
class GlassCard extends StatelessWidget {
  const GlassCard(
      {super.key, required this.title, required this.icon, required this.accent, required this.child, this.trailing});
  final String title;
  final IconData icon;
  final Color accent;
  final Widget child;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => ClipRRect(
        borderRadius: BorderRadius.circular(24),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [Colors.white.withValues(alpha: 0.09), Colors.white.withValues(alpha: 0.03)]),
              borderRadius: BorderRadius.circular(24),
              border: Border.all(color: Colors.white.withValues(alpha: 0.12)),
            ),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Container(
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(shape: BoxShape.circle, color: accent.withValues(alpha: 0.18)),
                  child: Icon(icon, size: 16, color: accent),
                ),
                const SizedBox(width: 10),
                Expanded(
                    child: Text(title,
                        style: Theme.of(context)
                            .textTheme
                            .titleSmall
                            ?.copyWith(color: accent, fontWeight: FontWeight.w600, letterSpacing: 0.2))),
                ?trailing,
              ]),
              const SizedBox(height: 12),
              child,
            ]),
          ),
        ),
      );
}
