import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/app.dart';

import 'session_rig.dart';

void main() {
  tearDown(() {
    AppHost.attach(null);
    AppHost.now = DateTime.now;
  });

  test('a start requested before the controller exists runs once on attach', () {
    fakeAsync((a) {
      final r = Rig();
      AppHost.startSession(speakText: 'Hi');
      a.flushMicrotasks();
      expect(r.socket.sent, isEmpty);
      AppHost.attach(r.c);
      a.flushMicrotasks();
      expect(r.socket.sent.where((e) => e.$1 == 'speak').map((e) => e.$2['text']), ['Hi']);
      AppHost.attach(r.c);
      a.flushMicrotasks();
      expect(r.socket.sent.where((e) => e.$1 == 'speak').length, 1);
    });
  });

  test('only the last pending request is started', () {
    fakeAsync((a) {
      final r = Rig();
      AppHost.startSession(speakText: 'one');
      AppHost.startSession(speakText: 'two');
      AppHost.attach(r.c);
      a.flushMicrotasks();
      expect(r.socket.sent.where((e) => e.$1 == 'speak').map((e) => e.$2['text']), ['two']);
    });
  });

  test('a pending request older than 30 s is dropped', () {
    fakeAsync((a) {
      final r = Rig();
      var t = DateTime(2026);
      AppHost.now = () => t;
      AppHost.startSession(speakText: 'old');
      t = t.add(const Duration(seconds: 31));
      AppHost.attach(r.c);
      a.flushMicrotasks();
      expect(r.socket.sent, isEmpty);
    });
  });

  test('with a controller present, start is immediate', () {
    fakeAsync((a) {
      final r = Rig();
      AppHost.attach(r.c);
      AppHost.startSession(speakText: 'now');
      a.flushMicrotasks();
      expect(r.socket.sent.where((e) => e.$1 == 'speak').length, 1);
    });
  });

  test('attach(null) clears the pending request', () {
    fakeAsync((a) {
      final r = Rig();
      AppHost.startSession(speakText: 'x');
      AppHost.attach(null);
      AppHost.attach(r.c);
      a.flushMicrotasks();
      expect(r.socket.sent, isEmpty);
    });
  });
}
