import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';

import 'app.dart';

String? speakTextOf(Map<String, dynamic> data) {
  final s = data['speak'];
  return s is String && s.trim().isNotEmpty ? s : null;
}

class PushBridge {
  static bool ready = false;

  /// Push is optional: without google-services.json the app still works; only tap-to-play is unavailable.
  static Future<void> init() async {
    try {
      await Firebase.initializeApp();
      ready = true;
    } catch (_) {
      return;
    }
    Future<void> play(RemoteMessage m) async {
      final text = speakTextOf(m.data);
      if (text != null) await AppHost.startSession(speakText: text);
    }

    final initial = await FirebaseMessaging.instance.getInitialMessage(); // app was closed, notification tapped
    if (initial != null) await play(initial);
    FirebaseMessaging.onMessageOpenedApp.listen(play); // app was in the background
  }

  static Future<String?> token() async => ready ? FirebaseMessaging.instance.getToken() : null;
}
