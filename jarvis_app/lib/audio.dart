import 'dart:typed_data';

import 'package:flutter_sound/flutter_sound.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:record/record.dart';

import 'session.dart';

class RecordMic implements Mic {
  final _rec = AudioRecorder();

  @override
  Future<Stream<Uint8List>> start() => _rec.startStream(const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: 16000,
        numChannels: 1,
        echoCancel: true, // barge-in depends on the mic not hearing Jarvis's own speaker
        noiseSuppress: true,
      ));

  @override
  Future<void> stop() async {
    if (await _rec.isRecording()) await _rec.stop();
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
  @override
  Future<void> say(String text) => _tts.speak(text); // Android's own TTS: works with the backend down
}
