import 'dart:async';

import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:porcupine_flutter/porcupine_manager.dart';

import 'app.dart';

const picovoiceKey = String.fromEnvironment('PICOVOICE_ACCESS_KEY');
const keywordAsset = 'assets/hey_jarvis_android.ppn'; // trained in the Picovoice console, see ACCEPTANCE.md
const _wakeKey = 'wake';

bool wakeIsFresh(int? savedMillis, DateTime now) {
  if (savedMillis == null) return false;
  final age = now.millisecondsSinceEpoch - savedMillis;
  return age >= 0 && age <= 15000;
}

@pragma('vm:entry-point')
void startCallback() => FlutterForegroundTask.setTaskHandler(WakeTaskHandler());

/// Runs Porcupine in the foreground service so the wake word works with the screen off (FR-14).
class WakeTaskHandler extends TaskHandler {
  PorcupineManager? _pm;
  bool _listening = false;

  /// Never throws: a missing keyword file or AccessKey must not leave an unhandled error and a notification that lies.
  Future<void> _listen() async {
    if (_listening) return; // resume can arrive while Porcupine already runs (e.g. after a manual Talk session)
    try {
      _pm ??= await PorcupineManager.fromKeywordPaths(picovoiceKey, [keywordAsset], _onWake, sensitivities: [0.6]);
      await _pm!.start();
      _listening = true;
    } catch (e) {
      await FlutterForegroundTask.updateService(
          notificationText: 'Wake word unavailable - use the Talk button in the app ($e)');
    }
  }

  Future<void> _pause() async {
    if (!_listening) return;
    _listening = false;
    try {
      await _pm?.stop(); // any session (wake, Talk, assistant, push) takes the microphone
    } catch (_) {}
  }

  Future<void> _onWake(int index) async {
    await _pause();
    await FlutterForegroundTask.saveData(key: _wakeKey, value: DateTime.now().millisecondsSinceEpoch);
    FlutterForegroundTask.sendDataToMain('wake');
    final n = FlutterLocalNotificationsPlugin();
    await n.initialize(settings: const InitializationSettings(android: AndroidInitializationSettings('@mipmap/ic_launcher')));
    // Full-screen intent wakes the screen; if Android denies it, this degrades to a heads-up notification.
    await n.show(
      id: 1,
      title: 'Jarvis',
      body: 'Listening…',
      notificationDetails: const NotificationDetails(
          android: AndroidNotificationDetails('wake', 'Wake word',
              importance: Importance.max,
              priority: Priority.high,
              fullScreenIntent: true,
              category: AndroidNotificationCategory.call)),
    );
  }

  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) => _listen();
  @override
  void onRepeatEvent(DateTime timestamp) {}
  @override
  Future<void> onDestroy(DateTime timestamp, bool isTimeout) async => _pm?.delete();
  @override
  void onReceiveData(Object data) {
    if (data == 'resume') _listen();
    if (data == 'pause') _pause();
  }
}

class WakeService {
  static void init() {
    FlutterForegroundTask.initCommunicationPort();
    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'jarvis_wake_service',
        channelName: 'Jarvis wake word',
        channelDescription: 'Listening for "Hey Jarvis"',
      ),
      iosNotificationOptions: const IOSNotificationOptions(),
      foregroundTaskOptions: ForegroundTaskOptions(eventAction: ForegroundTaskEventAction.nothing()),
    );
  }

  static Future<void> ensureRunning() async {
    if (await FlutterForegroundTask.isRunningService) return;
    await FlutterForegroundTask.startService(
      serviceId: 100,
      notificationTitle: 'Jarvis',
      notificationText: 'Listening for "Hey Jarvis"',
      serviceTypes: [ForegroundServiceTypes.microphone],
      callback: startCallback,
    );
  }

  static void resume() {
    _clearWakeNotification();
    FlutterForegroundTask.sendDataToTask('resume');
  }

  /// A session is starting (wake, Talk, assistant or push): the wake word lets go of the mic and the entry notification goes away.
  static void pause() {
    _clearWakeNotification();
    FlutterForegroundTask.sendDataToTask('pause');
  }

  static Future<void> _clearWakeNotification() async {
    try {
      await FlutterLocalNotificationsPlugin().cancel(id: 1); // the "Listening..." full-screen entry from _onWake
    } catch (_) {}
  }

  /// Wake events arrive live when this isolate is up, or as a saved timestamp when the activity was started by the notification.
  static Future<void> bridgeWakeToSession() async {
    FlutterForegroundTask.addTaskDataCallback((d) async {
      if (d != 'wake') return;
      await FlutterForegroundTask.removeData(key: _wakeKey); // handled live: reopening the app must not start a second session
      AppHost.startSession();
    });
    final saved = await FlutterForegroundTask.getData<int>(key: _wakeKey);
    if (wakeIsFresh(saved, DateTime.now())) {
      await FlutterForegroundTask.removeData(key: _wakeKey);
      AppHost.startSession();
    }
  }
}
