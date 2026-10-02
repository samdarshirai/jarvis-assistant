import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/vad.dart';

Uint8List pcm(int amp, [int samples = 320]) {
  final b = ByteData(samples * 2);
  for (var i = 0; i < samples; i++) {
    b.setInt16(i * 2, i.isEven ? amp : -amp, Endian.little);
  }
  return b.buffer.asUint8List();
}

void main() {
  test('silence never triggers', () {
    final v = Vad();
    for (var i = 0; i < 20; i++) {
      expect(v.feed(pcm(0)), isFalse);
    }
  });

  test('needs several loud frames in a row', () {
    final v = Vad();
    expect([v.feed(pcm(8000)), v.feed(pcm(8000)), v.feed(pcm(8000))], [false, false, true]);
  });

  test('a quiet frame resets the run, and triggering resets it too', () {
    final v = Vad();
    v.feed(pcm(8000));
    v.feed(pcm(8000));
    expect(v.feed(pcm(0)), isFalse);
    expect([v.feed(pcm(8000)), v.feed(pcm(8000)), v.feed(pcm(8000))], [false, false, true]);
    expect(v.feed(pcm(8000)), isFalse);
  });
}
