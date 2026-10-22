import 'dart:async';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/foundation.dart' show debugPrint;
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:jarvis_gate/jarvis_gate.dart';
import 'package:record/record.dart';

import 'app.dart';
import 'oww.dart';
import 'prefs.dart';
import 'store.dart';
import 'wake_gate.dart';

/// No sign-in token: the wake word does nothing. The app stops the service when /me says voice is not set up,
/// and a backend `needs_deepgram` close covers a key removed elsewhere (no /me round trip on every wake).
Future<bool> voiceConfigured() async => (await const ConfigStore().load()).token != null;

const _wakeKey = 'wake';
const _listeningText = 'Listening for "Hey Jarvis"';

/// The detector runs only while the gate is open and no session holds the mic.
bool shouldListen({required bool gateOpen, required bool held}) => gateOpen && !held;

bool wakeIsFresh(int? savedMillis, DateTime now) {
  if (savedMillis == null) return false;
  final age = now.millisecondsSinceEpoch - savedMillis;
  return age >= 0 && age <= 15000;
}

@pragma('vm:entry-point')
void startCallback() => FlutterForegroundTask.setTaskHandler(WakeTaskHandler());

/// Runs openWakeWord "hey_jarvis" in the foreground service so the wake word works with the screen off (FR-14).
class WakeTaskHandler extends TaskHandler {
  final _rec = AudioRecorder();
  final _ort = OrtInfer();
  Oww? _oww;
  StreamSubscription<Uint8List>? _sub;
  bool _listening = false;
  int _chunks = 0;
  double _peak = 0;
  bool _held = false; // a session (wake, Talk, assistant, push) holds the mic
  bool _gateOpen = false;
  GateState _gate = const GateState(charging: false, androidAuto: false); // unknown = closed: mic stays off
  StreamSubscription<GateState>? _gateSub;
  bool _gotEvent = false; // a change event is newer than the startup state() reply
  Future<void> _queue = Future.value();

  /// Serialises gate/pause/resume work so duplicate gate events cannot start two recorders.
  Future<void> _run(Future<void> Function() job) => _queue = _queue.then((_) => job()).catchError((Object _) {});

  /// Re-reads the setting and starts or stops the detector to match the gate.
  Future<void> _applyGate() async {
    _gateOpen = wakeGateOpen(enabled: await storedWakeEnabled(), charging: _gate.charging, androidAuto: _gate.androidAuto);
    if (_gateOpen) return _listen();
    await _pause();
    await FlutterForegroundTask.updateService(notificationText: 'Wake word paused - plug in or connect Android Auto');
  }

  /// Never throws: a missing model file or mic permission must not leave an unhandled error and a notification that lies.
  Future<void> _listen() async {
    if (!shouldListen(gateOpen: _gateOpen, held: _held)) return;
    if (_listening) return; // resume can arrive while the detector already runs (e.g. after a manual Talk session)
    if (!await voiceConfigured()) return;
    try {
      await _ort.load();
      final oww = _oww = Oww(_ort.call);
      final pcm = await _rec.startStream(const RecordConfig(encoder: AudioEncoder.pcm16bits, sampleRate: 16000, numChannels: 1));
      _listening = true;
      await FlutterForegroundTask.updateService(notificationText: _listeningText);
      _sub = pcm.listen((bytes) {
        if (!_listening) return;
        final pcm16 = ByteData.sublistView(bytes); // asInt16List throws when the chunk starts at an odd offset
        final samples = Float32List(bytes.lengthInBytes ~/ 2); // int16-scaled: openWakeWord does not normalise to +-1
        for (var i = 0; i < samples.length; i++) {
          samples[i] = pcm16.getInt16(i * 2, Endian.little).toDouble();
          _peak = math.max(_peak, samples[i].abs() / 32768.0);
        }
        if (++_chunks % 20 == 0) { // TEMP debug: loudest sample reaching the detector since the last line
          debugPrint('wake: chunks=$_chunks peak=${_peak.toStringAsFixed(3)}');
          _peak = 0;
        }
        oww.feed(samples).then((hit) {
          if (hit && _listening && identical(oww, _oww)) {
            debugPrint('wake: DETECTED hey_jarvis');
            _onWake();
          }
        }, onError: (Object e) => debugPrint('wake: detector error $e'));
      });
    } catch (e) {
      await FlutterForegroundTask.updateService(
          notificationText: 'Wake word unavailable - use the Talk button in the app ($e)');
    }
  }

  Future<void> _pause() async {
    if (!_listening) return;
    _listening = false;
    try {
      await _sub?.cancel();
      await _rec.stop(); // any session (wake, Talk, assistant, push) takes the microphone
    } catch (_) {}
    _oww = null; // late results from the old detector are ignored
  }

  Future<void> _onWake() async {
    _held = true; // the session owns the mic from now on; its 'resume' lets go (a gate event must not restart us first)
    await _pause();
    if (!await voiceConfigured()) return;
    await FlutterForegroundTask.saveData(key: _wakeKey, value: DateTime.now().millisecondsSinceEpoch);
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
              timeoutAfter: 15000, // backstop: never linger if a cancel is missed
              category: AndroidNotificationCategory.call)),
    );
    // After show: main cancels this on session start, and a cancel sent before show would leave it stuck.
    FlutterForegroundTask.sendDataToMain('wake');
  }

  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {
    _gateSub = JarvisGate.changes.listen((g) {
      _gotEvent = true;
      _gate = g;
      _run(_applyGate); // duplicates are harmless: re-applying the same gate is a no-op
    }, onError: (Object e) => debugPrint('wake: gate error $e'));
    try {
      // Android Auto may never report: a hung or failed read leaves the gate closed until a change event arrives.
      final seed = await JarvisGate.state().timeout(const Duration(seconds: 3));
      if (!_gotEvent) _gate = seed;
    } catch (_) {}
    await _run(_applyGate);
  }
  @override
  void onRepeatEvent(DateTime timestamp) {}
  @override
  Future<void> onDestroy(DateTime timestamp, bool isTimeout) async {
    await _gateSub?.cancel();
    await _pause();
    await _ort.close();
    await _rec.dispose();
  }
  @override
  void onReceiveData(Object data) {
    if (data == 'resume') _run(() { _held = false; return _applyGate(); });
    if (data == 'pause') { _held = true; _run(_pause); }
    if (data == 'gate') _run(_applyGate);
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
    if (!await voiceConfigured()) return;
    if (!await storedWakeEnabled()) return;
    if (await FlutterForegroundTask.isRunningService) return;
    await FlutterForegroundTask.startService(
      serviceId: 100,
      notificationTitle: 'Jarvis',
      notificationText: _listeningText,
      serviceTypes: [ForegroundServiceTypes.microphone],
      callback: startCallback,
    );
  }

  static void resume() {
    _clearWakeNotification();
    if (!AppHost.ready) return;
    FlutterForegroundTask.sendDataToTask('resume');
  }

  /// Charging, Android Auto or the wake setting changed: the task re-checks the gate.
  static void refreshGate() {
    if (!AppHost.ready) return;
    FlutterForegroundTask.sendDataToTask('gate');
  }

  static Future<void> stop() async {
    await _clearWakeNotification();
    if (await FlutterForegroundTask.isRunningService) await FlutterForegroundTask.stopService();
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
