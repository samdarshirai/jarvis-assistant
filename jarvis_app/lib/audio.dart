import 'dart:typed_data';

import 'package:flutter/services.dart';
import 'package:flutter_sound/flutter_sound.dart';
import 'package:flutter_tts/flutter_tts.dart';
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
    // Call-app routing: with a headset, mic and replies both go through it (the ack already plays there).
    final headset = await _headset();
    final stream = await _rec.startStream(RecordConfig(
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
    ));
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
  final _p = FlutterSoundPlayer();
  // Serialises play()/flush() so a play racing a flush cannot touch a half-closed stream.
  Future<void> _chain = Future.value();
  bool _open = false;

  Future<void> _ensure() async {
    if (_open) return;
    await _p.openPlayer();
    await _p.startPlayerFromStream(codec: Codec.pcm16, numChannels: 1, sampleRate: 16000, interleaved: true, bufferSize: 8192);
    _open = true;
  }

  void _enqueue(Future<void> Function() job) {
    _chain = _chain.then((_) => job()).catchError((Object _) {}); // never an unhandled error
  }

  @override
  void play(Uint8List pcm) => _enqueue(() async {
        await _ensure();
        _p.uint8ListSink?.add(pcm);
      });

  @override
  Future<void> flush() {
    _enqueue(() async {
      if (!_open) return;
      _open = false; // stopping drops the buffered audio; the next play() reopens the stream
      await _p.stopPlayer();
    });
    return _chain;
  }
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
