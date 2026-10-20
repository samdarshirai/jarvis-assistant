import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/keys_screen.dart';
import 'package:jarvis_app/theme.dart';

import 'auth_test.dart' show RecApi, meBody;

Future<void> _pump(WidgetTester t, RecApi api) async {
  t.view.physicalSize = const Size(360, 2000);
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.reset);
  await t.pumpWidget(MaterialApp(theme: jarvisTheme(), home: KeysScreen(api: api)));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('shows_last4_never_full_key', (t) async {
    await _pump(t, RecApi({'GET /me': meBody()}));
    expect(find.text('Set, ends in abcd'), findsOneWidget); // LLM
    expect(find.text('Set, ends in wxyz'), findsOneWidget); // Deepgram
    expect(find.text('Not set'), findsOneWidget); // Tavily
    expect(find.textContaining('https://openrouter.ai/api/v1'), findsWidgets);
    for (final f in t.widgetList<TextField>(find.byType(TextField))) {
      if (f.obscureText) expect(f.controller?.text ?? '', isEmpty); // key inputs start empty, never prefilled
    }
  });

  testWidgets('delete_optional_key', (t) async {
    var deleted = false;
    final api = RecApi({
      'GET /me': (Map<String, dynamic>? _) => meBody(deepgram: deleted ? null : 'wxyz'),
      'DELETE /settings/deepgram': (Map<String, dynamic>? _) {
        deleted = true;
        return <String, dynamic>{'ok': true};
      },
    });
    await _pump(t, api);
    await t.tap(find.byTooltip('Remove Deepgram key'));
    await t.pumpAndSettle();
    expect(api.paths, contains('DELETE /settings/deepgram'));
    expect(find.text('Set, ends in wxyz'), findsNothing);
    expect(find.text('Not set'), findsNWidgets(2));
  });

  testWidgets('replacing the LLM key preserves current advanced model ids', (t) async {
    final api = RecApi({'GET /me': meBody(), 'PUT /settings/llm': {'ok': true}});
    await _pump(t, api);
    await t.enterText(find.widgetWithText(TextField, 'API key'), 'replacement');
    await t.tap(find.text('Test and save'));
    await t.pumpAndSettle();
    final saved = api.calls.firstWhere((c) => c.$1 == 'PUT').$3!;
    expect(saved['model_fast'], 'fast/model');
    expect(saved['model_strong'], 'strong/model');
  });
}
