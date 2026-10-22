import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/prefs.dart';
import 'package:jarvis_app/settings_screen.dart';
import 'package:jarvis_app/theme.dart';
import 'package:jarvis_app/keys_screen.dart';

import 'auth_test.dart' show MemStore, RecApi, meBody;

Future<void> _pump(WidgetTester t, SettingsScreen s) async {
  t.view.physicalSize = const Size(320, 800);
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.reset);
  await t.pumpWidget(MaterialApp(theme: jarvisTheme(), home: s));
}

void main() {
  testWidgets('API keys opens the account key settings', (t) async {
    await _pump(t, SettingsScreen(api: RecApi({'GET /me': meBody()}),
        startAuth: () async => 'https://accounts.google.com/x', open: (_) async {}));
    await t.tap(find.text('API keys'));
    await t.pumpAndSettle();
    expect(find.byType(KeysScreen), findsOneWidget);
    expect(find.text('Set, ends in abcd'), findsOneWidget);
  });
  testWidgets('Reconnect Google opens the backend consent url', (t) async {
    Uri? opened;
    await _pump(t, SettingsScreen(startAuth: () async => 'https://accounts.google.com/x?y=1', open: (u) async => opened = u));
    await t.tap(find.text('Reconnect Google'));
    await t.pumpAndSettle();
    expect(opened, Uri.parse('https://accounts.google.com/x?y=1'));
  });

  testWidgets('wake switch, night rows and hints show; toggling calls onWakeChanged once', (t) async {
    final prefs = Prefs(store: MemStore());
    await prefs.load();
    var changed = 0;
    await _pump(t, SettingsScreen(prefs: prefs, onWakeChanged: () async => changed++,
        startAuth: () async => '', open: (_) async {}));
    expect(find.text('Hey Jarvis wake word'), findsOneWidget);
    expect(find.text('11:00 PM'), findsOneWidget);
    expect(find.text('7:00 AM'), findsOneWidget);
    expect(find.text('Night hours: Jarvis shows replies as text and makes no sound.'), findsOneWidget);
    expect(find.text('Double-tap on Pixel'), findsOneWidget);
    expect(find.text('Settings > System > Gestures > Quick Tap, then choose Jarvis.'), findsOneWidget);
    await t.tap(find.byType(Switch));
    await t.pumpAndSettle();
    expect(prefs.wakeEnabled, false);
    expect(changed, 1);
  });

  testWidgets('backend failure shows an error and nothing opens', (t) async {
    Uri? opened;
    await _pump(t, SettingsScreen(startAuth: () async => throw Exception('503'), open: (u) async => opened = u));
    await t.tap(find.text('Reconnect Google'));
    await t.pumpAndSettle();
    expect(opened, isNull);
    expect(find.textContaining("Couldn't start Google sign-in"), findsOneWidget);
  });
}
