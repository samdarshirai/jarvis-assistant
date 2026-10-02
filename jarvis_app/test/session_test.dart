import 'dart:typed_data';

import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/protocol.dart';
import 'package:jarvis_app/session.dart';

import 'fakes.dart';
import 'session_rig.dart';

const card = ConfirmCardEvent(interruptId: 'i1', summary: 'Create Gym', tapOnly: false, afterUntrusted: false);

void main() {
  test('start sends hello with the fcm token, streams mic audio, and listens', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      expect(r.c.phase, Phase.listening);
      expect(r.socket.sent.first.$1, 'hello');
      expect(r.socket.sent.first.$2, {'fcm_token': 'fcm-1'});
      r.mic.ctrl.add(quiet());
      expect(r.socket.audio.length, 1);
    });
  });

  test('speakText sends a speak request after hello', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start(speakText: 'Good morning.');
      a.flushMicrotasks();
      expect(r.socket.types, ['hello', 'speak']);
      expect(r.socket.sent[1].$2, {'text': 'Good morning.'});
    });
  });

  test('connect failure is offline: says so, ends, never listens', () {
    fakeAsync((a) {
      final r = Rig()..failConnect = true;
      r.c.start();
      a.flushMicrotasks();
      expect(r.c.phase, Phase.offline);
      expect(r.speaker.said, ['Jarvis is offline.']);
      expect(r.ended, 1);
      expect(r.mic.ctrl.hasListener, isFalse);
    });
  });

  test('starting while active is ignored', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.c.start();
      a.flushMicrotasks();
      expect(r.socket.types.where((t) => t == 'hello').length, 1);
    });
  });

  test('server states map to phases and audio is played', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(const StateEvent('thinking'));
      expect(r.c.phase, Phase.thinking);
      r.socket.ctrl.add(const StateEvent('speaking'));
      r.socket.ctrl.add(AudioEvent(Uint8List.fromList([1, 2])));
      expect(r.c.phase, Phase.speaking);
      expect(r.player.played.single, [1, 2]);
      r.socket.ctrl.add(const StateEvent('listening'));
      expect(r.c.phase, Phase.listening);
      expect(r.player.flushes, 0); // a normal end of speech must not cut the buffered tail
    });
  });

  test('user text while speaking flushes the player (server-side barge-in)', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(const StateEvent('speaking'));
      r.socket.ctrl.add(const TranscriptEvent('user', 'wait', false));
      expect(r.player.flushes, 1);
      expect(r.c.userText, 'wait');
    });
  });

  test('local speech while speaking sends cancel and flushes', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(const StateEvent('speaking'));
      for (var i = 0; i < 3; i++) {
        r.mic.ctrl.add(loud());
      }
      expect(r.socket.types.where((t) => t == 'cancel').length, 1);
      expect(r.player.flushes, 1);
      expect(r.c.phase, Phase.listening);
    });
  });

  test('card then tap sends the confirm frame with its id and clears the card', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(card);
      expect(r.c.card, isNotNull);
      r.c.confirm(true);
      expect(r.socket.sent.last.$1, 'confirm');
      expect(r.socket.sent.last.$2, {'decision': 'yes', 'interrupt_id': 'i1'});
      expect(r.c.card, isNull);
      r.c.confirm(false); // no card any more: nothing is sent
      expect(r.socket.types.where((t) => t == 'confirm').length, 1);
    });
  });

  test('silence for 8 s ends the session with bye', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      a.elapse(const Duration(seconds: 7));
      expect(r.c.phase, Phase.listening);
      a.elapse(const Duration(seconds: 2));
      a.flushMicrotasks();
      expect(r.socket.types, contains('bye'));
      expect(r.socket.closed && r.mic.stopped, isTrue);
      expect((r.c.phase, r.ended), (Phase.idle, 1));
      expect(r.speaker.said, isEmpty); // our own close is not "connection lost"
    });
  });

  test('activity restarts the silence timer', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      a.elapse(const Duration(seconds: 6));
      r.socket.ctrl.add(const TranscriptEvent('user', 'hm', false));
      a.elapse(const Duration(seconds: 6));
      expect(r.c.phase, Phase.listening);
    });
  });

  test('a pending card waits 30 s, and thinking or speaking never time out', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(card);
      a.elapse(const Duration(seconds: 29));
      expect(r.c.phase, Phase.listening);
      a.elapse(const Duration(seconds: 2));
      a.flushMicrotasks();
      expect(r.c.phase, Phase.idle);

      final s = Rig();
      s.c.start();
      a.flushMicrotasks();
      s.socket.ctrl.add(const StateEvent('thinking'));
      a.elapse(const Duration(seconds: 60));
      expect(s.c.phase, Phase.thinking);
    });
  });

  test('client actions run in order and a failure is shown, not sent', () {
    fakeAsync((a) {
      final r = Rig();
      r.phone.errors['compose_message'] = 'No contact named Anna.';
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(const ClientActionsEvent([
        {'type': 'set_alarm', 'hour': 6, 'minute': 0},
        {'type': 'compose_message', 'app': 'sms', 'contact': 'Anna', 'text': 'hi'},
      ]));
      a.flushMicrotasks();
      expect(r.phone.ran.map((m) => m['type']), ['set_alarm', 'compose_message']);
      expect(r.c.error, 'No contact named Anna.');
      expect(r.socket.types, isNot(contains('error')));
    });
  });

  test('server error frames are surfaced', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(const ErrorEvent('Speech output failed'));
      expect(r.c.error, 'Speech output failed');
    });
  });

  test('an unexpected server close says the connection was lost and ends', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.close();
      a.flushMicrotasks();
      expect(r.speaker.said, ['Jarvis connection lost.']);
      expect((r.c.phase, r.ended), (Phase.idle, 1));
    });
  });
}
