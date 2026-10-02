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
  static Future<void> startSession({String? speakText}) async => controller?.start(speakText: speakText);
}

class JarvisApp extends StatefulWidget {
  const JarvisApp({super.key, this.onSessionEnded, this.fcmToken});
  final VoidCallback? onSessionEnded;
  final Future<String?> Function()? fcmToken;
  @override
  State<JarvisApp> createState() => _JarvisAppState();
}

class _JarvisAppState extends State<JarvisApp> {
  final _store = ConfigStore();
  Config? _config;
  bool _loaded = false;
  SessionController? _controller;

  @override
  void initState() {
    super.initState();
    _store.load().then((c) => setState(() {
          _loaded = true;
          _bind(c);
        }));
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
          );
    AppHost.controller = _controller;
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Jarvis',
        theme: ThemeData(colorSchemeSeed: Colors.indigo, useMaterial3: true),
        home: !_loaded
            ? const Scaffold(body: Center(child: CircularProgressIndicator()))
            : _config == null
                ? PairingScreen(onSaved: (c) async {
                    await _store.save(c);
                    setState(() => _bind(c));
                  })
                : SessionScreen(controller: _controller!),
      );
}
