import 'dart:typed_data';

import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/audio.dart';
import 'package:jarvis_app/protocol.dart';
import 'package:jarvis_app/session.dart';

import 'fakes.dart';

SessionController _ctl(VoiceSocket Function() connect, Player p, Speaker s) => SessionController(
      connect: () async => connect(),
      mic: FakeMic(),
      player: p,
      phone: FakePhone(),
      speaker: s,
      fcmToken: () async => 'f',
      onEnded: () {},
      onStarted: () {},
    );

void main() {
  final a = Uint8List.fromList([1, 2]), b = Uint8List.fromList([3, 4]);

  test('day forwards play and say', () async {
    final p = FakePlayer(), s = FakeSpeaker();
    QuietPlayer(p, () => false).play(a);
    await QuietSpeaker(s, () => false).say('hi');
    expect(p.played, [a]);
    expect(s.said, ['hi']);
  });

  test('night drops play and say', () async {
    final p = FakePlayer(), s = FakeSpeaker();
    QuietPlayer(p, () => true).play(a);
    await QuietSpeaker(s, () => true).say('hi');
    expect(p.played, isEmpty);
    expect(s.said, isEmpty);
  });

  test('night mid-reply drops chunks and flushes inner once', () {
    final p = FakePlayer();
    var night = false;
    final q = QuietPlayer(p, () => night);
    q.play(a);
    night = true;
    q.play(b);
    q.play(b);
    q.play(b);
    expect(p.played, [a]);
    expect(p.flushes, 1);
    night = false;
    q.play(b);
    expect(p.played, [a, b]);
    night = true;
    q.play(b);
    expect(p.flushes, 2);
  });

  test('night beginning mid-reply cuts buffered audio without another chunk', () {
    fakeAsync((fa) {
      final p = FakePlayer();
      var night = false;
      final q = QuietPlayer(p, () => night);
      q.play(Uint8List(32000 * 5)); // 5 s of 16 kHz mono pcm16, already handed to the native player
      fa.elapse(const Duration(milliseconds: 1500));
      expect(p.flushes, 0);
      night = true;
      fa.elapse(const Duration(milliseconds: 1500));
      expect(p.flushes, 1);
      fa.elapse(const Duration(seconds: 10));
      expect(p.flushes, 1);
      expect(fa.pendingTimers, isEmpty);
    });
  });

  test('the watch stops once forwarded audio has played out', () {
    fakeAsync((fa) {
      final p = FakePlayer();
      var night = false;
      QuietPlayer(p, () => night).play(Uint8List(32000 * 2));
      fa.elapse(const Duration(seconds: 4));
      expect(fa.pendingTimers, isEmpty);
      night = true;
      fa.elapse(const Duration(seconds: 2));
      expect(p.flushes, 0); // nothing left playing, nothing to cut
    });
  });

  test('flush always forwards', () async {
    final p = FakePlayer();
    await QuietPlayer(p, () => true).flush();
    expect(p.flushes, 1);
  });

  test('session at night is silent but text still updates', () {
    fakeAsync((fa) {
      final socket = FakeSocket(), p = FakePlayer(), s = FakeSpeaker();
      final c = _ctl(() => socket, QuietPlayer(p, () => true), QuietSpeaker(s, () => true));
      c.start();
      fa.flushMicrotasks();
      socket.ctrl.add(const TranscriptEvent('user', 'hello', true));
      socket.ctrl.add(const TranscriptEvent('assistant', 'good night', true));
      socket.ctrl.add(AudioEvent(a));
      expect(c.userText, 'hello');
      expect(c.jarvisText, 'good night');
      expect(p.played, isEmpty);
      expect(s.said, isEmpty);
    });
  });

  test('offline message is silent at night', () {
    fakeAsync((fa) {
      final s = FakeSpeaker();
      final c = _ctl(() => throw Exception('down'), QuietPlayer(FakePlayer(), () => true), QuietSpeaker(s, () => true));
      c.start();
      fa.flushMicrotasks();
      expect(c.phase, Phase.offline);
      expect(s.said, isEmpty);
    });
  });
}
