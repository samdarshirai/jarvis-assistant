import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';

import 'app.dart';
import 'wake.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  WakeService.init();
  await [Permission.microphone, Permission.notification, Permission.contacts].request();
  runApp(JarvisApp(onSessionEnded: WakeService.resume));
  // The manual Talk button must work even without a wake word (missing keyword file / AccessKey), so never crash startup.
  try {
    await WakeService.ensureRunning(); // also covers "service not running" after a reboot once the user opens the app
    await WakeService.bridgeWakeToSession();
  } catch (e) {
    debugPrint('wake word unavailable: $e');
  }
}
