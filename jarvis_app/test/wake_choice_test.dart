import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/prefs.dart';
import 'package:jarvis_app/theme.dart';
import 'package:jarvis_app/wake_choice_screen.dart';

import 'auth_test.dart' show MemStore;

void main() {
  testWidgets('switch is on by default; Continue persists the choice', (t) async {
    final prefs = Prefs(store: MemStore());
    await prefs.load();
    var done = 0;
    await t.pumpWidget(MaterialApp(theme: jarvisTheme(), home: WakeChoiceScreen(prefs: prefs, onDone: () => done++)));
    expect(find.textContaining('charging or on Android Auto'), findsOneWidget);
    expect(t.widget<Switch>(find.byType(Switch)).value, true);
    await t.tap(find.byType(Switch));
    await t.pump();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    expect(prefs.wakeEnabled, false);
    expect(prefs.wakeChosen, true);
    expect(done, 1);
  });
}
