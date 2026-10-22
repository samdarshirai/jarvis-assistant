import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';

import 'session.dart';

class RecordMic implements Mic {
  final _rec = AudioRecorder();
  static const _audio = MethodChannel('jarvis/audio');

  Future<bool> _headset() async {
    try {
      return await _audio.invokeMethod<bool>('headsetStart') ?? false;
    } catch (_) {
      return false; // no channel (tests, assistant cold start) or no headset: phone mic and speaker
    }
  }

  @override
  Future<Stream<Uint8List>> start() async {
    // Asked again on every Talk: startup may have been dismissed. Permanently denied: Android shows no dialog, so open Settings.
    if (!await Permission.microphone.request().isGranted) {
      await openAppSettings();
      throw 'Microphone permission is off - allow it in Settings, then tap Talk again';
    }
    // Call-app routing: with a headset, mic and replies both go through it (the ack already plays there).
    final headset = await _headset();
    final config = RecordConfig(
      encoder: AudioEncoder.pcm16bits,
      sampleRate: 16000,
      numChannels: 1,
      echoCancel: true, // barge-in depends on the mic not hearing Jarvis's own speaker
      noiseSuppress: true,
      // echoCancel alone is ignored on the default mic source; the voice-communication source enables the hardware AEC.
      androidConfig: AndroidRecordConfig(
        audioSource: AndroidAudioSource.voiceCommunication,
        audioManagerMode: AudioManagerMode.modeInCommunication,
        speakerphone: !headset, // communication mode otherwise plays through the earpiece
      ),
    );
    // The wake-word service releases the mic asynchronously after onStarted; "PCM reader failed to initialize" means it still held it.
    // ponytail: fixed 3 tries x 300 ms, not an ack from the service.
    Stream<Uint8List> stream;
    for (var i = 0;; i++) {
      try {
        stream = await _rec.startStream(config);
        break;
      } on PlatformException {
        if (i == 2) rethrow;
        await Future<void>.delayed(const Duration(milliseconds: 300));
      }
    }
    return headset ? _undouble(stream) : stream;
  }

  /// The Bluetooth SCO mic delivers every sample twice (speech 2x too slow, Deepgram hears nothing).
  /// Decided once, on the first chunk with sound; ponytail: assumes the headset does not change mid-session.
  Stream<Uint8List> _undouble(Stream<Uint8List> s) {
    bool? doubled;
    return s.map((b) {
      final bd = ByteData.sublistView(b);
      final n = b.length ~/ 4 * 2; // whole sample pairs
      if (doubled == null) {
        var sound = false, same = true;
        for (var i = 0; i + 1 < n; i += 2) {
          final x = bd.getInt16(i * 2, Endian.little);
          sound |= x != 0;
          same &= x == bd.getInt16(i * 2 + 2, Endian.little);
        }
        if (!sound) return b;
        doubled = same;
      }
      if (!doubled!) return b;
      final out = ByteData(n);
      for (var i = 0; i < n; i += 2) {
        out.setInt16(i, bd.getInt16(i * 2, Endian.little), Endian.little);
      }
      return out.buffer.asUint8List();
    });
  }

  @override
  Future<void> stop() async {
    if (await _rec.isRecording()) await _rec.stop();
    try {
      await _audio.invokeMethod('headsetStop');
    } catch (_) {}
  }
}

class PcmPlayer implements Player {
  static const _audio = MethodChannel('jarvis/audio');

  // Native AudioTrack with voice-call usage: flutter_sound plays as music, which Samsung's echo canceller does not hear.
  @override
  void play(Uint8List pcm) => _audio.invokeMethod('play', pcm).catchError((Object _) {}); // never an unhandled error

  @override
  Future<void> flush() => _audio.invokeMethod('flush').catchError((Object _) {});
}

class TtsSpeaker implements Speaker {
  final _tts = FlutterTts();
  Future<void>? _init;
  @override
  Future<void> say(String text) async {
    _init ??= _setup();
    await _init;
    // Android's own TTS: works with the backend down. speak() returns 1 when queued; right after wake the engine or audio
    // focus is often not ready and it silently returns 0, so retry instead of dropping the ack.
    for (var i = 0; i < 3; i++) {
      if (await _tts.speak(text) == 1) return;
      await Future<void>.delayed(const Duration(milliseconds: 250));
    }
  }

  Future<void> _setup() async {
    await _tts.awaitSpeakCompletion(true); // say() resolves when speech ends
    try {
      // ponytail: Google TTS en-US/GB male voices are iob/iol/iom/tpd/rjs; none installed -> lower pitch only
      final voices = await _tts.getVoices as List;
      final male = voices.cast<Map>().where((v) {
        final n = '${v['name']}'.toLowerCase();
        return n.startsWith('en-') && RegExp(r'-x-(iob|iol|iom|tpd|rjs)-').hasMatch(n);
      });
      if (male.isNotEmpty) {
        await _tts.setVoice({'name': '${male.first['name']}', 'locale': '${male.first['locale']}'});
      } else {
        await _tts.setPitch(0.75);
      }
    } catch (_) {} // voice choice is cosmetic; never block the ack
  }
}

/// Drops all Jarvis sound at night. isNight is checked per call and fails closed (see Prefs).
/// Chunks arrive faster than real time, so the native player may hold a whole reply: while forwarded audio may still be
/// playing, a 1 s watch flushes it as soon as night begins, even if no further chunk comes.
class QuietPlayer implements Player {
  QuietPlayer(this._inner, this._isNight, {this.tick = const Duration(seconds: 1)});
  final Player _inner;
  final bool Function() _isNight;
  final Duration tick;
  bool _cut = false;
  int _leftMs = 0; // forwarded audio not yet played out, estimated from bytes (16 kHz mono pcm16 = 32 bytes/ms)
  Timer? _watch;

  void _cutNow() {
    _watch?.cancel();
    _watch = null;
    _leftMs = 0;
    if (!_cut) {
      _cut = true;
      _inner.flush(); // night began mid-reply: drop what is already buffered
    }
  }

  @override
  void play(Uint8List pcm) {
    if (_isNight()) return _cutNow();
    _cut = false;
    _inner.play(pcm);
    _leftMs += pcm.length ~/ 32;
    _watch ??= Timer.periodic(tick, (_) {
      if (_isNight()) return _cutNow();
      _leftMs -= tick.inMilliseconds;
      // One tick of slack past the estimate covers the native player's own latency.
      if (_leftMs <= -tick.inMilliseconds) {
        _watch?.cancel();
        _watch = null;
        _leftMs = 0;
      }
    });
  }

  @override
  Future<void> flush() {
    _watch?.cancel();
    _watch = null;
    _leftMs = 0;
    return _inner.flush();
  }
}

class QuietSpeaker implements Speaker {
  QuietSpeaker(this._inner, this._isNight);
  final Speaker _inner;
  final bool Function() _isNight;

  // ponytail: checked once per utterance; a short ack or the offline message already speaking when night begins
  // finishes (a few seconds at most). Interrupt it via a stop() on Speaker if that ever matters.
  @override
  Future<void> say(String text) async {
    if (!_isNight()) await _inner.say(text);
  }
}
