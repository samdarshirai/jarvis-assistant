import 'dart:async';

import 'package:flutter/foundation.dart';

import 'protocol.dart';
import 'vad.dart';

enum Phase { idle, connecting, listening, thinking, speaking, offline }

abstract class VoiceSocket {
  Stream<ServerEvent> get events;
  void sendAudio(Uint8List pcm);
  void sendJson(String type, [Map<String, dynamic> fields]);
  Future<void> close();
}

abstract class Mic {
  Future<Stream<Uint8List>> start();
  Future<void> stop();
}

abstract class Player {
  void play(Uint8List pcm);
  Future<void> flush();
}

abstract class PhoneActions {
  /// Runs one client action; returns an error message, or null on success.
  Future<String?> run(Map<String, dynamic> action);
}

abstract class Speaker {
  Future<void> say(String text);
}

class SessionController extends ChangeNotifier {
  SessionController({
    required this.connect,
    required this.mic,
    required this.player,
    required this.phone,
    required this.speaker,
    this.fcmToken,
    this.onEnded,
    Vad? vad,
    this.silence = const Duration(seconds: 8),
    this.confirmWait = const Duration(seconds: 30),
  }) : vad = vad ?? Vad();

  final Future<VoiceSocket> Function() connect;
  final Mic mic;
  final Player player;
  final PhoneActions phone;
  final Speaker speaker;
  final Future<String?> Function()? fcmToken;
  final VoidCallback? onEnded;
  final Vad vad;
  final Duration silence, confirmWait;

  Phase phase = Phase.idle;
  String userText = '', jarvisText = '';
  ConfirmCardEvent? card;
  String? error;

  VoiceSocket? _socket;
  StreamSubscription<ServerEvent>? _events;
  StreamSubscription<Uint8List>? _micSub;
  Timer? _timer;
  bool _ending = false;

  void _set(Phase p) {
    phase = p;
    notifyListeners();
  }

  Future<void> start({String? speakText}) async {
    if (phase != Phase.idle && phase != Phase.offline) return;
    _ending = false;
    error = null;
    card = null;
    userText = jarvisText = '';
    _set(Phase.connecting);
    try {
      _socket = await connect();
    } catch (_) {
      _set(Phase.offline);
      await speaker.say('Jarvis is offline.');
      onEnded?.call();
      return;
    }
    _events = _socket!.events.listen(_onEvent, onDone: _onClosed, onError: (_) => _onClosed());
    String? token;
    try {
      token = await fcmToken?.call();
    } catch (_) {} // push is optional; never block a session on it
    _socket!.sendJson('hello', {'fcm_token': ?token});
    if (speakText != null) _socket!.sendJson('speak', {'text': speakText});
    _micSub = (await mic.start()).listen(_onMic);
    _set(Phase.listening);
    _arm();
  }

  void _onMic(Uint8List pcm) {
    _socket?.sendAudio(pcm);
    if (phase == Phase.speaking) {
      if (vad.feed(pcm)) _bargeIn();
    } else if (phase == Phase.listening && vad.feed(pcm)) {
      _arm(); // the user is talking: keep the session alive
    }
  }

  void _bargeIn() {
    player.flush();
    _socket?.sendJson('cancel');
    _set(Phase.listening);
    _arm();
  }

  void _onEvent(ServerEvent e) {
    switch (e) {
      case StateEvent(:final state):
        const map = {'listening': Phase.listening, 'thinking': Phase.thinking, 'speaking': Phase.speaking};
        if (map[state] != null) _set(map[state]!);
      case TranscriptEvent(:final role, :final text):
        if (role == 'user') {
          if (phase == Phase.speaking) player.flush(); // the user spoke over Jarvis: drop the buffered audio
          userText = text;
        } else {
          jarvisText = text;
        }
        notifyListeners();
      case ConfirmCardEvent c:
        card = c;
        notifyListeners();
      case ClientActionsEvent(:final actions):
        _runActions(actions);
      case ErrorEvent(:final message):
        error = message;
        notifyListeners();
      case AudioEvent(:final pcm):
        player.play(pcm);
    }
    _arm();
  }

  Future<void> _runActions(List<Map<String, dynamic>> actions) async {
    for (final a in actions) {
      final err = await phone.run(a);
      if (err != null) {
        error = err;
        notifyListeners();
      }
    }
  }

  void _arm() {
    _timer?.cancel();
    if (phase == Phase.listening) {
      _timer = Timer(card != null ? confirmWait : silence, _end);
    }
  }

  void confirm(bool yes) {
    final c = card;
    if (c == null) return;
    _socket?.sendJson('confirm', {'decision': yes ? 'yes' : 'no', 'interrupt_id': c.interruptId});
    card = null;
    notifyListeners();
    _arm();
  }

  void stop() => _end();

  Future<void> _end() async {
    if (_ending || phase == Phase.idle) return;
    _ending = true;
    _timer?.cancel();
    _socket?.sendJson('bye');
    await _teardown();
  }

  void _onClosed() {
    if (_ending || phase == Phase.idle) return;
    _ending = true;
    speaker.say('Jarvis connection lost.');
    _teardown(closeSocket: false);
  }

  Future<void> _teardown({bool closeSocket = true}) async {
    _timer?.cancel();
    await _micSub?.cancel();
    await mic.stop();
    await player.flush();
    await _events?.cancel();
    if (closeSocket) await _socket?.close();
    _socket = null;
    card = null;
    _set(Phase.idle);
    onEnded?.call();
  }
}
