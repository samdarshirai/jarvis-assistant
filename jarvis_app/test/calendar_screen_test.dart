import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/calendar_screen.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/theme.dart';

class FakeApi implements ApiClient {
  FakeApi(this.onGet);
  final Future<Map<String, dynamic>> Function(String, Map<String, String>?) onGet;
  final calls = <Map<String, String>?>[];
  @override
  Config get config => throw UnimplementedError();
  @override
  Duration get timeout => Duration.zero;
  @override
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) {
    calls.add(query);
    return onGet(path, query);
  }

  @override
  Future<Map<String, dynamic>> post(String path, [Map<String, dynamic>? body]) => throw UnimplementedError();
}

final _now = DateTime(2026, 10, 7, 10, 30); // Wednesday; Monday is 5 Oct

const _events = {
  'events': [
    {'id': 'a', 'summary': 'Standup', 'start': '2026-10-07T11:00:00', 'end': '2026-10-07T11:45:00', 'location': 'Office', 'all_day': false},
    {'id': 'b', 'summary': 'Holiday', 'start': '2026-10-07', 'end': '2026-10-08', 'location': null, 'all_day': true},
  ],
};

Future<FakeApi> _pump(WidgetTester t, Map<String, dynamic>? data,
    {bool fail = false, VoidCallback? ask, double width = 320}) async {
  t.view.physicalSize = Size(width, 800);
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.reset);
  final api = FakeApi((_, _) async => fail ? throw Exception('down') : data!);
  await t.pumpWidget(MaterialApp(
      theme: jarvisTheme(), home: CalendarScreen(api: api, onAskJarvis: ask ?? () {}, now: () => _now)));
  await t.pumpAndSettle();
  return api;
}

void main() {
  testWidgets('loads the week and shows today', (t) async {
    final api = await _pump(t, _events);
    expect(api.calls.single, {'from': '2026-10-05', 'days': '7'});
    expect(find.text('October 2026'), findsOneWidget);
    expect(find.text('Now · 10:30'), findsOneWidget);
    expect(find.text('11:00'), findsOneWidget);
    expect(find.text('Standup'), findsOneWidget);
    expect(find.text('Next · Office · 45m'), findsOneWidget);
    expect(find.text('All day · Holiday'), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  testWidgets('tap expands location; other day is empty and asks Jarvis', (t) async {
    var asked = 0;
    await _pump(t, _events, ask: () => asked++);
    expect(find.byKey(const ValueKey('location-row')), findsNothing);
    await t.tap(find.byKey(const ValueKey('event-a')));
    await t.pump();
    expect(find.byKey(const ValueKey('location-row')), findsOneWidget);
    await t.tap(find.byKey(const ValueKey('day-9')));
    await t.pump();
    expect(find.text('Nothing scheduled'), findsOneWidget);
    expect(find.textContaining('Now ·'), findsNothing);
    await t.tap(find.text('Ask Jarvis to schedule something'));
    await t.tap(find.byTooltip('Add event'));
    expect(asked, 2);
  });

  testWidgets('error shows retry', (t) async {
    await _pump(t, null, fail: true);
    expect(find.text("Couldn't reach Jarvis"), findsOneWidget);
    expect(find.text('Retry'), findsOneWidget);
  });

  testWidgets('null events shows Unavailable', (t) async {
    await _pump(t, {'events': null});
    expect(find.text('Unavailable'), findsOneWidget);
  });
}
