import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/app.dart';
import 'package:jarvis_app/onboarding_screen.dart';
import 'package:jarvis_app/theme.dart';
import 'package:jarvis_app/ui.dart';

import 'auth_test.dart' show MemStore, RecApi, meBody;
import 'dash_fixture.dart';

Future<void> _pump(WidgetTester t, Widget w) async {
  t.view.physicalSize = const Size(360, 800);
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.reset);
  await t.pumpWidget(MaterialApp(theme: jarvisTheme(), home: w));
}

FilledButton _button(WidgetTester t, String label) =>
    t.widget<FilledButton>(find.ancestor(of: find.text(label), matching: find.byWidgetPredicate((w) => w is FilledButton)));

void main() {
  testWidgets('shows_sign_in_first_when_no_token', (t) async {
    var started = 0;
    await t.pumpWidget(JarvisApp(store: MemStore(), apiFor: (_) => RecApi({}), links: const Stream.empty(), startWake: () async {}));
    await t.pump();
    expect(find.byType(OnboardingScreen), findsOneWidget);
    expect(find.text('Sign in with Google'), findsWidgets);
    expect(find.text('API key'), findsNothing);
    await t.pumpWidget(const SizedBox());

    await _pump(t, OnboardingScreen(signIn: () async => started++, onDone: () {}));
    await t.tap(find.text('Sign in with Google').last);
    await t.pumpAndSettle();
    expect(find.textContaining('tap Advanced, then Continue'), findsOneWidget);
    await t.tap(find.text('Continue to Google'));
    await t.pumpAndSettle();
    expect(started, 1);
  });

  testWidgets('rejection_message_shown', (t) async {
    await _pump(t, OnboardingScreen(signIn: () async {}, onDone: () {}, error: 'Sign-in failed, try again'));
    expect(find.text('Sign-in failed, try again'), findsOneWidget);
    expect(find.text('Not on the list? Ask the owner to add your email.'), findsOneWidget);
  });

  testWidgets('llm_step_blocks_continue_until_save_succeeds', (t) async {
    var tries = 0;
    final api = RecApi({
      'PUT /settings/llm': (Map<String, dynamic>? b) =>
          ++tries == 1 ? ApiError(422, {'error': 'rejected_key', 'model': null}, '/settings/llm') : {'ok': true}
    });
    await _pump(t, OnboardingScreen(step: OnboardingStep.llm, api: api, signIn: () async {}, onDone: () {}));
    expect(_button(t, 'Continue').onPressed, isNull);
    await t.enterText(find.widgetWithText(TextField, 'API key'), ' sk-test-1234 ');
    await t.tap(find.text('Test and save'));
    await t.pumpAndSettle();
    expect(find.textContaining('rejected this API key'), findsOneWidget);
    expect(_button(t, 'Continue').onPressed, isNull);
    await t.tap(find.text('Test and save'));
    await t.pumpAndSettle();
    expect(api.calls.last.$3!['api_key'], 'sk-test-1234');
    expect(api.calls.last.$3!['base_url'], 'https://openrouter.ai/api/v1');
    expect(_button(t, 'Continue').onPressed, isNotNull);
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    expect(find.text('Skip'), findsOneWidget);
  });

  testWidgets('llm_error_names_model', (t) async {
    final api = RecApi({
      'PUT /settings/llm': ApiError(422, {'error': 'model_not_found', 'model': 'openai/gpt-x'}, '/settings/llm')
    });
    await _pump(t, OnboardingScreen(step: OnboardingStep.llm, api: api, signIn: () async {}, onDone: () {}));
    await t.enterText(find.widgetWithText(TextField, 'API key'), 'k');
    await t.tap(find.text('Test and save'));
    await t.pumpAndSettle();
    expect(find.textContaining('openai/gpt-x'), findsOneWidget);
    expect(find.textContaining('does not offer this model'), findsOneWidget);
  });

  testWidgets('optional_step_can_be_skipped', (t) async {
    var done = 0;
    final api = RecApi({});
    await _pump(t, OnboardingScreen(step: OnboardingStep.optional, api: api, signIn: () async {}, onDone: () => done++));
    expect(find.textContaining('Deepgram'), findsWidgets);
    expect(find.textContaining('Tavily'), findsWidgets);
    await t.tap(find.text('Skip'));
    await t.pump();
    expect(done, 1);
    expect(api.calls, isEmpty);
  });

  testWidgets('lands_on_home_after_finish', (t) async {
    var wake = 0;
    var saved = false;
    final api = RecApi({
      'GET /me': (Map<String, dynamic>? _) => meBody(llm: saved),
      'PUT /settings/llm': (Map<String, dynamic>? _) { saved = true; return <String, dynamic>{'ok': true}; },
    });
    await t.pumpWidget(JarvisApp(
        store: MemStore({'token': 'tok'}),
        apiFor: (_) => api,
        links: const Stream.empty(),
        startWake: () async => wake++,
        dashboardFetch: (_) async => sampleDashboard(),
        alarm: () async => null));
    await t.pump();
    await t.pump();
    expect(find.widgetWithText(TextField, 'API key'), findsOneWidget); // signed in, no LLM key: chat and voice stay locked
    expect(find.byType(SessionScreen), findsNothing);
    expect(wake, 0);
    await t.enterText(find.widgetWithText(TextField, 'API key'), 'k');
    await t.tap(find.text('Test and save'));
    await t.pumpAndSettle();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    await t.tap(find.text('Finish'));
    await t.pump();
    await t.pump();
    expect(find.byType(SessionScreen), findsOneWidget);
    expect(wake, 1);
    await t.pumpWidget(const SizedBox());
  });
}
