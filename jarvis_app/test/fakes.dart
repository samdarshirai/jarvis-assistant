import 'dart:async';
import 'dart:typed_data';

import 'package:jarvis_app/protocol.dart';
import 'package:jarvis_app/session.dart';

// A cancel with no onCancel returns a root-zone future that fakeAsync cannot flush; this one completes in the caller's zone.
Future<void> _zoned() => Future<void>.value();

class FakeSocket implements VoiceSocket {
  final ctrl = StreamController<ServerEvent>(sync: true, onCancel: _zoned);
  final audio = <Uint8List>[];
  final sent = <(String, Map<String, dynamic>)>[];
  bool closed = false;
  bool hangClose = false; // close() never completes (dead network)
  @override
  Stream<ServerEvent> get events => ctrl.stream;
  @override
  void sendAudio(Uint8List pcm) => audio.add(pcm);
  @override
  void sendJson(String type, [Map<String, dynamic> fields = const {}]) => sent.add((type, fields));
  @override
  Future<void> close() async {
    closed = true;
    if (hangClose) return Completer<void>().future;
    unawaited(ctrl.close()); // awaiting it would hang in fakeAsync (root-zone done future)
  }

  List<String> get types => sent.map((e) => e.$1).toList();
}

class FakeMic implements Mic {
  final ctrl = StreamController<Uint8List>(sync: true, onCancel: _zoned);
  bool stopped = false;
  int started = 0;
  Object? startError, stopError;
  @override
  Future<Stream<Uint8List>> start() async {
    if (startError != null) throw startError!;
    started++;
    return ctrl.stream;
  }

  @override
  Future<void> stop() async {
    stopped = true;
    if (stopError != null) throw stopError!;
  }
}

class FakePlayer implements Player {
  final played = <Uint8List>[];
  int flushes = 0;
  @override
  void play(Uint8List pcm) => played.add(pcm);
  @override
  Future<void> flush() async => flushes++;
}

class FakePhone implements PhoneActions {
  final ran = <Map<String, dynamic>>[];
  final errors = <String, String>{}; // action type -> error to return
  final throwOn = <String>{}; // action types whose run() throws
  @override
  Future<String?> run(Map<String, dynamic> action) async {
    ran.add(action);
    if (throwOn.contains(action['type'])) throw StateError('boom');
    return errors[action['type']];
  }
}

class FakeSpeaker implements Speaker {
  final said = <String>[];
  @override
  Future<void> say(String text) async => said.add(text);
}

Uint8List loud() {
  final b = ByteData(640);
  for (var i = 0; i < 320; i++) {
    b.setInt16(i * 2, i.isEven ? 9000 : -9000, Endian.little);
  }
  return b.buffer.asUint8List();
}

Uint8List quiet() => Uint8List(640);
