import 'package:jarvis_app/session.dart';

import 'fakes.dart';

class Rig {
  final socket = FakeSocket(), mic = FakeMic(), player = FakePlayer(), phone = FakePhone(), speaker = FakeSpeaker();
  int ended = 0;
  bool failConnect = false;
  late final SessionController c = SessionController(
    connect: () async {
      if (failConnect) throw Exception('down');
      return socket;
    },
    mic: mic,
    player: player,
    phone: phone,
    speaker: speaker,
    fcmToken: () async => 'fcm-1',
    onEnded: () => ended++,
  );
}
