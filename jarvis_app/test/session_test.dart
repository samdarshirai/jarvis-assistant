import 'dart:async';
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

  test('start plays the ready beep and says nothing; a pushed brief gets none', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      expect(r.speaker.said, isEmpty);
      expect(r.c.phase, Phase.listening);
      final p = Rig();
      p.c.start(speakText: 'brief');
      a.flushMicrotasks();
      expect(p.speaker.said, isEmpty);
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
      expect(r.player.played.last, [1, 2]);
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

  test('mic.start failing tears down to idle with an error, and start works again', () {
    fakeAsync((a) {
      final r = Rig();
      r.mic.startError = Exception('permission denied');
      r.c.start();
      a.flushMicrotasks();
      expect(r.c.phase, Phase.idle);
      expect(r.c.error, isNotNull);
      expect(r.socket.closed && r.mic.stopped, isTrue);
      expect(r.ended, 1);
      r.mic.startError = null;
      r.socket = FakeSocket();
      r.c.start();
      a.flushMicrotasks();
      expect(r.c.phase, Phase.listening);
    });
  });

  test('stop while connecting is honoured once connect completes', () {
    fakeAsync((a) {
      final r = Rig()..connectGate = Completer<void>();
      r.c.start();
      a.flushMicrotasks();
      expect(r.c.phase, Phase.connecting);
      r.c.stop();
      a.flushMicrotasks();
      expect(r.c.phase, Phase.idle);
      r.connectGate!.complete();
      a.flushMicrotasks();
      expect(r.c.phase, Phase.idle);
      expect(r.socket.closed, isTrue);
      expect(r.mic.started, 0);
      expect(r.ended, 1);
    });
  });

  test('a throwing action is shown and the next one still runs', () {
    fakeAsync((a) {
      final r = Rig();
      r.phone.throwOn.add('set_alarm');
      r.c.start();
      a.flushMicrotasks();
      r.socket.ctrl.add(const ClientActionsEvent([
        {'type': 'set_alarm'},
        {'type': 'open_app'},
      ]));
      r.socket.ctrl.add(const ClientActionsEvent([
        {'type': 'open_app'},
      ]));
      a.flushMicrotasks();
      expect(r.phone.ran.map((m) => m['type']), ['set_alarm', 'open_app', 'open_app']);
      expect(r.c.error, contains('set_alarm'));
    });
  });

  test('a socket close that never completes still ends idle within the timeout', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.socket.hangClose = true;
      r.c.stop();
      a.flushMicrotasks();
      a.elapse(const Duration(seconds: 3));
      a.flushMicrotasks();
      expect((r.c.phase, r.ended), (Phase.idle, 1));
    });
  });

  test('mic.stop throwing still ends idle and calls onEnded', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.mic.stopError = StateError('mic');
      r.c.stop();
      a.flushMicrotasks();
      expect((r.c.phase, r.ended), (Phase.idle, 1));
      expect(r.socket.closed, isTrue);
    });
  });

  test('stop from offline does not call onEnded again', () {
    fakeAsync((a) {
      final r = Rig()..failConnect = true;
      r.c.start();
      a.flushMicrotasks();
      r.c.stop();
      a.flushMicrotasks();
      expect(r.ended, 1);
    });
  });

  test('onStarted fires once per fresh start and not for an ignored second start', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.c.start();
      a.flushMicrotasks();
      expect(r.started, 1);
    });
  });

  test('a push arriving during a live session is spoken on the open socket, not dropped', () {
    fakeAsync((a) {
      final r = Rig();
      r.c.start();
      a.flushMicrotasks();
      r.c.start(speakText: 'Good morning.');
      a.flushMicrotasks();
      expect(r.socket.types.where((t) => t == 'hello').length, 1); // no second session
      expect(r.socket.sent.last.$1, 'speak');
      expect(r.socket.sent.last.$2, {'text': 'Good morning.'});
      // an ignored start without text sends nothing
      final before = r.socket.sent.length;
      r.c.start();
      a.flushMicrotasks();
      expect(r.socket.sent.length, before);
    });
  });

  test('a push arriving while connecting is queued and spoken right after hello', () {
    fakeAsync((a) {
      final r = Rig()..connectGate = Completer<void>();
      r.c.start();
      a.flushMicrotasks();
      expect(r.c.phase, Phase.connecting);
      r.c.start(speakText: 'Brief.');
      a.flushMicrotasks();
      expect(r.socket.sent, isEmpty);
      r.connectGate!.complete();
      a.flushMicrotasks();
      expect(r.socket.types, ['hello', 'speak']);
      expect(r.socket.sent[1].$2, {'text': 'Brief.'});
    });
  });
}
