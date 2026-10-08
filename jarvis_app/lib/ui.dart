import 'package:flutter/material.dart';

import 'capabilities.dart';
import 'config.dart';
import 'dashboard.dart';
import 'dashboard_cards.dart';
import 'session.dart';
import 'theme.dart';

class SessionScreen extends StatelessWidget {
  const SessionScreen({super.key, required this.controller, this.dashboard});
  final SessionController controller;
  final DashboardController? dashboard;

  @override
  Widget build(BuildContext context) => Scaffold(
        body: CocoaBackground(
          child: SafeArea(
            child: ListenableBuilder(
              listenable: controller,
              builder: (context, _) {
                final idle = controller.phase == Phase.idle || controller.phase == Phase.offline;
                final voice = !idle || controller.card != null || controller.error != null;
                return voice ? _VoiceView(controller: controller) : _home(context);
              },
            ),
          ),
        ),
      );

  Widget _home(BuildContext context) => Stack(children: [
        Positioned.fill(
          child: RefreshIndicator(
            onRefresh: () async => dashboard?.refresh(),
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 130),
              children: [
                if (dashboard != null)
                  DashboardSections(
                      controller: dashboard!,
                      onPlayBrief: (text) => controller.start(speakText: text),
                      now: DateTime.now),
              ],
            ),
          ),
        ),
        const Positioned(
          left: 0,
          right: 0,
          bottom: 0,
          height: 190,
          child: IgnorePointer(
            child: DecoratedBox(
              decoration: BoxDecoration(
                  gradient: LinearGradient(
                      begin: Alignment.topCenter, end: Alignment.bottomCenter, colors: [Color(0x00211715), cocoaBottom])),
            ),
          ),
        ),
        Positioned(
          left: 0,
          right: 0,
          bottom: 20,
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Text('Say "Hey Jarvis"', style: TextStyle(fontSize: 12, color: mutedText)),
            const SizedBox(height: 8),
            Row(mainAxisAlignment: MainAxisAlignment.center, children: [
              Tooltip(
                message: 'What Jarvis can do',
                child: GestureDetector(
                  onTap: () => Navigator.of(context)
                      .push(MaterialPageRoute(builder: (_) => const CapabilitiesScreen())),
                  child: Container(
                    width: 54,
                    height: 54,
                    decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: darkPill.withValues(alpha: 0.6),
                        border: Border.all(color: Colors.white.withValues(alpha: 0.1))),
                    child: const Icon(Icons.apps, color: cream),
                  ),
                ),
              ),
              const SizedBox(width: 16),
              Container(
                padding: const EdgeInsets.all(3),
                decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: amber, width: 1.5)),
                child: AmberOrb(phase: controller.phase, onTap: () => controller.start()),
              ),
            ]),
          ]),
        ),
      ]);
}

class _VoiceView extends StatelessWidget {
  const _VoiceView({required this.controller});
  final SessionController controller;

  static const _status = {
    Phase.idle: '',
    Phase.connecting: 'Connecting…',
    Phase.listening: 'Listening… What can I do for you?',
    Phase.thinking: 'Thinking…',
    Phase.speaking: 'Speaking…',
    Phase.offline: 'Jarvis is offline',
  };

  @override
  Widget build(BuildContext context) {
    final c = controller.card;
    return Column(children: [
      Row(children: [
        IconButton(icon: const Icon(Icons.arrow_back, color: cream), tooltip: 'Back', onPressed: controller.stop),
        const Text('Talk with Jarvis', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600, color: cream)),
      ]),
      Expanded(
        child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(horizontal: 24),
          child: Column(children: [
            const SizedBox(height: 16),
            AmberOrb(phase: controller.phase, onTap: controller.stop, size: 140),
            const SizedBox(height: 24),
            Text(_status[controller.phase]!,
                textAlign: TextAlign.center, style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w500, color: cream)),
            const SizedBox(height: 16),
            if (controller.userText.isNotEmpty)
              Text(controller.userText, textAlign: TextAlign.center, style: const TextStyle(color: cream)),
            if (controller.jarvisText.isNotEmpty)
              Text(controller.jarvisText,
                  textAlign: TextAlign.center, style: const TextStyle(fontWeight: FontWeight.bold, color: cream)),
            if (controller.error != null)
              Text(controller.error!, textAlign: TextAlign.center, style: const TextStyle(color: danger)),
          ]),
        ),
      ),
      if (c != null) _confirm(context, c),
    ]);
  }

  Widget _confirm(BuildContext context, dynamic c) => Container(
        margin: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(color: darkPill, borderRadius: BorderRadius.circular(28)),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('Needs your OK', style: TextStyle(fontSize: 12, color: mutedText)),
          const SizedBox(height: 8),
          ConstrainedBox(
            constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.25),
            child: SingleChildScrollView(
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(color: const Color(0xFF1F1917), borderRadius: BorderRadius.circular(20)),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(c.summary as String, style: const TextStyle(color: cream)),
                  if (c.afterUntrusted as bool) ...[
                    const SizedBox(height: 10),
                    Container(
                      padding: const EdgeInsets.all(10),
                      decoration: BoxDecoration(
                          color: amber.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(12)),
                      child: const Text(
                          '⚠ Proposed after reading third-party content (email or web) — check recipient and text.',
                          style: TextStyle(color: amberLight, fontSize: 13)),
                    ),
                  ],
                ]),
              ),
            ),
          ),
          const SizedBox(height: 12),
          Row(children: [
            Expanded(
              child: TextButton(
                style: TextButton.styleFrom(
                    backgroundColor: const Color(0xFF1F1917), foregroundColor: danger, minimumSize: const Size(0, 48)),
                onPressed: () => controller.confirm(false),
                child: const Text('Cancel'),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: FilledButton(
                style: FilledButton.styleFrom(
                    backgroundColor: amber, foregroundColor: amberInk, minimumSize: const Size(0, 48)),
                onPressed: () => controller.confirm(true),
                child: const Text('Confirm'),
              ),
            ),
          ]),
        ]),
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
        body: CocoaBackground(
          child: SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(children: [
                Align(
                    alignment: Alignment.centerLeft,
                    child: Text('Pair Jarvis', style: Theme.of(context).textTheme.headlineMedium)),
                const SizedBox(height: 16),
                TextField(controller: _url, decoration: const InputDecoration(labelText: 'Backend URL (https://…)')),
                TextField(controller: _token, decoration: const InputDecoration(labelText: 'Device token'), obscureText: true),
                const SizedBox(height: 16),
                FilledButton(
                    style: FilledButton.styleFrom(backgroundColor: amber, foregroundColor: amberInk),
                    onPressed: () => widget.onSaved(Config(_url.text.trim(), _token.text.trim())),
                    child: const Text('Pair')),
              ]),
            ),
          ),
        ),
      );
}
