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
    this.onStarted,
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
  final VoidCallback? onStarted; // a fresh session is starting: whoever else holds the mic (wake word) lets go of it
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
  DateTime _playEnd = DateTime.fromMillisecondsSinceEpoch(0); // when buffered Jarvis audio finishes playing
  bool _ending = false;
  int _gen = 0; // bumped by every teardown so an in-flight start() can tell it was cancelled
  Future<void> _actionsChain = Future.value();
  final List<String> _queuedSpeak = []; // push text that arrived while this session was still connecting

  void _set(Phase p) {
    phase = p;
    notifyListeners();
  }

  Future<void> start({String? speakText}) async {
    if (phase != Phase.idle && phase != Phase.offline) {
      // already in a session: a pushed brief must not be lost, so speak it on the live socket (or right after hello)
      if (speakText != null && !_ending) {
        if (phase == Phase.connecting) {
          _queuedSpeak.add(speakText);
        } else {
          try {
            _socket?.sendJson('speak', {'text': speakText});
            _arm();
          } catch (_) {} // socket closing: the session is ending anyway
        }
      }
      return;
    }
    final gen = ++_gen;
    _queuedSpeak.clear();
    _ending = false;
    error = null;
    card = null;
    userText = jarvisText = '';
    _set(Phase.connecting);
    try {
      onStarted?.call();
    } catch (_) {} // a hook failure must not stop the session
    // audible "I'm listening" cue (phone may be locked); runs during connect, finished before the mic opens
    final ack = speakText == null ? speaker.say('What can I do for you?') : null;
    VoiceSocket socket;
    try {
      socket = await connect();
    } catch (_) {
      if (gen != _gen) return; // stopped while connecting
      _set(Phase.offline);
      await speaker.say('Jarvis is offline.');
      onEnded?.call();
      return;
    }
    if (gen != _gen) {
      await _quiet(socket.close); // stopped while connecting: never go live
      return;
    }
    _socket = socket;
    try {
      _events = socket.events.listen(_onEvent, onDone: _onClosed, onError: (_) => _onClosed());
      String? token;
      try {
        token = await fcmToken?.call();
      } catch (_) {} // push is optional; never block a session on it
      if (gen != _gen) return; // teardown already closed the socket
      socket.sendJson('hello', {'fcm_token': ?token});
      if (speakText != null) socket.sendJson('speak', {'text': speakText});
      for (final t in _queuedSpeak) {
        socket.sendJson('speak', {'text': t});
      }
      _queuedSpeak.clear();
      try {
        await ack; // so the mic never hears the ack
      } catch (_) {} // TTS failure must not block the session
      if (gen != _gen) return;
      final stream = await mic.start();
      if (gen != _gen) {
        await _quiet(mic.stop);
        return;
      }
      _micSub = stream.listen(_onMic);
      _set(Phase.listening);
      _arm();
    } catch (e) {
      if (gen != _gen) return;
      error = 'Could not start Jarvis: $e';
      _ending = true;
      await _teardown();
    }
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
    _playEnd = DateTime.fromMillisecondsSinceEpoch(0);
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
          if (phase == Phase.speaking) { player.flush(); _playEnd = DateTime.fromMillisecondsSinceEpoch(0); } // the user spoke over Jarvis: drop the buffered audio
          userText = text;
        } else {
          jarvisText = text;
        }
        notifyListeners();
      case ConfirmCardEvent c:
        card = c;
        notifyListeners();
      case ClientActionsEvent(:final actions):
        _actionsChain = _actionsChain.then((_) => _runActions(actions));
      case ErrorEvent(:final message):
        error = message;
        notifyListeners();
      case AudioEvent(:final pcm):
        final now = DateTime.now();
        final start = _playEnd.isAfter(now) ? _playEnd : now;
        _playEnd = start.add(Duration(microseconds: pcm.length * 1000000 ~/ 32000)); // 16 kHz mono pcm16
        player.play(pcm);
    }
    _arm();
  }

  Future<void> _runActions(List<Map<String, dynamic>> actions) async {
    for (final a in actions) {
      String? err;
      try {
        err = await phone.run(a);
      } catch (e) {
        err = 'Could not run ${a['type']}: $e';
      }
      if (err != null) {
        error = err;
        notifyListeners();
      }
    }
  }

  void _arm() {
    _timer?.cancel();
    if (phase == Phase.listening) {
      // Server flips to listening once audio is sent, not played: count silence from when playback ends.
      final left = _playEnd.difference(DateTime.now());
      _timer = Timer((card != null ? confirmWait : silence) + (left.isNegative ? Duration.zero : left), _end);
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
    if (_ending || phase == Phase.idle || phase == Phase.offline) return;
    _ending = true;
    _timer?.cancel();
    try {
      _socket?.sendJson('bye');
    } catch (_) {} // a dead socket must not block teardown
    await _teardown();
  }

  void _onClosed() {
    if (_ending || phase == Phase.idle) return;
    _ending = true;
    speaker.say('Jarvis connection lost.');
    _teardown(closeSocket: false);
  }

  Future<void> _quiet(FutureOr<void> Function() f) async {
    try {
      await f();
    } catch (_) {} // cleanup is best effort; teardown must always reach idle
  }

  Future<void> _teardown({bool closeSocket = true}) async {
    _gen++;
    _timer?.cancel();
    final socket = _socket;
    _socket = null;
    try {
      await _quiet(() => _micSub?.cancel());
      await _quiet(mic.stop);
      await _quiet(player.flush);
      await _quiet(() => _events?.cancel());
      if (closeSocket && socket != null) {
        await _quiet(() => socket.close().timeout(const Duration(seconds: 2)));
      }
    } finally {
      card = null;
      _set(Phase.idle);
      onEnded?.call();
    }
  }
}
