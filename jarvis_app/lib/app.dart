import 'dart:async';

import 'package:flutter/material.dart';

import 'audio.dart';
import 'config.dart';
import 'phone.dart';
import 'session.dart';
import 'store.dart';
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
  const JarvisApp({super.key, this.onSessionEnded, this.onSessionStarted, this.fcmToken, this.store = const ConfigStore()});
  final VoidCallback? onSessionEnded;
  final VoidCallback? onSessionStarted;
  final Future<String?> Function()? fcmToken;
  final ConfigStore store;
  @override
  State<JarvisApp> createState() => _JarvisAppState();
}

class _JarvisAppState extends State<JarvisApp> {
  ConfigStore get _store => widget.store;
  Config? _config;
  bool _loaded = false;
  SessionController? _controller;

  @override
  void initState() {
    super.initState();
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
    _controller?.dispose();
    if (AppHost.controller == _controller) AppHost.attach(null);
    super.dispose();
  }

  void _bind(Config? c) {
    _config = c;
    _controller?.dispose();
    _controller = c == null
        ? null
        : SessionController(
            connect: () => WsVoiceSocket.open(c),
            mic: RecordMic(),
            player: PcmPlayer(),
            phone: AndroidPhoneActions(),
            speaker: TtsSpeaker(),
            fcmToken: widget.fcmToken,
            onEnded: widget.onSessionEnded,
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
        theme: ThemeData(colorSchemeSeed: Colors.indigo, useMaterial3: true),
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
                : SessionScreen(controller: _controller!),
      );
}
