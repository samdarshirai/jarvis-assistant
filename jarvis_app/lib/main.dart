import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';

import 'app.dart';
import 'launch.dart';
import 'push.dart';
import 'wake.dart';

bool _bridgesStarted = false;

Future<void> _ready(bool voice) async {
  await Permission.notification.request();
  try { await PushBridge.init(); } catch (_) {}
  if (!voice) return;
  await [Permission.microphone, Permission.contacts, Permission.bluetoothConnect].request();
  try { await WakeService.ensureRunning(); } catch (_) {}
  if (_bridgesStarted) return;
  _bridgesStarted = true;
  try { await WakeService.bridgeWakeToSession(); } catch (_) {}
  try { await LaunchBridge.init(); } catch (_) {}
}

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  WakeService.init();
  runApp(JarvisApp(onSessionEnded: WakeService.resume, onSessionStarted: WakeService.pause,
      fcmToken: PushBridge.token, pushInit: PushBridge.init, fcmRefresh: PushBridge.refreshes, onReady: _ready, stopWake: WakeService.stop,
      onWakeChanged: (on) async {
        if (on) {
          await WakeService.ensureRunning();
        } else {
          await WakeService.stop();
        }
        WakeService.refreshGate();
      }));
}
