import 'dart:typed_data';

import 'package:flutter_onnxruntime/flutter_onnxruntime.dart';

/// Runs one of the three openWakeWord models on a flat float input and returns the flat float output.
typedef Infer = Future<List<double>> Function(OwwModel model, Float32List input, List<int> shape);

enum OwwModel { mel, embedding, classifier }

const _chunk = 1280; // 80 ms at 16 kHz: openWakeWord's step
const _melContext = 480; // extra samples the mel model needs before each chunk
const _melRows = 76; // mel frames per embedding window
const _melBins = 32;
const _embDim = 96;
const _embWindow = 16; // embeddings the classifier looks at (about 1.3 s)

/// openWakeWord "hey_jarvis" streaming detector, ported from openwakeword/utils.py (AudioFeatures) so the scores match.
/// Pipeline per 80 ms chunk: mel spectrogram, then a 96-d embedding of the last 76 mel frames, then the classifier on the last 16 embeddings.
class Oww {
  Oww(this._infer, {this.threshold = 0.2});

  final Infer _infer;
  final double threshold; // 0.2 hit 27/36 with ~1 false wake in 4 min on the owner's voice; tune with a longer negative set

  final _raw = <double>[]; // last chunk + context, int16-scaled samples
  final _pending = <double>[]; // samples not yet forming a full chunk
  final _mel = <List<double>>[for (var i = 0; i < _melRows; i++) List.filled(_melBins, 1.0)];
  final _emb = <List<double>>[];
  Future<void> _tail = Future.value(); // serialises feeds so audio is scored in order

  /// Scores [pcm] (int16-scaled samples, any length). Completes true if the wake word fired; the detector then re-arms.
  Future<bool> feed(Float32List pcm) {
    final out = _tail.then((_) => _feed(pcm));
    _tail = out.then((_) {}, onError: (Object _) {});
    return out;
  }

  Future<bool> _feed(Float32List pcm) async {
    _pending.addAll(pcm);
    var fired = false;
    while (_pending.length >= _chunk) {
      final chunk = _pending.sublist(0, _chunk);
      _pending.removeRange(0, _chunk);
      if (await _step(chunk)) fired = true;
    }
    return fired;
  }

  Future<bool> _step(List<double> chunk) async {
    _raw.addAll(chunk);
    if (_raw.length > _chunk + _melContext) _raw.removeRange(0, _raw.length - (_chunk + _melContext));
    if (_raw.length < _chunk + _melContext) return false; // first chunk only seeds the context

    final melOut = await _infer(OwwModel.mel, Float32List.fromList(_raw), [1, _raw.length]);
    for (var i = 0; i + _melBins <= melOut.length; i += _melBins) {
      _mel.add([for (var j = 0; j < _melBins; j++) melOut[i + j] / 10 + 2]); // same transform openWakeWord applies
    }
    if (_mel.length > 970) _mel.removeRange(0, _mel.length - 970);

    final window = Float32List(_melRows * _melBins);
    final start = _mel.length - _melRows;
    for (var r = 0; r < _melRows; r++) {
      window.setRange(r * _melBins, (r + 1) * _melBins, _mel[start + r]);
    }
    _emb.add(await _infer(OwwModel.embedding, window, [1, _melRows, _melBins, 1]));
    if (_emb.length > _embWindow) _emb.removeAt(0);
    if (_emb.length < _embWindow) return false; // warm-up: also the cooldown after a detection

    final feats = Float32List(_embWindow * _embDim);
    for (var i = 0; i < _embWindow; i++) {
      feats.setRange(i * _embDim, (i + 1) * _embDim, _emb[i]);
    }
    final score = (await _infer(OwwModel.classifier, feats, [1, _embWindow, _embDim])).first;
    if (score <= threshold) return false;
    _emb.clear();
    return true;
  }
}

/// onnxruntime-backed [Infer]: loads the three models from assets/oww/ once.
class OrtInfer {
  final _sessions = <OwwModel, OrtSession>{};
  static const _files = {
    OwwModel.mel: 'melspectrogram.onnx',
    OwwModel.embedding: 'embedding_model.onnx',
    OwwModel.classifier: 'hey_jarvis_v0.1.onnx',
  };

  Future<void> load() async {
    for (final e in _files.entries) {
      _sessions[e.key] ??= await OnnxRuntime().createSessionFromAsset('assets/oww/${e.value}');
    }
  }

  Future<List<double>> call(OwwModel model, Float32List input, List<int> shape) async {
    final s = _sessions[model]!;
    final x = await OrtValue.fromList(input, shape);
    final out = await s.run({s.inputNames.first: x});
    try {
      return [for (final v in await out.values.first.asFlattenedList()) (v as num).toDouble()];
    } finally {
      await x.dispose();
      for (final v in out.values) {
        await v.dispose();
      }
    }
  }

  Future<void> close() async {
    for (final s in _sessions.values) {
      await s.close();
    }
    _sessions.clear();
  }
}
