import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/dashboard.dart';
import 'package:jarvis_app/protocol.dart';
import 'package:jarvis_app/theme.dart';
import 'package:jarvis_app/ui.dart';

import 'dash_fixture.dart';
import 'mail_screen_test.dart' show FakeApi;
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

  testWidgets('apps button opens the capabilities list', (t) async {
    final r = Rig();
    await t.pumpWidget(MaterialApp(home: SessionScreen(controller: r.c)));
    await t.tap(find.byIcon(Icons.apps));
    await t.pumpAndSettle();
    expect(find.text('What Jarvis can do'), findsOneWidget);
    expect(find.text('Calendar'), findsOneWidget);
  });

  testWidgets('home shows the dashboard with the orb and the orb starts a session', (t) async {
    final sem = t.ensureSemantics();
    final r = Rig();
    final d = DashboardController(fetch: () async => sampleDashboard());
    await d.refresh();
    await t.pumpWidget(MaterialApp(home: SessionScreen(controller: r.c, dashboard: d)));
    expect(t.getSemantics(find.bySemanticsLabel('Talk')).flagsCollection.isButton, isTrue);
    expect(find.text('Standup'), findsOneWidget);
    expect(find.text('Say "Hey Jarvis"'), findsOneWidget);
    await t.tap(find.byTooltip('Talk'));
    await t.pump();
    expect(r.started, 1);
    expect(find.byTooltip('Stop'), findsOneWidget);
    sem.dispose();
    expect(t.getSemantics(find.bySemanticsLabel('Stop')).flagsCollection.isButton, isTrue);
    r.c.stop();
    await t.pump();
  });

  testWidgets('Play on the brief card speaks the brief text through the session', (t) async {
    final r = Rig();
    final d = DashboardController(fetch: () async => sampleDashboard());
    await d.refresh();
    await t.pumpWidget(MaterialApp(home: SessionScreen(controller: r.c, dashboard: d)));
    await t.tap(find.byTooltip('Play'));
    await t.pump();
    await t.pump();
    expect(r.socket.sent.where((e) => e.$1 == 'speak').single.$2, {'text': 'Calendar today: 09:00 Standup.'});
    r.c.stop();
    await t.pump();
  });

  testWidgets('small phone with all sections, long text and an open confirm card does not overflow', (t) async {
    t.view.physicalSize = const Size(360 * 3, 640 * 3);
    t.view.devicePixelRatio = 3;
    addTearDown(t.view.reset);
    final r = Rig();
    final d = DashboardController(fetch: () async => sampleDashboard(), alarm: () async => DateTime(2026, 10, 6, 6, 30));
    await d.refresh();
    await t.pumpWidget(MaterialApp(home: SessionScreen(controller: r.c, dashboard: d)));
    await r.c.start();
    r.socket.ctrl.add(ConfirmCardEvent(interruptId: 'i', summary: 'Create ${'Gym ' * 80}', tapOnly: false, afterUntrusted: true));
    r.socket.ctrl.add(TranscriptEvent('user', 'word ' * 120, true));
    await t.pump();
    expect(t.takeException(), isNull);
    expect(find.text('Confirm'), findsOneWidget);
    for (final f in [find.byTooltip('Stop'), find.text('Confirm')]) {
      final rect = t.getRect(f);
      expect(rect.top, greaterThanOrEqualTo(0));
      expect(rect.bottom, lessThanOrEqualTo(640));
    }
    r.c.stop();
    await t.pump();
  });

  testWidgets('pairing screen is on the cocoa background and still saves', (t) async {
    Config? saved;
    await t.pumpWidget(MaterialApp(theme: jarvisTheme(), home: PairingScreen(onSaved: (c) => saved = c)));
    expect(find.byType(CocoaBackground), findsOneWidget);
    await t.enterText(find.byType(TextField).first, ' https://x.test ');
    await t.enterText(find.byType(TextField).last, ' tok ');
    await t.tap(find.text('Pair'));
    expect(saved?.url, 'https://x.test');
    expect(saved?.token, 'tok');
  });

  testWidgets('with an api, tapping Inbox opens the mail screen and the chat button opens chat', (t) async {
    final r = Rig();
    final d = DashboardController(fetch: () async => sampleDashboard());
    await d.refresh();
    final api = FakeApi({
      '/mail': {'items': [], 'count': 0, 'more': false},
    });
    await t.pumpWidget(MaterialApp(home: SessionScreen(controller: r.c, dashboard: d, api: api)));
    await t.tap(find.text('Inbox'));
    await t.pumpAndSettle();
    expect(api.calls, contains('/mail'));
    await t.pageBack();
    await t.pumpAndSettle();
    await t.tap(find.byTooltip('Chat'));
    await t.pumpAndSettle();
    expect(find.text('Write here…'), findsOneWidget);
  });

  testWidgets('voice view shows quick-start chips and a chip sends its text to chat', (t) async {
    final r = Rig();
    final api = FakeApi({});
    await t.pumpWidget(MaterialApp(home: SessionScreen(controller: r.c, api: api)));
    await r.c.start();
    await t.pump();
    expect(find.text('Write an email'), findsOneWidget);
    await t.tap(find.text('Write an email'));
    await t.pump();
    await t.pump();
    expect(find.text('Write here…'), findsOneWidget);
    expect(find.text('Write an email'), findsWidgets); // sent as a user bubble
  });
}
