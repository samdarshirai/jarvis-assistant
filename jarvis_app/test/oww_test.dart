import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/oww.dart';

void main() {
  late List<(OwwModel, int)> calls;
  double score = 0;

  Oww make() {
    calls = [];
    return Oww((m, input, shape) async {
      calls.add((m, input.length));
      return switch (m) {
        OwwModel.mel => List.filled(8 * 32, 0.0), // 8 new mel frames per chunk
        OwwModel.embedding => List.filled(96, 0.0),
        OwwModel.classifier => [score],
      };
    }, threshold: 0.5);
  }

  Float32List pcm(int n) => Float32List(n);

  setUp(() => score = 0);

  test('buffers until a full 1280-sample chunk and seeds the mel context first', () async {
    final o = make();
    expect(await o.feed(pcm(1279)), isFalse);
    expect(calls, isEmpty);
    await o.feed(pcm(1)); // first chunk only seeds the 480-sample context
    expect(calls, isEmpty);
    await o.feed(pcm(1280));
    expect(calls.first, (OwwModel.mel, 1280 + 480));
    expect(calls[1], (OwwModel.embedding, 76 * 32));
  });

  test('classifier waits for 16 embeddings, then fires above the threshold and re-arms', () async {
    final o = make();
    score = 0.9;
    var fired = 0;
    for (var i = 0; i < 40; i++) {
      if (await o.feed(pcm(1280))) fired++;
    }
    // chunk 1 seeds, chunks 2..16 warm up (15 embeddings), chunk 17 is the first classified: fire, then 16 more to re-arm
    expect(calls.where((c) => c.$1 == OwwModel.classifier).first.$2, 16 * 96);
    expect(fired, 2);
  });

  test('stays quiet below the threshold', () async {
    final o = make();
    score = 0.4;
    for (var i = 0; i < 40; i++) {
      expect(await o.feed(pcm(1280)), isFalse);
    }
  });

  test('odd-sized feeds are rechunked in order', () async {
    final o = make();
    for (var i = 0; i < 10; i++) {
      await o.feed(pcm(1920)); // what the phone mic delivers
    }
    expect(calls.where((c) => c.$1 == OwwModel.mel).length, 14); // 19200 samples = 15 chunks, the first only seeds the context
  });
}
