import 'package:flutter/material.dart';

import 'config.dart';
import 'session.dart';

class SessionScreen extends StatelessWidget {
  const SessionScreen({super.key, required this.controller});
  final SessionController controller;

  static const _labels = {
    Phase.idle: 'Say "Hey Jarvis"',
    Phase.connecting: 'Connecting…',
    Phase.listening: 'Listening…',
    Phase.thinking: 'Thinking…',
    Phase.speaking: 'Speaking…',
    Phase.offline: 'Jarvis is offline',
  };

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final c = controller.card;
          return Scaffold(
            body: SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(_labels[controller.phase]!, style: Theme.of(context).textTheme.headlineMedium),
                  const SizedBox(height: 24),
                  if (controller.userText.isNotEmpty) Text(controller.userText),
                  if (controller.jarvisText.isNotEmpty) Text(controller.jarvisText, style: const TextStyle(fontWeight: FontWeight.bold)),
                  if (controller.error != null) Text(controller.error!, style: const TextStyle(color: Colors.red)),
                  const Spacer(),
                  if (c != null) ...[
                    if (c.afterUntrusted) const Text('⚠ Proposed after reading email content — check recipient and text.'),
                    Text(c.summary),
                    const SizedBox(height: 12),
                    Row(children: [
                      FilledButton(onPressed: () => controller.confirm(true), child: const Text('Confirm')),
                      const SizedBox(width: 12),
                      OutlinedButton(onPressed: () => controller.confirm(false), child: const Text('Cancel')),
                    ]),
                  ],
                  const SizedBox(height: 12),
                  Row(children: [
                    FilledButton.tonal(
                        onPressed: controller.phase == Phase.idle || controller.phase == Phase.offline
                            ? () => controller.start()
                            : controller.stop,
                        child: Text(controller.phase == Phase.idle || controller.phase == Phase.offline ? 'Talk' : 'Stop')),
                  ]),
                ]),
              ),
            ),
          );
        },
      );
}

class PairingScreen extends StatefulWidget {
  const PairingScreen({super.key, required this.onSaved});
  final void Function(Config) onSaved;
  @override
  State<PairingScreen> createState() => _PairingScreenState();
}

class _PairingScreenState extends State<PairingScreen> {
  final _url = TextEditingController(), _token = TextEditingController();

  @override
  Widget build(BuildContext context) => Scaffold(
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(children: [
              TextField(controller: _url, decoration: const InputDecoration(labelText: 'Backend URL (https://…)')),
              TextField(controller: _token, decoration: const InputDecoration(labelText: 'Device token'), obscureText: true),
              const SizedBox(height: 16),
              FilledButton(
                  onPressed: () => widget.onSaved(Config(_url.text.trim(), _token.text.trim())),
                  child: const Text('Pair')),
            ]),
          ),
        ),
      );
}
