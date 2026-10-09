import 'dart:async';

import 'package:flutter/material.dart';

import 'alarm.dart';
import 'api.dart';
import 'audio.dart';
import 'config.dart';
import 'dashboard.dart';
import 'phone.dart';
import 'session.dart';
import 'store.dart';
import 'theme.dart';
import 'ui.dart';
import 'ws_socket.dart';

/// Lets the wake-word, assistant and push entry points start a session on the one controller.
class AppHost {
  static SessionController? controller;
  static DateTime Function() now = DateTime.now; // injectable for tests
  static const pendingTtl = Duration(seconds: 30);
  static ({String? speakText, DateTime at})? _pending;

  /// Cold-start entry points can fire before the controller exists: remember the last request and run it on attach.
  static Future<void> startSession({String? speakText}) async {
    final c = controller;
    if (c != null) return c.start(speakText: speakText);
    _pending = (speakText: speakText, at: now());
  }

  /// Sets the controller (null clears it and any pending request) and drains a fresh pending request exactly once.
  static void attach(SessionController? c) {
    controller = c;
    if (c == null) {
      _pending = null;
      return;
    }
    final p = _pending;
    _pending = null;
    if (p == null || now().difference(p.at) > pendingTtl) return;
    scheduleMicrotask(() {
      if (controller == c) c.start(speakText: p.speakText);
    });
  }
}

class JarvisApp extends StatefulWidget {
  const JarvisApp({super.key, this.onSessionEnded, this.onSessionStarted, this.fcmToken, this.store = const ConfigStore(), this.dashboardFetch, this.alarm});
  final VoidCallback? onSessionEnded;
  final VoidCallback? onSessionStarted;
  final Future<String?> Function()? fcmToken;
  final ConfigStore store;
  final Future<DashboardData> Function(Config)? dashboardFetch;
  final Future<DateTime?> Function()? alarm;
  @override
  State<JarvisApp> createState() => _JarvisAppState();
}

class _JarvisAppState extends State<JarvisApp> with WidgetsBindingObserver {
  ConfigStore get _store => widget.store;
  Config? _config;
  bool _loaded = false;
  SessionController? _controller;
  DashboardController? _dashboard;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _store.load().catchError((Object _) => null).then((c) {
      if (!mounted) return;
      setState(() {
        _loaded = true;
        _bind(c);
      });
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _controller?.dispose(); // before the dashboard: its onEnded refreshes it
    _dashboard?.dispose();
    if (AppHost.controller == _controller) AppHost.attach(null);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _dashboard?.refresh();
  }

  void _bind(Config? c) {
    _config = c;
    _controller?.dispose(); // before the dashboard: its onEnded refreshes it
    _dashboard?.dispose();
    _dashboard = c == null
        ? null
        : (DashboardController(fetch: () => (widget.dashboardFetch ?? fetchDashboard)(c), alarm: widget.alarm ?? nextAlarm)
          ..refresh());
    _controller = c == null
        ? null
        : SessionController(
            connect: () => WsVoiceSocket.open(c),
            mic: RecordMic(),
            player: PcmPlayer(),
            phone: AndroidPhoneActions(),
            speaker: TtsSpeaker(),
            fcmToken: widget.fcmToken,
            onEnded: () {
              widget.onSessionEnded?.call();
              _dashboard?.refresh(); // a spoken "add task" should show up
            },
            onStarted: widget.onSessionStarted,
          );
    // unpaired: no controller yet, but keep any pending cold-start request until pairing creates one
    if (_controller == null) {
      AppHost.controller = null;
    } else {
      AppHost.attach(_controller);
    }
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Jarvis',
        theme: jarvisTheme(),
        home: !_loaded
            ? const Scaffold(body: Center(child: CircularProgressIndicator()))
            : _config == null
                ? PairingScreen(onSaved: (c) async {
                    try {
                      await _store.save(c);
                    } catch (_) {} // not persisted: works this run, re-pair next launch
                    if (!mounted) return;
                    setState(() => _bind(c));
                  })
                : SessionScreen(
                    controller: _controller!,
                    dashboard: _dashboard,
                    api: ApiClient(_config!),
                    alarm: widget.alarm ?? nextAlarm,
                    runPhoneActions: (actions) async {
                      for (final a in actions) {
                        await AndroidPhoneActions().run(a);
                      }
                    }),
      );
}
