import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/alarms_screen.dart';
import 'package:jarvis_app/theme.dart';

final _now = DateTime(2026, 10, 7, 10);

Future<void> _pump(WidgetTester t, DateTime? at, VoidCallback ask) async {
  t.view.physicalSize = const Size(320, 800);
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.reset);
  await t.pumpWidget(MaterialApp(
      theme: jarvisTheme(), home: AlarmsScreen(alarm: () async => at, onAskJarvis: ask, now: () => _now)));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('shows next alarm and asks Jarvis', (t) async {
    var asked = 0;
    await _pump(t, DateTime(2026, 10, 8, 6, 30), () => asked++);
    expect(find.text('06:30'), findsOneWidget);
    expect(find.text('Tomorrow'), findsOneWidget);
    await t.tap(find.text('Set an alarm with Jarvis'));
    await t.tap(find.byTooltip('Set an alarm'));
    expect(asked, 2);
    expect(t.takeException(), isNull);
  });

  testWidgets('no alarm', (t) async {
    await _pump(t, null, () {});
    expect(find.text('No alarm set'), findsOneWidget);
  });
}
