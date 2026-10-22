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
import 'memory_screen.dart';
import 'settings_screen.dart';
import 'shopping_screen.dart';
import 'tasks_screen.dart';
import 'confirm_card.dart';
import 'dashboard.dart';
import 'dashboard_cards.dart';
import 'session.dart';
import 'theme.dart';

class SessionScreen extends StatelessWidget {
  const SessionScreen({super.key, required this.controller, this.dashboard, this.displayName, this.api, this.alarm, this.runPhoneActions,
    this.startSignIn, this.voiceReady = true, this.onSettingsReturn, this.onNeedsSetup, this.open = openInBrowser});
  final SessionController controller;
  final DashboardController? dashboard;
  final String? displayName;
  final ApiClient? api; // null: detail screens are not reachable (tests, unpaired)
  final Future<DateTime?> Function()? alarm;
  final Future<void> Function(List<Map<String, dynamic>> actions)? runPhoneActions;
  final Future<Uri> Function()? startSignIn;
  final bool voiceReady;
  final VoidCallback? onSettingsReturn;
  final VoidCallback? onNeedsSetup; // chat got 409 needs_setup: lock until /me says the LLM key is back
  final Future<void> Function(Uri) open;

  void _talk(BuildContext context, {String? text}) {
    if (voiceReady) {
      controller.start(speakText: text);
    } else {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('Add your Deepgram key in Settings > API keys to enable voice.')));
    }
  }

  void _push(BuildContext context, Widget screen) =>
      Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => screen));

  void _open(BuildContext context, HomeTarget t) {
    final a = api;
    if (a == null) return;
    // the voice orb is the "ask Jarvis" path: leave the detail screen, then start a session
    void ask() {
      Navigator.of(context).pop();
      _talk(context);
    }

    _push(
        context,
        switch (t) {
          HomeTarget.calendar => CalendarScreen(api: a, onAskJarvis: ask),
          HomeTarget.mail => MailScreen(api: a, onReplyByVoice: ask),
          HomeTarget.notes => NotesScreen(api: a, onAskJarvis: ask),
          HomeTarget.tasks => TasksScreen(api: a, onAskJarvis: ask),
          HomeTarget.shopping => ShoppingScreen(api: a, onAskJarvis: ask),
          HomeTarget.memory => MemoryScreen(api: a, onAskJarvis: ask),
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
            onNeedsSetup: onNeedsSetup,
            onVoice: () {
              Navigator.of(context).pop();
              _talk(context);
            },
            runPhoneActions: runPhoneActions ?? (_) async {}));
  }

  void _settings(BuildContext context) {
    final a = api;
    if (a == null) return;
    _push(
        context,
        SettingsScreen(
            api: a,
            startAuth: () async {
              if (startSignIn == null) throw StateError('Sign-in unavailable');
              return (await startSignIn!()).toString();
            },
            open: open,
            onReturn: () { dashboard?.refresh(); onSettingsReturn?.call(); }));
  }

  void _brief(BuildContext context, String text) => _push(
      context, BriefScreen(brief: text, speak: (t) => _talk(context, text: t), stop: controller.stop));

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

  Widget _sideButton(IconData icon, String tip, VoidCallback onTap) => Tooltip(
        message: tip,
        child: GestureDetector(
          onTap: onTap,
          child: Container(
            width: 54,
            height: 54,
            decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: darkPill.withValues(alpha: 0.6),
                border: Border.all(color: Colors.white.withValues(alpha: 0.1))),
            child: Icon(icon, color: cream),
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
                      displayName: displayName,
                      onPlayBrief: (text) => api == null ? _talk(context, text: text) : _brief(context, text),
                      onOpen: (t) => _open(context, t),
                      onOpenEmail: openEmailInGmail,
                      onSettings: api == null ? null : () => _settings(context),
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
            Text(voiceReady ? 'Say "Hey Jarvis"' : 'Add a Deepgram key in Settings to enable voice',
                style: const TextStyle(fontSize: 12, color: mutedText)),
            const SizedBox(height: 8),
            Row(mainAxisAlignment: MainAxisAlignment.center, children: [
              api == null ? const SizedBox(width: 54) : _sideButton(Icons.chat_bubble, 'Chat', () => _chat(context)),
              const SizedBox(width: 16),
              OrbButton(onTap: () => _talk(context)),
              const SizedBox(width: 16),
              _sideButton(Icons.apps, 'What Jarvis can do',
                  () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const CapabilitiesScreen()))),
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
            child: Icon(icon, size: 22, color: cream),
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
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
        child: Row(children: [
          RoundButton(icon: Icons.arrow_back, tooltip: 'Back', onTap: controller.stop),
          const Expanded(
              child: Text('Talk with Jarvis',
                  textAlign: TextAlign.center, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 16, color: cream))),
          const SizedBox(width: 46),
        ]),
      ),
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
              Text(controller.userText,
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: blush, fontSize: 34, fontWeight: FontWeight.w500, height: 1.22, letterSpacing: -0.5)),
            if (controller.jarvisText.isNotEmpty)
              Text(controller.jarvisText,
                  textAlign: TextAlign.center, style: const TextStyle(fontSize: 18, color: cream, height: 1.4)),
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
