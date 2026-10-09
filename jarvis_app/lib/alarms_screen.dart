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
        onMic: onAskJarvis,
        trailing: RoundButton(icon: Icons.add, tooltip: 'Set an alarm', onTap: onAskJarvis),
        child: FutureBuilder<DateTime?>(
          future: alarm().catchError((_) => null),
          builder: (context, snap) => ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 110), children: [
            _hero(snap),
            const SizedBox(height: 8),
            GestureDetector(
              onTap: onAskJarvis,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
                decoration: BoxDecoration(color: blush, borderRadius: BorderRadius.circular(28)),
                child: const Row(children: [
                  Icon(Icons.mic, color: ink, size: 22),
                  SizedBox(width: 12),
                  Expanded(child: Text('Set an alarm with Jarvis', style: TextStyle(color: ink, fontSize: 15, fontWeight: FontWeight.w500))),
                ]),
              ),
            ),
          ]),
        ),
      );

  Widget _hero(AsyncSnapshot<DateTime?> snap) {
    final at = snap.data;
    return Padding(
      padding: const EdgeInsets.fromLTRB(0, 20, 0, 26),
      child: Column(children: [
        const Text('Next alarm', style: TextStyle(fontSize: 13, color: mutedText)),
        const SizedBox(height: 4),
        if (snap.connectionState != ConnectionState.done)
          const SizedBox(height: 104)
        else if (at == null)
          Text('No alarm set', style: condensed(56, color: cream))
        else ...[
          FittedBox(fit: BoxFit.scaleDown, child: Text(hhmm(at), style: condensed(104, color: cream))),
          const SizedBox(height: 4),
          Text(dayLabel(at, now()), style: const TextStyle(fontSize: 15, color: amber)),
        ],
      ]),
    );
  }
}
