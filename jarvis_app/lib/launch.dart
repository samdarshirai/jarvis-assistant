import 'package:flutter/services.dart';

import 'app.dart';

/// The assistant role (power-button long-press) starts MainActivity with an extra; the native side forwards it here.
class LaunchBridge {
  static const _ch = MethodChannel('jarvis/launch');

  static Future<void> init() async {
    _ch.setMethodCallHandler((call) async {
      if (call.method == 'start') await AppHost.startSession();
    });
    if (await _ch.invokeMethod<bool>('launchedByAssistant') ?? false) await AppHost.startSession();
  }
}
