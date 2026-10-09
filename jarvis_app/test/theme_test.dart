import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/capabilities.dart';
import 'package:jarvis_app/session.dart';
import 'package:jarvis_app/theme.dart';

void main() {
  test('theme is dark Material 3 with InstrumentSans', () {
    final t = jarvisTheme();
    expect(t.brightness, Brightness.dark);
    expect(t.useMaterial3, isTrue);
    expect(t.textTheme.bodyMedium?.fontFamily, 'InstrumentSans');
  });

  testWidgets('BlushCard shows title, trailing and child over the background', (t) async {
    await t.pumpWidget(MaterialApp(
        theme: jarvisTheme(),
        home: const Scaffold(
            body: CocoaBackground(child: BlushCard(title: 'Tasks', trailing: Text('3'), child: Text('body'))))));
    expect(find.text('Tasks'), findsOneWidget);
    expect(find.text('3'), findsOneWidget);
    expect(find.text('body'), findsOneWidget);
  });

  testWidgets('AmberOrb shows mic when idle, stop when listening, and taps', (t) async {
    var taps = 0;
    Widget orb(Phase p) =>
        MaterialApp(theme: jarvisTheme(), home: Scaffold(body: AmberOrb(phase: p, onTap: () => taps++)));
    await t.pumpWidget(orb(Phase.idle));
    expect(find.byIcon(Icons.mic), findsOneWidget);
    await t.tap(find.byType(AmberOrb));
    expect(taps, 1);
    await t.pumpWidget(orb(Phase.listening));
    await t.pump(const Duration(milliseconds: 300));
    expect(find.byIcon(Icons.stop), findsOneWidget);
  });

  testWidgets('CapabilitiesScreen renders every title on a small phone', (t) async {
    t.view.physicalSize = const Size(320, 568);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.reset);
    await t.pumpWidget(MaterialApp(theme: jarvisTheme(), home: const CapabilitiesScreen()));
    for (final (title, _) in capabilities) {
      await t.scrollUntilVisible(find.text(title), 200);
      expect(find.text(title), findsOneWidget);
    }
    expect(t.takeException(), isNull);
  });
}
