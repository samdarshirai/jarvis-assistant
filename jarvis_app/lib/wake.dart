import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/services.dart' show rootBundle;
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa;

import 'app.dart';

const _kwsFiles = ['encoder.onnx', 'decoder.onnx', 'joiner.onnx', 'tokens.txt', 'keywords.txt'];
const _wakeKey = 'wake';

bool wakeIsFresh(int? savedMillis, DateTime now) {
  if (savedMillis == null) return false;
  final age = now.millisecondsSinceEpoch - savedMillis;
  return age >= 0 && age <= 15000;
}

@pragma('vm:entry-point')
void startCallback() => FlutterForegroundTask.setTaskHandler(WakeTaskHandler());

/// Runs sherpa-onnx keyword spotting in the foreground service so the wake word works with the screen off (FR-14).
class WakeTaskHandler extends TaskHandler {
  final _rec = AudioRecorder();
  sherpa.KeywordSpotter? _kws;
  sherpa.OnlineStream? _stream;
  StreamSubscription<Uint8List>? _sub;
  bool _listening = false;

  /// The native library reads model files from disk, so copy the bundled assets out once.
  Future<sherpa.KeywordSpotter> _spotter() async {
    final dir = Directory('${(await getApplicationSupportDirectory()).path}/kws')..createSync(recursive: true);
    for (final f in _kwsFiles) {
      final out = File('${dir.path}/$f');
      if (!out.existsSync()) await out.writeAsBytes((await rootBundle.load('assets/kws/$f')).buffer.asUint8List());
    }
    sherpa.initBindings();
    return sherpa.KeywordSpotter(sherpa.KeywordSpotterConfig(
      model: sherpa.OnlineModelConfig(
        transducer: sherpa.OnlineTransducerModelConfig(
            encoder: '${dir.path}/encoder.onnx', decoder: '${dir.path}/decoder.onnx', joiner: '${dir.path}/joiner.onnx'),
        tokens: '${dir.path}/tokens.txt',
        debug: false,
      ),
      keywordsFile: '${dir.path}/keywords.txt',
      keywordsThreshold: 0.25, // lower = fewer false accepts, higher = fewer misses; tune on the phone
      keywordsScore: 1.0,
    ));
  }

  /// Never throws: a missing model file or mic permission must not leave an unhandled error and a notification that lies.
  Future<void> _listen() async {
    if (_listening) return; // resume can arrive while the spotter already runs (e.g. after a manual Talk session)
    try {
      final kws = _kws ??= await _spotter();
      final stream = _stream = kws.createStream();
      final pcm = await _rec.startStream(const RecordConfig(encoder: AudioEncoder.pcm16bits, sampleRate: 16000, numChannels: 1));
      _listening = true;
      _sub = pcm.listen((bytes) {
        if (!_listening) return;
        final s16 = bytes.buffer.asInt16List(bytes.offsetInBytes, bytes.lengthInBytes ~/ 2);
        final f32 = Float32List(s16.length);
        for (var i = 0; i < s16.length; i++) {
          f32[i] = s16[i] / 32768.0;
        }
        stream.acceptWaveform(samples: f32, sampleRate: 16000);
        while (kws.isReady(stream)) {
          kws.decode(stream);
          if (kws.getResult(stream).keyword.isNotEmpty) {
            kws.reset(stream);
            _onWake();
            return;
          }
        }
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
    _stream?.free();
    _stream = null;
  }

  Future<void> _onWake() async {
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
  Future<void> onDestroy(DateTime timestamp, bool isTimeout) async {
    await _pause();
    _kws?.free();
    await _rec.dispose();
  }
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
