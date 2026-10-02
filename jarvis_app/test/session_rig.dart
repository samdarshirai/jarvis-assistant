import 'dart:async';

import 'package:jarvis_app/session.dart';

import 'fakes.dart';

class Rig {
  FakeSocket socket = FakeSocket();
  final mic = FakeMic(), player = FakePlayer(), phone = FakePhone(), speaker = FakeSpeaker();
  int ended = 0;
  int started = 0;
  bool failConnect = false;
  Completer<void>? connectGate; // when set, connect() waits for it
  late final SessionController c = SessionController(
    connect: () async {
      await connectGate?.future;
      if (failConnect) throw Exception('down');
      return socket;
    },
    mic: mic,
    player: player,
    phone: phone,
    speaker: speaker,
    fcmToken: () async => 'fcm-1',
    onEnded: () => ended++,
    onStarted: () => started++,
  );
}
