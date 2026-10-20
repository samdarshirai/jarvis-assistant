import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/settings_screen.dart';
import 'package:jarvis_app/theme.dart';
import 'package:jarvis_app/keys_screen.dart';

import 'auth_test.dart' show RecApi, meBody;

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

  testWidgets('backend failure shows an error and nothing opens', (t) async {
    Uri? opened;
    await _pump(t, SettingsScreen(startAuth: () async => throw Exception('503'), open: (u) async => opened = u));
    await t.tap(find.text('Reconnect Google'));
    await t.pumpAndSettle();
    expect(opened, isNull);
    expect(find.textContaining("Couldn't start Google sign-in"), findsOneWidget);
  });
}
