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
    'name is required before keys; failed save preserves text and retries',
    (t) async {
      final me = meBody(displayName: null, llm: false);
      var attempts = 0;
      final api = RecApi({
        'PUT /me': (Map<String, dynamic>? body) {
          if (++attempts == 1) return ApiError(503, null, '/me');
          me['display_name'] = body!['display_name'];
          return me;
        },
      });
      await _pump(
        t,
        OnboardingScreen(
          me: me,
          api: api,
          signIn: () async {},
          onDone: () async {},
        ),
      );
      expect(find.text('What should Jarvis call you?'), findsOneWidget);
      await _tap(t, 'Continue');
      expect(attempts, 0);
      await t.enterText(
        find.widgetWithText(TextField, 'Your name'),
        '  Élodie  ',
      );
      await _tap(t, 'Continue');
      expect(
        find.textContaining('Your name could not be saved'),
        findsOneWidget,
      );
      expect(
        t
            .widget<TextField>(find.widgetWithText(TextField, 'Your name'))
            .controller!
            .text,
        '  Élodie  ',
      );
      expect(find.widgetWithText(TextField, 'API key'), findsNothing);
      await _tap(t, 'Continue');
      expect(api.calls.last.$3, {'display_name': 'Élodie'});
      expect(find.widgetWithText(TextField, 'API key'), findsOneWidget);
    },
  );

  testWidgets(
    'existing unnamed account with complete keys saves and proceeds home',
    (t) async {
      final me = meBody(displayName: null);
      var done = 0;
      final api = RecApi({
        'PUT /me': (Map<String, dynamic>? body) {
          me['display_name'] = body!['display_name'];
          return me;
        },
      });
      await _pump(
        t,
        OnboardingScreen(
          me: me,
          api: api,
          signIn: () async {},
          onDone: () async {
            done++;
          },
        ),
      );
      await t.enterText(find.widgetWithText(TextField, 'Your name'), '李');
      await _tap(t, 'Continue');
      expect(done, 1);
    },
  );

  testWidgets(
    'authenticated unnamed account routes to name even with complete keys',
    (t) async {
      await t.pumpWidget(
        JarvisApp(
          store: MemStore({'token': 'tok'}),
          apiFor: (_) => RecApi({'GET /me': meBody(displayName: null)}),
          links: const Stream.empty(),
        ),
      );
      await t.pump();
      await t.pump();
      expect(find.text('What should Jarvis call you?'), findsOneWidget);
      expect(find.byType(SessionScreen), findsNothing);
      expect(AppHost.ready, isFalse);
      await t.pumpWidget(const SizedBox());
    },
  );

  testWidgets(
    'Google explains the one required key; cancellation permits retry',
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
      expect(find.textContaining('paste one API key'), findsOneWidget);
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
      final me = meBody(llm: false);
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
      expect(find.text('Google connected'), findsOneWidget);
      expect(find.text('Skip'), findsNothing);
      setup['llm'] = false; // changed elsewhere before completion
      await _tap(t, 'Start using Jarvis');
      expect(done, 0);
      expect(find.widgetWithText(TextField, 'API key'), findsOneWidget);
      tries = 1;
      await t.enterText(
        find.widgetWithText(TextField, 'API key'),
        'replacement',
      );
      await _tap(t, 'Test and save');
      await _tap(t, 'Continue');
      await _tap(t, 'Start using Jarvis');
      expect(done, 1);
    },
  );

  testWidgets('Ready opens the app after final setup recheck', (t) async {
    var saved = false;
    final api = RecApi({
      'GET /me': (Map<String, dynamic>? _) => meBody(llm: saved),
      'PUT /settings/llm': (Map<String, dynamic>? _) {
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
    await t.enterText(find.widgetWithText(TextField, 'API key'), 'secret');
    await _tap(t, 'Test and save');
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

  for (var mask = 0; mask < 2; mask++) {
    testWidgets('restart resumes first missing key for combination $mask', (
      t,
    ) async {
      final me = meBody(llm: mask == 1);
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
      if (mask == 1) {
        expect(find.byType(SessionScreen), findsOneWidget);
      } else {
        expect(find.byType(SessionScreen), findsNothing);
        final step = firstMissingStep(
          me['setup'] as Map,
          displayName: me['display_name'] as String?,
        );
        expect(
          find.textContaining('Step ${step.index + 1} of 4'),
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
