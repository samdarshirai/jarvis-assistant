import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/app.dart';
import 'package:jarvis_app/onboarding_screen.dart';
import 'package:jarvis_app/theme.dart';
import 'package:jarvis_app/ui.dart';

import 'auth_test.dart' show MemStore, RecApi, meBody;
import 'dash_fixture.dart';

class PendingSaveApi extends RecApi {
  PendingSaveApi(this.me) : super({});
  final Map<String, dynamic> me;
  Completer<Map<String, dynamic>> pending = Completer();
  @override
  Future<Map<String, dynamic>> get(
    String path, [
    Map<String, String>? query,
  ]) async => me;
  @override
  Future<Map<String, dynamic>> put(String path, Map<String, dynamic> body) =>
      pending.future;
}

Future<void> _pump(WidgetTester t, Widget w) async {
  t.view.physicalSize = const Size(360, 1100);
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.reset);
  await t.pumpWidget(MaterialApp(theme: jarvisTheme(), home: w));
}

Future<void> _tap(WidgetTester t, String label) async {
  final f = find.text(label).last;
  await t.ensureVisible(f);
  await t.pumpAndSettle();
  await t.tap(f);
  await t.pumpAndSettle();
}

FilledButton _button(WidgetTester t, String label) => t.widget<FilledButton>(
  find.ancestor(of: find.text(label), matching: find.byType(FilledButton)),
);

