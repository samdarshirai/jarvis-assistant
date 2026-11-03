import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';

import 'app.dart';
import 'store.dart';

String? speakTextOf(Map<String, dynamic> data) {
  final s = data['speak'];
  return s is String && s.trim().isNotEmpty ? s : null;
}

/// "Jarvis learned N things": a typed push that only opens the Learned inbox. It carries no `speak` and never talks.
bool isLearnedPush(Map<String, dynamic> data) => data['type'] == 'candidates';

class PushBridge {
  static bool ready = false;
  static Future<void>? _starting; // init runs from both onReady and the token registration: start Firebase once

  /// Push is optional: without google-services.json the app still works; only tap-to-play is unavailable.
  static Future<void> init() async {
    if ((await const ConfigStore().load()).token == null) return;
    await (_starting ??= _start());
  }

  static Stream<String> refreshes() => ready ? FirebaseMessaging.instance.onTokenRefresh : const Stream.empty();

  static Future<void> _start() async {
    try {
      await Firebase.initializeApp();
      ready = true;
    } catch (_) {
      return;
    }
    Future<void> play(RemoteMessage m) async {
      if (isLearnedPush(m.data)) {
        AppHost.openLearned(); // opens Memory; never starts a voice session
        return;
      }
      final text = speakTextOf(m.data);
      if (text != null) await AppHost.startSession(speakText: text);
    }

    final initial = await FirebaseMessaging.instance.getInitialMessage(); // app was closed, notification tapped
    if (initial != null) await play(initial);
    FirebaseMessaging.onMessageOpenedApp.listen(play); // app was in the background
    FirebaseMessaging.onMessage.listen((m) {
      if (isLearnedPush(m.data)) AppHost.learned?.refresh(); // in the foreground: only the badge moves
    });
  }

  static Future<String?> token() async => ready && (await const ConfigStore().load()).token != null
      ? FirebaseMessaging.instance.getToken() : null;
}
