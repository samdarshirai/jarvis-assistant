import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/dashboard.dart';
import 'package:jarvis_app/dashboard_cards.dart';
import 'package:jarvis_app/theme.dart';

import 'dash_fixture.dart';

final _now = DateTime(2026, 10, 5, 8, 0);

Future<DashboardController> _loaded(WidgetTester t,
    {DashboardData? data, DateTime? alarm, bool fail = false, void Function(String)? onPlay}) async {
  final c = DashboardController(
      fetch: () async => fail ? throw Exception('down') : (data ?? sampleDashboard()), alarm: () async => alarm);
  await c.refresh();
  await t.pumpWidget(MaterialApp(
      theme: jarvisTheme(),
      home: Scaffold(
          body: SingleChildScrollView(
              child: DashboardSections(controller: c, onPlayBrief: onPlay ?? (_) {}, now: () => _now)))));
  return c;
}

void main() {
  test('helpers', () {
    expect(greeting(DateTime(2026, 1, 1, 8)), 'Good morning');
    expect(greeting(DateTime(2026, 1, 1, 13)), 'Good afternoon');
    expect(greeting(DateTime(2026, 1, 1, 20)), 'Good evening');
    expect(dateLine(DateTime(2026, 10, 5)), 'Monday, 5 October');
    expect(hhmm(DateTime(2026, 1, 1, 6, 5)), '06:05');
    expect(dayLabel(DateTime(2026, 10, 5, 23), DateTime(2026, 10, 5, 8)), 'Today');
    expect(dayLabel(DateTime(2026, 10, 6, 6), DateTime(2026, 10, 5, 8)), 'Tomorrow');
    expect(dayLabel(DateTime(2026, 10, 9, 6), DateTime(2026, 10, 5, 8)), 'Friday');
  });

  testWidgets('shows every section with its data', (t) async {
    await _loaded(t, alarm: DateTime(2026, 10, 6, 6, 30));
    expect(find.text('Good morning'), findsOneWidget);
    expect(find.text('Monday, 5 October'), findsOneWidget);
    expect(find.text('Morning brief'), findsOneWidget);
    expect(find.textContaining('09:00 Standup'), findsOneWidget);
    expect(find.text('Standup'), findsOneWidget);
    expect(find.text('09:00'), findsOneWidget);
    expect(find.text('All day'), findsOneWidget);
    expect(find.text('Pay rent'), findsOneWidget);
    expect(find.text('Overdue'), findsOneWidget);
    expect(find.text('5+ unread'), findsOneWidget);
    expect(find.text('Boss'), findsOneWidget);
    expect(find.text('Contract'), findsOneWidget);
    expect(find.text('Gym plan'), findsOneWidget);
    expect(find.text('06:30 · Tomorrow'), findsOneWidget);
    expect(find.text('Offline'), findsNothing);
  });

  testWidgets('a null section says Unavailable and the others still render', (t) async {
    await _loaded(t, data: const DashboardData(tasks: [TaskItem(title: 'Pay rent', overdue: false)]));
    expect(find.text('Pay rent'), findsOneWidget);
    expect(find.text('Unavailable'), findsAtLeastNWidgets(3)); // events, mail, notes (+ brief)
  });

  testWidgets('empty lists and no alarm show friendly text', (t) async {
    await _loaded(t, data: const DashboardData(events: [], tasks: [], unread: UnreadMail(count: 0, more: false, items: []), notes: [], brief: 'x'));
    for (final s in ['No events today', 'No pending tasks', 'No unread email', 'No notes yet', 'No alarm set']) {
      expect(find.text(s), findsOneWidget);
    }
  });

  testWidgets('a long mail subject and event title do not overflow a narrow phone', (t) async {
    t.view.physicalSize = const Size(360 * 3, 640 * 3);
    t.view.devicePixelRatio = 3;
    addTearDown(t.view.reset);
    final long = 'x' * 300;
    await _loaded(t, data: DashboardData.fromJson({
      'events': [{'summary': long, 'start': '2026-10-05T09:00:00', 'location': long}],
      'unread': {'count': 1, 'more': false, 'items': [{'from': long, 'subject': long}]},
      'brief': long,
    }));
    expect(t.takeException(), isNull);
  });

  testWidgets('offline with cached data keeps the cards and shows the chip', (t) async {
    var fail = false;
    final c = DashboardController(fetch: () async => fail ? throw Exception('down') : sampleDashboard());
    await c.refresh();
    await t.pumpWidget(MaterialApp(
        theme: jarvisTheme(),
        home: Scaffold(
            body: SingleChildScrollView(child: DashboardSections(controller: c, onPlayBrief: (_) {}, now: () => _now)))));
    fail = true;
    await c.refresh();
    await t.pump();
    expect(find.text('Offline'), findsOneWidget);
    expect(find.text('Standup'), findsOneWidget);
  });

  testWidgets('first load failing shows Retry, which refetches', (t) async {
    var calls = 0;
    final c = DashboardController(fetch: () async {
      calls++;
      throw Exception('down');
    });
    await c.refresh();
    await t.pumpWidget(MaterialApp(
        theme: jarvisTheme(),
        home: Scaffold(body: DashboardSections(controller: c, onPlayBrief: (_) {}, now: () => _now))));
    expect(find.text("Couldn't reach Jarvis"), findsOneWidget);
    await t.tap(find.text('Retry'));
    await t.pump();
    expect(calls, 2);
  });

  testWidgets('re-consent banner and Play button', (t) async {
    String? played;
    await _loaded(t,
        data: DashboardData.fromJson({...sampleJson, 'reauth': true}), onPlay: (s) => played = s);
    expect(find.textContaining('Google needs re-consent'), findsOneWidget);
    await t.tap(find.text('Play'));
    expect(played, 'Calendar today: 09:00 Standup.');
  });
}
