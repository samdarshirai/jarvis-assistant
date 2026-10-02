import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';

import 'app.dart';
import 'launch.dart';
import 'push.dart';
import 'wake.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  WakeService.init();
  await [Permission.microphone, Permission.notification, Permission.contacts].request();
  try {
    await PushBridge.init(); // optional; never blocks startup
  } catch (e) {
    debugPrint('push unavailable: $e');
  }
  runApp(JarvisApp(onSessionEnded: WakeService.resume, fcmToken: PushBridge.token));
  // The manual Talk button must work even without a wake word (missing keyword file / AccessKey), so never crash startup.
  try {
    await WakeService.ensureRunning(); // also covers "service not running" after a reboot once the user opens the app
    await WakeService.bridgeWakeToSession();
  } catch (e) {
    debugPrint('wake word unavailable: $e');
  }
  try {
    await LaunchBridge.init();
  } catch (e) {
    debugPrint('assistant launch unavailable: $e');
  }
}