void main() {
  testWidgets(
    'Google explains all required services; cancellation permits retry',
    (t) async {
      var started = 0;
      await _pump(
        t,
        OnboardingScreen(
          signIn: () async {
            started++;
          },
          onDone: () async {},
        ),
      );
      expect(find.textContaining('three required API keys'), findsOneWidget);
      await _tap(t, 'Before you connect');
      expect(
        find.textContaining('tap Advanced, then Continue'),
        findsOneWidget,
      );
      await _tap(t, 'Continue to Google');
      expect(started, 1);
      expect(find.textContaining('If you cancelled'), findsOneWidget);
      await _tap(t, 'Continue to Google');
      expect(started, 2);
    },
  );

  testWidgets('Google launch failure and rejection stay inline', (t) async {
    await _pump(
      t,
      OnboardingScreen(
        signIn: () async => throw Exception('offline'),
        onDone: () async {},
        error: 'Sign-in failed, try again',
      ),
    );
    expect(find.text(rejectionHint), findsOneWidget);
    await _tap(t, 'Continue to Google');
    expect(find.textContaining("Couldn't start Google"), findsOneWidget);
    expect(find.text('Sign-in failed, try again'), findsOneWidget);
  });

  testWidgets(
    'all saves required; retries, masking, back and final server check',
    (t) async {
      var tries = 0, done = 0;
      final me = meBody(llm: false, deepgram: null, tavily: null);
      final setup = me['setup'] as Map;
      final api = RecApi({
        'GET /me': (Map<String, dynamic>? _) => me,
        'PUT /settings/llm': (Map<String, dynamic>? _) {
          if (++tries == 1) {
            return ApiError(422, {'error': 'rejected_key'}, '/settings/llm');
          }
          setup['llm'] = true;
          return {'ok': true};
        },
        for (final name in ['deepgram', 'tavily'])
          'PUT /settings/$name': (Map<String, dynamic>? _) {
            setup[name] = true;
            return {'ok': true};
          },
      });
      await _pump(
        t,
        OnboardingScreen(
          api: api,
          me: me,
          signIn: () async {},
          onDone: () async {
            done++;
          },
        ),
      );
      expect(_button(t, 'Continue').onPressed, isNull);
      await t.enterText(
        find.widgetWithText(TextField, 'API key'),
        ' sk-test-1234 ',
      );
      await _tap(t, 'Test and save');
      expect(find.textContaining('rejected this API key'), findsOneWidget);
      expect(_button(t, 'Continue').onPressed, isNull);
      await _tap(t, 'Test and save');
      expect(api.calls.first.$3!['api_key'], 'sk-test-1234');
      expect(
        t
            .widget<TextField>(find.widgetWithText(TextField, 'API key'))
            .controller!
            .text,
        isEmpty,
      );
      await _tap(t, 'Continue');
      expect(find.widgetWithText(TextField, 'Deepgram key'), findsOneWidget);
      expect(_button(t, 'Continue').onPressed, isNull);
      await t.enterText(
        find.widgetWithText(TextField, 'Deepgram key'),
        'deep-secret',
      );
      expect(
        t
            .widget<TextField>(find.widgetWithText(TextField, 'Deepgram key'))
            .obscureText,
        isTrue,
      );
      await t.tap(find.byTooltip('Show Deepgram key'));
      await t.pump();
      expect(
        t
            .widget<TextField>(find.widgetWithText(TextField, 'Deepgram key'))
            .obscureText,
        isFalse,
      );
      await _tap(t, 'Save');
      expect(find.text('Saved.'), findsOneWidget);
      await _tap(t, 'Continue');
      await _tap(t, 'Back');
      expect(find.textContaining('•••• Saved'), findsOneWidget);
      await _tap(t, 'Continue');
      await t.enterText(
        find.widgetWithText(TextField, 'Tavily key'),
        'tav-secret',
      );
      await _tap(t, 'Save');
      await _tap(t, 'Continue');
      expect(find.text('Google connected'), findsOneWidget);
      expect(find.text('Skip'), findsNothing);
      setup['tavily'] = false; // changed elsewhere before completion
      await _tap(t, 'Start using Jarvis');
      expect(done, 0);
      expect(find.widgetWithText(TextField, 'Tavily key'), findsOneWidget);
      await t.enterText(
        find.widgetWithText(TextField, 'Tavily key'),
        'replacement',
      );
      await _tap(t, 'Save');
      await _tap(t, 'Continue');
      await _tap(t, 'Start using Jarvis');
      expect(done, 1);
    },
  );

  for (final name in ['deepgram', 'tavily']) {
    testWidgets(
      '$name blocks navigation during save and supports failed-save retry',
      (t) async {
        final me = meBody(
          deepgram: name == 'deepgram' ? null : 'saved',
          tavily: null,
        );
        final api = PendingSaveApi(me);
        await _pump(
          t,
          OnboardingScreen(
            me: me,
            api: api,
            signIn: () async {},
            onDone: () async {},
          ),
        );
        final label = name == 'deepgram' ? 'Deepgram key' : 'Tavily key';
        await t.enterText(find.widgetWithText(TextField, label), 'secret');
        await t.ensureVisible(find.text('Save'));
        await t.pumpAndSettle();
        await t.tap(find.text('Save'));
        await t.pump();
        expect(
          t.widget<TextField>(find.widgetWithText(TextField, label)).enabled,
          isFalse,
        );
        expect(
          t
              .widget<TextButton>(find.widgetWithText(TextButton, 'Back'))
              .onPressed,
          isNull,
        );
        expect(_button(t, 'Please wait…').onPressed, isNull);
        api.pending.completeError(ApiError(503, null, '/settings/$name'));
        await t.pumpAndSettle();
        expect(find.textContaining('could not be saved'), findsOneWidget);
        expect(_button(t, 'Continue').onPressed, isNull);
        api.pending = Completer();
        await t.tap(find.text('Save'));
        await t.pump();
        (me['setup'] as Map)[name] = true;
        api.pending.complete({'ok': true});
        await t.pumpAndSettle();
        expect(find.text('Saved.'), findsOneWidget);
        expect(_button(t, 'Continue').onPressed, isNotNull);
        expect(
          t
              .widget<TextField>(find.widgetWithText(TextField, label))
              .controller!
              .text,
          isEmpty,
        );
      },
    );
  }

  testWidgets('Ready opens the app after final setup recheck', (t) async {
    var saved = false;
    final api = RecApi({
      'GET /me': (Map<String, dynamic>? _) =>
          meBody(tavily: saved ? 'saved' : null),
      'PUT /settings/tavily': (Map<String, dynamic>? _) {
        saved = true;
        return {'ok': true};
      },
    });
    t.view.physicalSize = const Size(360, 1100);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.reset);
    await t.pumpWidget(
      JarvisApp(
        store: MemStore({'token': 'tok'}),
        apiFor: (_) => api,
        links: const Stream.empty(),
        dashboardFetch: (_) async => sampleDashboard(),
        alarm: () async => null,
      ),
    );
    await t.pump();
    await t.pump();
    await t.enterText(find.widgetWithText(TextField, 'Tavily key'), 'secret');
    await _tap(t, 'Save');
    await _tap(t, 'Continue');
    expect(find.byType(SessionScreen), findsNothing);
    await t.ensureVisible(find.text('Start using Jarvis'));
    await t.pumpAndSettle();
    await t.tap(find.text('Start using Jarvis'));
    await t.pump();
    await t.pump();
    expect(find.byType(SessionScreen), findsOneWidget);
    await t.pumpWidget(const SizedBox());
  });

  for (var mask = 0; mask < 8; mask++) {
    testWidgets('restart resumes first missing key for combination $mask', (
      t,
    ) async {
      final me = meBody(
        llm: mask & 1 != 0,
        deepgram: mask & 2 != 0 ? 'deep' : null,
        tavily: mask & 4 != 0 ? 'tav' : null,
      );
      await t.pumpWidget(
        JarvisApp(
          store: MemStore({'token': 'tok'}),
          apiFor: (_) => RecApi({'GET /me': me}),
          links: const Stream.empty(),
          dashboardFetch: (_) async => sampleDashboard(),
          alarm: () async => null,
        ),
      );
      await t.pump();
      await t.pump();
      if (mask == 7) {
        expect(find.byType(SessionScreen), findsOneWidget);
      } else {
        expect(find.byType(SessionScreen), findsNothing);
        final step = firstMissingStep(me['setup'] as Map);
        expect(
          find.textContaining('Step ${step.index + 1} of 5'),
          findsOneWidget,
        );
        expect(AppHost.controller, isNull);
      }
      await t.pumpWidget(const SizedBox());
    });
  }

  testWidgets('small screen and large text allow keyboard scrolling', (
    t,
  ) async {
    await _pump(
      t,
      OnboardingScreen(
        me: meBody(llm: false),
        api: RecApi({}),
        signIn: () async {},
        onDone: () async {},
      ),
    );
    t.view.physicalSize = const Size(320, 568);
    t.view.viewInsets = const FakeViewPadding(bottom: 240);
    await t.pumpWidget(
      MaterialApp(
        theme: jarvisTheme(),
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context)
              .copyWith(textScaler: const TextScaler.linear(2)),
          child: child!,
        ),
        home: OnboardingScreen(
          me: meBody(llm: false),
          api: RecApi({}),
          signIn: () async {},
          onDone: () async {},
        ),
      ),
    );
    await t.pumpAndSettle();
    await t.scrollUntilVisible(
      find.text('Test and save'),
      100,
      scrollable: find.byType(Scrollable).first,
    );
    await t.pumpAndSettle();
    expect(find.byTooltip('Show API key'), findsOneWidget);
    expect(t.takeException(), isNull);
  });
}
