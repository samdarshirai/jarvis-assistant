import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/protocol.dart';
import 'package:jarvis_app/ui.dart';

import 'session_rig.dart';

void main() {
  testWidgets('shows phase, transcripts and the confirm card with working buttons', (t) async {
    final r = Rig();
    await t.pumpWidget(MaterialApp(home: SessionScreen(controller: r.c)));
    await r.c.start();
    r.socket.ctrl.add(const TranscriptEvent('user', 'put gym at seven', true));
    r.socket.ctrl.add(const ConfirmCardEvent(
        interruptId: 'i1', summary: 'Create Gym', tapOnly: false, afterUntrusted: true));
    await t.pump();
    expect(find.text('put gym at seven'), findsOneWidget);
    expect(find.textContaining('Create Gym'), findsOneWidget);
    expect(find.textContaining('after reading third-party content (email or web)'), findsOneWidget);
    await t.tap(find.text('Confirm'));
    await t.pump();
    expect(r.socket.sent.last.$1, 'confirm');
    expect(r.socket.sent.last.$2, {'decision': 'yes', 'interrupt_id': 'i1'});
    expect(find.text('Confirm'), findsNothing);
    r.c.stop(); // cancel the silence timer so the test ends cleanly
    await t.pump();
  });
}
