import 'dart:ui';

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
        body: GlassBackground(
          child: SafeArea(
            child: Column(children: [
              Expanded(
                child: RefreshIndicator(
                  onRefresh: () async => dashboard?.refresh(),
                  child: ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsets.fromLTRB(20, 8, 20, 16),
                    children: [
                      Align(
                        alignment: Alignment.centerRight,
                        child: IconButton(
                          icon: const Icon(Icons.info_outline),
                          tooltip: 'What Jarvis can do',
                          onPressed: () => Navigator.of(context)
                              .push(MaterialPageRoute(builder: (_) => const CapabilitiesScreen())),
                        ),
                      ),
                      if (dashboard != null)
                        DashboardSections(
                            controller: dashboard!, onPlayBrief: (text) => controller.start(speakText: text)),
                    ],
                  ),
                ),
              ),
              VoiceBar(controller: controller),
            ]),
          ),
        ),
      );
}

class VoiceBar extends StatelessWidget {
  const VoiceBar({super.key, required this.controller});
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
          final inactive = controller.phase == Phase.idle || controller.phase == Phase.offline;
          final expanded = !inactive || c != null || controller.error != null;
          final glow = switch (controller.phase) {
            Phase.listening => accentCal,
            Phase.thinking => accentBrief,
            Phase.speaking => accentTasks,
            Phase.offline => Colors.redAccent,
            _ => accentBrief,
          };
          return ClipRRect(
            borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 20, sigmaY: 20),
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.fromLTRB(20, 14, 20, 14),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.08),
                  border: Border(top: BorderSide(color: Colors.white.withValues(alpha: 0.14))),
                ),
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  if (expanded)
                    ConstrainedBox(
                      constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.4),
                      child: SingleChildScrollView(
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          if (controller.userText.isNotEmpty) Text(controller.userText),
                          if (controller.jarvisText.isNotEmpty)
                            Text(controller.jarvisText, style: const TextStyle(fontWeight: FontWeight.bold)),
                          if (controller.error != null)
                            Text(controller.error!, style: const TextStyle(color: Colors.redAccent)),
                          if (c != null) ...[
                            const SizedBox(height: 8),
                            if (c.afterUntrusted)
                              const Text('⚠ Proposed after reading third-party content (email or web) — check recipient and text.'),
                            Text(c.summary),
                            const SizedBox(height: 12),
                            Row(children: [
                              FilledButton(onPressed: () => controller.confirm(true), child: const Text('Confirm')),
                              const SizedBox(width: 12),
                              OutlinedButton(onPressed: () => controller.confirm(false), child: const Text('Cancel')),
                            ]),
                          ],
                          const SizedBox(height: 12),
                        ]),
                      ),
                    ),
                  Row(children: [
                    Tooltip(
                      message: inactive ? 'Talk' : 'Stop',
                      child: GestureDetector(
                        onTap: inactive ? () => controller.start() : controller.stop,
                        child: AnimatedContainer(
                          duration: const Duration(milliseconds: 250),
                          width: 60,
                          height: 60,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            gradient: LinearGradient(colors: [glow, glow.withValues(alpha: 0.5)]),
                            boxShadow: [BoxShadow(color: glow.withValues(alpha: inactive ? 0.25 : 0.6), blurRadius: inactive ? 12 : 28)],
                          ),
                          child: Icon(inactive ? Icons.mic : Icons.stop, color: Colors.white),
                        ),
                      ),
                    ),
                    const SizedBox(width: 16),
                    Expanded(child: Text(_labels[controller.phase]!, style: Theme.of(context).textTheme.titleMedium)),
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
