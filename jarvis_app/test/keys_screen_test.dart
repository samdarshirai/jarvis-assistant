import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
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
    expect(find.textContaining('Deepgram'), findsNothing);
    expect(find.textContaining('Tavily'), findsNothing);
    expect(find.textContaining('https://openrouter.ai/api/v1'), findsWidgets);
    for (final f in t.widgetList<TextField>(find.byType(TextField))) {
      if (f.obscureText) expect(f.controller?.text ?? '', isEmpty); // key inputs start empty, never prefilled
    }
  });

  testWidgets('prefilled default model ids are not sent; edited ones are', (t) async {
    final api = RecApi({'GET /me': meBody(), 'PUT /settings/llm': {'ok': true}});
    await _pump(t, api);
    await t.enterText(find.widgetWithText(TextField, 'API key'), 'replacement');
    await t.tap(find.text('Test and save'));
    await t.pumpAndSettle();
    final first = api.calls.firstWhere((c) => c.$1 == 'PUT').$3!;
    expect(first.containsKey('model_fast'), isFalse);
    expect(first.containsKey('model_strong'), isFalse);
    expect(find.text('AI key works. Saved.'), findsOneWidget); // survives the /me refetch

    await t.tap(find.text('Advanced'));
    await t.pumpAndSettle();
    await t.enterText(find.widgetWithText(TextField, 'Fast model id'), 'my/fast');
    await t.enterText(find.widgetWithText(TextField, 'API key'), 'again');
    await t.tap(find.text('Test and save'));
    await t.pumpAndSettle();
    final second = api.calls.lastWhere((c) => c.$1 == 'PUT').$3!;
    expect(second['model_fast'], 'my/fast');
    expect(second.containsKey('model_strong'), isFalse);
  });

  test('keyErrorText maps every backend code', () {
    String text(String code, [String? model]) => keyErrorText(ApiError(422, {'error': code, 'model': model}, '/settings/llm'));
    expect(text('rejected_key'), 'The provider rejected this API key.');
    expect(text('out_of_credit'), 'The account behind this key is out of credit.');
    expect(text('model_not_found', 'x/y'), 'The provider does not offer this model (model: x/y).');
    expect(text('bad_url'), startsWith('That base URL is not allowed'));
    expect(text('unreachable'), 'Could not reach the provider at that base URL.');
    expect(text('no_models'), 'Enter a fast and a strong model id under Advanced.');
    expect(text('bad_request'), 'Check the model ids (up to 5, comma-separated) and the key.');
    expect(text('mystery'), 'That key was not accepted.');
  });
}
