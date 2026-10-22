import 'package:flutter/material.dart';

import 'calendar_screen.dart' show ScreenShell;
import 'prefs.dart';
import 'theme.dart';

/// One-time choice, shown before the home screen: should "Hey Jarvis" listen at all?
class WakeChoiceScreen extends StatefulWidget {
  const WakeChoiceScreen({super.key, required this.prefs, required this.onDone});
  final Prefs prefs;
  final VoidCallback onDone;

  @override
  State<WakeChoiceScreen> createState() => _WakeChoiceScreenState();
}

class _WakeChoiceScreenState extends State<WakeChoiceScreen> {
  bool _on = true, _busy = false;

  Future<void> _continue() async {
    setState(() => _busy = true);
    await widget.prefs.setWakeEnabled(_on);
    await widget.prefs.markWakeChosen();
    widget.onDone();
  }

  @override
  Widget build(BuildContext context) => ScreenShell(
        title: 'Hey Jarvis',
        child: ListView(padding: const EdgeInsets.all(16), children: [
          const Text('Say "Hey Jarvis" to start a conversation hands-free. It listens only while your phone is charging or on Android Auto.',
              style: TextStyle(color: cream)),
          const SizedBox(height: 8),
          SwitchListTile(
              contentPadding: EdgeInsets.zero,
              activeThumbColor: amber,
              title: const Text('Hey Jarvis wake word', style: TextStyle(color: cream)),
              value: _on,
              onChanged: (v) => setState(() => _on = v)),
          const Text('You can change this later in Settings.', style: TextStyle(color: mutedText, fontSize: 12)),
          const SizedBox(height: 16),
          FilledButton(onPressed: _busy ? null : _continue, child: const Text('Continue')),
        ]),
      );
}
