import 'package:flutter/material.dart';

import 'alarms_screen.dart';
import 'api.dart';
import 'brief_screen.dart';
import 'calendar_screen.dart';
import 'capabilities.dart';
import 'chat_screen.dart';
import 'mail_screen.dart';
import 'notes_screen.dart';
import 'orb.dart';
import 'phone.dart';
import 'tasks_screen.dart';
import 'config.dart';
import 'confirm_card.dart';
import 'dashboard.dart';
import 'dashboard_cards.dart';
import 'session.dart';
import 'theme.dart';

class SessionScreen extends StatelessWidget {
  const SessionScreen({super.key, required this.controller, this.dashboard, this.api, this.alarm, this.runPhoneActions});
  final SessionController controller;
  final DashboardController? dashboard;
  final ApiClient? api; // null: detail screens are not reachable (tests, unpaired)
  final Future<DateTime?> Function()? alarm;
  final Future<void> Function(List<Map<String, dynamic>> actions)? runPhoneActions;

  void _push(BuildContext context, Widget screen) =>
      Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => screen));

  void _open(BuildContext context, HomeTarget t) {
    final a = api;
    if (a == null) return;
    // the voice orb is the "ask Jarvis" path: leave the detail screen, then start a session
    void ask() {
      Navigator.of(context).pop();
      controller.start();
    }

    _push(
        context,
        switch (t) {
          HomeTarget.calendar => CalendarScreen(api: a, onAskJarvis: ask),
          HomeTarget.mail => MailScreen(api: a, onReplyByVoice: ask),
          HomeTarget.notes => NotesScreen(api: a, onAskJarvis: ask),
          HomeTarget.tasks => TasksScreen(api: a),
          HomeTarget.alarms => AlarmsScreen(alarm: alarm ?? (() async => dashboard?.nextAlarm), onAskJarvis: ask),
        });
  }

  void _chat(BuildContext context, {String? initialText}) {
    final a = api;
    if (a == null) return;
    _push(
        context,
        ChatScreen(
            api: a,
            initialText: initialText,
            onVoice: () {
              Navigator.of(context).pop();
              controller.start();
            },
            runPhoneActions: runPhoneActions ?? (_) async {}));
  }

  void _brief(BuildContext context, String text) => _push(
      context, BriefScreen(brief: text, speak: (t) => controller.start(speakText: t), stop: controller.stop));

  @override
  Widget build(BuildContext context) => Scaffold(
        body: CocoaBackground(
          child: SafeArea(
            child: ListenableBuilder(
              listenable: controller,
              builder: (context, _) {
                final idle = controller.phase == Phase.idle || controller.phase == Phase.offline;
                final voice = !idle || controller.card != null || controller.error != null;
                return voice
                    ? _VoiceView(
                        controller: controller,
                        onKeyboard: api == null
                            ? null
                            : (text) {
                                controller.stop();
                                _chat(context, initialText: text);
                              })
                    : _home(context);
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
                      onPlayBrief: (text) => api == null ? controller.start(speakText: text) : _brief(context, text),
                      onOpen: (t) => _open(context, t),
                      onOpenEmail: openEmailInGmail,
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
              if (api != null) ...[
                Tooltip(
                  message: 'Chat',
                  child: GestureDetector(
                    onTap: () => _chat(context),
                    child: Container(
                      width: 54,
                      height: 54,
                      decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: darkPill.withValues(alpha: 0.6),
                          border: Border.all(color: Colors.white.withValues(alpha: 0.1))),
                      child: const Icon(Icons.chat_bubble, color: cream),
                    ),
                  ),
                ),
                const SizedBox(width: 16),
              ],
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
  const _VoiceView({required this.controller, this.onKeyboard});
  final SessionController controller;
  /// Opens typed chat (optionally sending [text] first); null when the REST api is unavailable.
  final void Function(String? text)? onKeyboard;

  static const _status = {
    Phase.idle: '',
    Phase.connecting: 'Connecting…',
    Phase.listening: 'Listening… What can I do for you?',
    Phase.thinking: 'Thinking…',
    Phase.speaking: 'Speaking…',
    Phase.offline: 'Jarvis is offline',
  };

  static const _chips = [
    (Icons.calendar_month, 'Schedule a meeting', 'Schedule a meeting'),
    (Icons.mail_outline, 'Write an email', 'Write an email'),
    (Icons.alarm, 'Set an alarm', 'Set an alarm'),
    (Icons.edit_note, 'Take a note', 'Take a note'),
    (Icons.schedule, "What's after lunch?", "What's on my calendar after lunch?"),
    (Icons.check_box_outlined, 'Add a task', 'Add a task'),
  ];

  Widget _round(IconData icon, String tip, VoidCallback? onTap) => Tooltip(
        message: tip,
        child: GestureDetector(
          onTap: onTap,
          child: Container(
            width: 52,
            height: 52,
            decoration: BoxDecoration(shape: BoxShape.circle, color: darkPill.withValues(alpha: 0.5)),
            child: Icon(icon, color: cream),
          ),
        ),
      );

  Widget _chip((IconData, String, String) c) => GestureDetector(
        onTap: () => onKeyboard!(c.$3),
        child: Container(
          padding: const EdgeInsets.fromLTRB(7, 7, 14, 7),
          decoration: BoxDecoration(
              color: darkPill.withValues(alpha: 0.35),
              borderRadius: BorderRadius.circular(18),
              border: Border.all(color: Colors.white.withValues(alpha: 0.06))),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Container(
              width: 32,
              height: 32,
              decoration: BoxDecoration(color: Colors.white.withValues(alpha: 0.07), borderRadius: BorderRadius.circular(11)),
              child: Icon(c.$1, size: 18, color: amber),
            ),
            const SizedBox(width: 9),
            Text(c.$2, style: const TextStyle(color: cream, fontSize: 14)),
          ]),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final c = controller.card, phase = controller.phase;
    final speed = switch (phase) { Phase.thinking => 3.0, Phase.speaking => 1.6, _ => 1.0 };
    final showChips = onKeyboard != null && phase == Phase.listening && controller.userText.isEmpty && c == null;
    return Column(children: [
      Row(children: [
        IconButton(icon: const Icon(Icons.arrow_back, color: cream), tooltip: 'Back', onPressed: controller.stop),
        const Expanded(
            child: Text('Talk with Jarvis',
                overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600, color: cream))),
      ]),
      Expanded(
        child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(horizontal: 24),
          child: Column(children: [
            const SizedBox(height: 8),
            AnimatedOrb(size: 240, speed: speed),
            const SizedBox(height: 12),
            Text(_status[phase]!,
                textAlign: TextAlign.center, style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w500, color: cream)),
            const SizedBox(height: 16),
            if (showChips) Wrap(alignment: WrapAlignment.center, spacing: 8, runSpacing: 8, children: [for (final q in _chips) _chip(q)]),
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
      if (c != null)
        ConfirmCard(
            summary: c.summary,
            afterUntrusted: c.afterUntrusted,
            onConfirm: () => controller.confirm(true),
            onCancel: () => controller.confirm(false)),
      Padding(
        padding: const EdgeInsets.only(bottom: 20, top: 8),
        child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          _round(Icons.keyboard, 'Type instead', onKeyboard == null ? null : () => onKeyboard!(null)),
          const SizedBox(width: 34),
          AmberOrb(phase: phase, onTap: controller.stop, size: 80),
          const SizedBox(width: 34),
          _round(Icons.close, 'Close', controller.stop),
        ]),
      ),
    ]);
  }
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
