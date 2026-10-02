import 'dart:typed_data';

/// Energy-based speech detector for barge-in: true once `frames` consecutive frames are loud.
/// ponytail: fixed RMS threshold; tune on the Pixel (echo cancellation decides how loud the speaker leaks), or swap in a real VAD.
class Vad {
  Vad({this.threshold = 1500.0, this.frames = 3});
  final double threshold;
  final int frames;
  int _run = 0;

  bool feed(Uint8List pcm) {
    final n = pcm.length ~/ 2;
    if (n == 0) return false;
    final bd = ByteData.sublistView(pcm);
    var sum = 0.0;
    for (var i = 0; i < n; i++) {
      final s = bd.getInt16(i * 2, Endian.little);
      sum += s * s;
    }
    final loud = (sum / n) > threshold * threshold;
    _run = loud ? _run + 1 : 0;
    if (_run >= frames) {
      _run = 0;
      return true;
    }
    return false;
  }
}
