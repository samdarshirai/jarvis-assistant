import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/protocol.dart';

void main() {
  test('binary frames are audio', () {
    final e = decodeServer(Uint8List.fromList([1, 2, 3]));
    expect(e, isA<AudioEvent>());
    expect((e as AudioEvent).pcm, [1, 2, 3]);
  });

  test('state', () {
    final e = decodeServer('{"type":"state","state":"speaking"}') as StateEvent;
    expect(e.state, 'speaking');
  });

  test('transcript', () {
    final e = decodeServer('{"type":"transcript","role":"user","text":"hi","final":false}') as TranscriptEvent;
    expect((e.role, e.text, e.isFinal), ('user', 'hi', false));
  });

  test('confirm card', () {
    final e = decodeServer(jsonEncode({
      'type': 'confirm_card', 'interrupt_id': 'i1', 'summary': 'Create Gym', 'tap_only': true, 'after_untrusted': false,
    })) as ConfirmCardEvent;
    expect((e.interruptId, e.summary, e.tapOnly, e.afterUntrusted), ('i1', 'Create Gym', true, false));
  });

  test('client actions', () {
    final e = decodeServer('{"type":"client_actions","actions":[{"type":"set_alarm","hour":6,"minute":0}]}')
        as ClientActionsEvent;
    expect(e.actions, [
      {'type': 'set_alarm', 'hour': 6, 'minute': 0}
    ]);
  });

  test('error', () {
    expect((decodeServer('{"type":"error","message":"boom"}') as ErrorEvent).message, 'boom');
  });

  test('garbage and unknown frames decode to null', () {
    for (final raw in ['', 'nope', '[]', '{"type":3}', '{"type":"mystery"}', '{"type":"state"}', 42, null]) {
      expect(decodeServer(raw), isNull, reason: '$raw');
    }
  });

  test('encode makes a typed object', () {
    expect(jsonDecode(encode('confirm', {'decision': 'yes', 'interrupt_id': 'i1'})),
        {'type': 'confirm', 'decision': 'yes', 'interrupt_id': 'i1'});
    expect(jsonDecode(encode('cancel')), {'type': 'cancel'});
  });
}
