import 'package:flutter/material.dart';

import 'calendar_screen.dart';
import 'dashboard_cards.dart';
import 'theme.dart';

/// Android only exposes the next alarm, so this is a hero card + "set with Jarvis".
class AlarmsScreen extends StatelessWidget {
  const AlarmsScreen({super.key, required this.alarm, required this.onAskJarvis, this.now = DateTime.now});
  final Future<DateTime?> Function() alarm;
  final VoidCallback onAskJarvis;
  final DateTime Function() now;

  @override
  Widget build(BuildContext context) => ScreenShell(
        title: 'Alarms',
        trailing: RoundButton(icon: Icons.add, tooltip: 'Set an alarm', filled: true, onTap: onAskJarvis),
        child: FutureBuilder<DateTime?>(
          future: alarm().catchError((_) => null),
          builder: (context, snap) => ListView(padding: const EdgeInsets.fromLTRB(16, 16, 16, 24), children: [
            _hero(snap),
            const SizedBox(height: 16),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: amber, foregroundColor: amberInk, padding: const EdgeInsets.all(16)),
              onPressed: onAskJarvis,
              child: const Text('Set an alarm with Jarvis'),
            ),
          ]),
        ),
      );

  Widget _hero(AsyncSnapshot<DateTime?> snap) {
    final at = snap.data;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(gradient: amberGradient, borderRadius: BorderRadius.circular(32)),
      child: DefaultTextStyle.merge(
        style: const TextStyle(color: amberInk),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Row(children: [
            Icon(Icons.alarm, size: 20),
            SizedBox(width: 8),
            Flexible(child: Text('Next alarm', maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600))),
          ]),
          const SizedBox(height: 12),
          if (snap.connectionState != ConnectionState.done)
            const SizedBox(height: 72)
          else if (at == null)
            const Text('No alarm set', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w600))
          else
            FittedBox(
                fit: BoxFit.scaleDown,
                alignment: Alignment.centerLeft,
                child: Row(crossAxisAlignment: CrossAxisAlignment.baseline, textBaseline: TextBaseline.alphabetic, children: [
                  Text(hhmm(at), style: condensed(88, color: amberInk)),
                  const SizedBox(width: 12),
                  Text(dayLabel(at, now()), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
                ])),
        ]),
      ),
    );
  }
}
