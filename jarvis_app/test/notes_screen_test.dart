import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/notes_screen.dart';

import 'mail_screen_test.dart' show FakeApi;

void main() {
  Future<void> pump(WidgetTester t, FakeApi api, VoidCallback ask) async {
    await t.binding.setSurfaceSize(const Size(320, 640));
    await t.pumpWidget(MaterialApp(home: NotesScreen(api: api, onAskJarvis: ask)));
    await t.pumpAndSettle();
  }

  testWidgets('tiles and sheet', (t) async {
    final ago = DateTime.now().subtract(const Duration(hours: 3)).toIso8601String();
    await pump(
        t,
        FakeApi({
          '/notes': {
            'notes': [
              for (var i = 1; i <= 4; i++) {'id': i, 'title': 'Note $i', 'snippet': 'snip $i', 'updated_at': ago}
            ]
          },
          '/notes/1': {'body': 'Full body text'},
        }),
        () {});
    expect(find.text('Your notes'), findsOneWidget);
    expect(find.text('3h ago'), findsWidgets);
    await t.tap(find.text('Note 1'));
    await t.pumpAndSettle();
    expect(find.text('Full body text'), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  testWidgets('empty and mic', (t) async {
    var asked = 0;
    await pump(t, FakeApi({'/notes': {'notes': []}}), () => asked++);
    expect(find.text('No notes yet'), findsOneWidget);
    await t.tap(find.text('Dictate a note'));
    await t.tap(find.byIcon(Icons.mic));
    expect(asked, 2);
  });
}
