import 'package:flutter/material.dart';

import 'session.dart';

// Cocoa / blush / amber palette from the Claude Design prototype.
const cocoaTop = Color(0xFF6E4C43), cocoaMid = Color(0xFF3D2A26), cocoaBottom = Color(0xFF211715);
const ink = Color(0xFF1A1110), amberInk = Color(0xFF2A1600), darkPill = Color(0xFF0F0B0A);
const cream = Color(0xFFF7EDE8), mutedText = Color(0xFFC9B4AC);
const blush = Color(0xFFFCE6DD), amber = Color(0xFFFFB000), amberLight = Color(0xFFFFC94D), amberDeep = Color(0xFFF59E0B);
const danger = Color(0xFFFF7A66);

const amberGradient = LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: [amberLight, amberDeep]);

TextStyle logoStyle(double size) =>
    TextStyle(fontFamily: 'Unbounded', fontWeight: FontWeight.w800, fontSize: size, color: amber, letterSpacing: size * 0.03);

TextStyle condensed(double size, {FontWeight weight = FontWeight.w700, Color? color}) =>
    TextStyle(fontFamily: 'BarlowCondensed', fontWeight: weight, fontSize: size, height: 1, color: color);

ThemeData jarvisTheme() => ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      fontFamily: 'InstrumentSans',
      colorScheme: ColorScheme.fromSeed(seedColor: amber, brightness: Brightness.dark, surface: cocoaBottom),
      scaffoldBackgroundColor: cocoaBottom,
      appBarTheme: const AppBarTheme(backgroundColor: Colors.transparent, elevation: 0, scrolledUnderElevation: 0),
    );

/// Cocoa radial backdrop; wrap each screen body in it.
class CocoaBackground extends StatelessWidget {
  const CocoaBackground({super.key, required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) => DecoratedBox(
        decoration: const BoxDecoration(
            gradient: RadialGradient(
                center: Alignment(0.7, -0.84), radius: 1.3, colors: [cocoaTop, cocoaMid, cocoaBottom], stops: [0, 0.48, 1])),
        child: child,
      );
}

/// Rounded titled card. Blush (light, ink text) by default; `dark` gives the translucent pill style.
class BlushCard extends StatelessWidget {
  const BlushCard(
      {super.key, required this.title, required this.child, this.trailing, this.dark = false, this.onTap});
  final String title;
  final Widget child;
  final Widget? trailing;
  final bool dark;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.fromLTRB(12, 16, 12, 12),
          decoration: BoxDecoration(
            color: dark ? darkPill.withValues(alpha: 0.35) : blush,
            borderRadius: BorderRadius.circular(32),
            border: dark ? Border.all(color: Colors.white.withValues(alpha: 0.06)) : null,
          ),
          child: DefaultTextStyle.merge(
            style: TextStyle(color: dark ? cream : ink),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(4, 0, 4, 8),
                child: Row(children: [
                  Expanded(child: Text(title, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600))),
                  ?trailing,
                ]),
              ),
              child,
            ]),
          ),
        ),
      );
}

/// Amber orb: mic when idle, stop while a session runs; ring pulses while active.
class AmberOrb extends StatelessWidget {
  const AmberOrb({super.key, required this.phase, required this.onTap, this.size = 72});
  final Phase phase;
  final VoidCallback onTap;
  final double size;

  @override
  Widget build(BuildContext context) {
    final inactive = phase == Phase.idle || phase == Phase.offline;
    return Tooltip(
      message: inactive ? 'Talk' : 'Stop',
      child: Semantics(
        button: true,
        label: inactive ? 'Talk' : 'Stop',
        excludeSemantics: true,
        child: GestureDetector(
          onTap: onTap,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 250),
            width: size,
            height: size,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: amberGradient,
              boxShadow: [BoxShadow(color: amber.withValues(alpha: inactive ? 0.25 : 0.55), blurRadius: inactive ? 12 : 30)],
            ),
            child: Icon(inactive ? Icons.mic : Icons.stop, size: size * 0.45, color: amberInk),
          ),
        ),
      ),
    );
  }
}
