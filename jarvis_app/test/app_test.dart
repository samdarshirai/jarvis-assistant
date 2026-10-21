import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/app.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/onboarding_screen.dart';
import 'package:jarvis_app/ui.dart';

import 'auth_test.dart' show MemStore, RecApi, meBody;
import 'dash_fixture.dart';

class _Boom extends MemStore {
  @override
  Future<String?> read(String key) async => throw Exception('keystore');
}

void main() {
  var wake = 0, stopped = 0;
  setUp(() { wake = 0; stopped = 0; });

  JarvisApp app(MemStore store, RecApi api, {Stream<Uri> links = const Stream.empty(), List<Config>? configs}) => JarvisApp(
      store: store,
      apiFor: (c) {
        configs?.add(c);
        return api;
      },
      links: links,
      startWake: () async => wake++,
      stopWake: () async => stopped++,
      tz: () async => 'Asia/Kolkata',
      dashboardFetch: (_) async => sampleDashboard(),
      alarm: () async => null);

  testWidgets('throwing store shows the wizard, not a spinner', (t) async {
    await t.pumpWidget(app(_Boom(), RecApi({})));
    await t.pump();
    expect(find.byType(OnboardingScreen), findsOneWidget);
  });

  testWidgets('no_token_shows_wizard', (t) async {
    await t.pumpWidget(app(MemStore(), RecApi({})));
    await t.pump();
    expect(find.byType(OnboardingScreen), findsOneWidget);
    expect(find.byType(SessionScreen), findsNothing);
    expect(AppHost.controller, isNull);
  });

  testWidgets('wake_service_not_started_without_token', (t) async {
    await t.pumpWidget(app(MemStore(), RecApi({})));
    await t.pump();
    await t.pump();
    expect(wake, 0);
  });

  testWidgets('setup must be confirmed before chat or voice unlocks', (t) async {
    await t.pumpWidget(app(MemStore({'token': 'tok'}), RecApi({'GET /me': ApiError(503, null, '/me')})));
    await t.pump();
    await t.pump();
    expect(find.byType(SessionScreen), findsNothing);
    expect(wake, 0);
    expect(find.text('Retry'), findsOneWidget);
  });

  testWidgets('no Deepgram key returns to required setup', (t) async {
    await t.pumpWidget(app(MemStore({'token': 'tok'}), RecApi({'GET /me': meBody(deepgram: null)})));
    await t.pump();
    await t.pump();
    expect(find.byType(SessionScreen), findsNothing);
    expect(find.widgetWithText(TextField, 'Deepgram key'), findsOneWidget);
    expect(AppHost.controller, isNull);
    expect(wake, 0);
    expect(find.widgetWithText(TextField, 'Deepgram key'), findsOneWidget);
    await t.pumpWidget(const SizedBox());
  });

  testWidgets('token_goes_to_home', (t) async {
    await t.pumpWidget(app(MemStore({'token': 'tok'}), RecApi({'GET /me': meBody()})));
    await t.pump();
    await t.pump();
    expect(find.byType(SessionScreen), findsOneWidget);
    expect(find.text('Standup'), findsOneWidget);
    expect(wake, 1);
    await t.pumpWidget(const SizedBox());
  });

  testWidgets('chat 409 needs_setup re-checks /me and locks to the key step', (t) async {
    var llm = true;
    final api = RecApi({
      'GET /me': (Map<String, dynamic>? _) => meBody(llm: llm),
      'POST /chat': (Map<String, dynamic>? _) {
        llm = false; // key removed elsewhere
        return ApiError(409, {'error': 'needs_setup', 'missing': 'llm'}, '/chat');
      },
    });
    await t.pumpWidget(app(MemStore({'token': 'tok'}), api));
    await t.pump();
    await t.pump();
    await t.tap(find.byIcon(Icons.chat_bubble));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField), 'hi');
    await t.tap(find.byIcon(Icons.send));
    await t.pumpAndSettle();
    expect(find.byType(SessionScreen), findsNothing);
    expect(find.widgetWithText(TextField, 'API key'), findsOneWidget);
    expect(stopped, greaterThan(0));
    await t.pumpWidget(const SizedBox());
  });

  testWidgets('returning from key removal reloads setup and stops voice', (t) async {
    t.view.physicalSize = const Size(360, 2000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.reset);
    var removed = false;
    final api = RecApi({
      'GET /me': (Map<String, dynamic>? _) => meBody(deepgram: removed ? null : 'wxyz'),
      'DELETE /settings/deepgram': (Map<String, dynamic>? _) {
        removed = true;
        return <String, dynamic>{'ok': true};
      },
    });
    await t.pumpWidget(app(MemStore({'token': 'tok'}), api));
    await t.pump(); await t.pump();
    expect(AppHost.controller, isNotNull);
    await t.tap(find.byTooltip('Settings'));
    await t.pumpAndSettle();
    await t.tap(find.text('API keys'));
    await t.pumpAndSettle();
    await t.tap(find.byTooltip('Remove Deepgram key'));
    await t.pumpAndSettle();
    expect(AppHost.controller, isNull);
    expect(stopped, 1);
    expect(find.byType(OnboardingScreen), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Deepgram key'), findsOneWidget);
    await t.pumpWidget(const SizedBox());
  });

  testWidgets('a revoked token goes back to sign-in', (t) async {
    final store = MemStore({'token': 'old'});
    await t.pumpWidget(app(store, RecApi({'GET /me': ApiError(401, null, '/me')})));
    await t.pump();
    await t.pump();
    expect(find.text('Continue to Google'), findsOneWidget);
    expect(await store.read('token'), isNull);
  });

  testWidgets('Reconnect Google uses PKCE sign-in and claims the new token', (t) async {
    final links = StreamController<Uri>();
    final store = MemStore({'token': 'owner-token'});
    final opened = <Uri>[];
    final api = RecApi({
      'GET /me': meBody(),
      'POST /auth/start': {'nonce': 'reconnect', 'url': 'https://accounts.google.com/reconnect'},
      'POST /auth/claim': {'token': 'new-token'},
      'PUT /me': {'ok': true},
    });
    await t.pumpWidget(JarvisApp(store: store, apiFor: (_) => api, links: links.stream,
        open: (url) async => opened.add(url), tz: () async => 'Europe/Berlin',
        dashboardFetch: (_) async => sampleDashboard(), alarm: () async => null));
    await t.pump(); await t.pump();
    await t.tap(find.byTooltip('Settings'));
    await t.pumpAndSettle();
    await t.tap(find.text('Reconnect Google'));
    await t.pumpAndSettle();
    expect(opened, [Uri.parse('https://accounts.google.com/reconnect')]);
    expect((await store.pending())?.nonce, 'reconnect');
    links.add(Uri.parse('jarvis://signin?nonce=reconnect&code=finish'));
    for (var i = 0; i < 8; i++) { await t.pump(); }
    expect((await store.load()).token, 'new-token');
    expect(api.paths, isNot(contains('POST /google/auth/start')));
    expect(api.calls.lastWhere((c) => c.$2 == '/auth/claim').$3!['code'], 'finish');
    await links.close(); // before unmounting: after the app cancels its subscription, close() never completes
    await t.pumpWidget(const SizedBox());
  });

  testWidgets('dispose clears AppHost.controller', (t) async {
    await t.pumpWidget(app(MemStore({'token': 'tok'}), RecApi({'GET /me': meBody()})));
    await t.pump();
    await t.pump();
    expect(AppHost.controller, isNotNull);
    await t.pumpWidget(const SizedBox());
    expect(AppHost.controller, isNull);
  });

  testWidgets('a deep link for our own sign-in claims, sends the timezone and lands on the LLM step', (t) async {
    final links = StreamController<Uri>();
    final store = MemStore({'signin_nonce': 'n1', 'signin_verifier': 'v' * 64});
    final configs = <Config>[];
    final api = RecApi({
      'POST /auth/claim': {'token': 'devtok', 'needs_setup': true},
      'PUT /me': {'ok': true},
      'GET /me': meBody(llm: false),
    });
    await t.pumpWidget(app(store, api, links: links.stream, configs: configs));
    await t.pump();
    links.add(Uri.parse('jarvis://signin?nonce=evil&code=c'));
    await t.pump();
    await t.pump();
    expect(api.calls, isEmpty); // a link this install did not start is ignored, silently
    expect(find.textContaining('failed'), findsNothing);
    links.add(Uri.parse('jarvis://signin?nonce=n1&code=c'));
    for (var i = 0; i < 5; i++) {
      await t.pump();
    }
    expect(api.paths, ['POST /auth/claim', 'PUT /me', 'GET /me']);
    expect(api.calls[1].$3, {'tz': 'Asia/Kolkata'});
    expect(configs.last.token, 'devtok');
    expect(find.widgetWithText(TextField, 'API key'), findsOneWidget);
    expect(wake, 0);
    await links.close();
  });

  testWidgets('a failed claim says so with the rejection hint', (t) async {
    final links = StreamController<Uri>();
    final store = MemStore({'signin_nonce': 'n1', 'signin_verifier': 'v' * 64});
    final api = RecApi({'POST /auth/claim': ApiError(401, null, '/auth/claim')});
    await t.pumpWidget(app(store, api, links: links.stream));
    await t.pump();
    links.add(Uri.parse('jarvis://signin?nonce=n1&code=c'));
    for (var i = 0; i < 4; i++) {
      await t.pump();
    }
    expect(find.text('Sign-in failed, try again'), findsOneWidget);
    expect(find.text('Not on the list? Ask the owner to add your email.'), findsOneWidget);
    await links.close();
  });

  // A friend without Deepgram never opens /voice, so the push token goes over REST after push init.
  for (final (name, me) in [('no Deepgram key', meBody(deepgram: null)), ('no LLM key', meBody(llm: false, deepgram: null))]) {
    testWidgets('the push token is registered after push init with $name', (t) async {
      final order = <String>[];
      final refresh = StreamController<String>();
      final api = RecApi({
        'GET /me': me,
        'PUT /device/fcm': (Map<String, dynamic>? body) {
          order.add('put ${body!['token']}');
          return {'ok': true};
        },
      });
      await t.pumpWidget(JarvisApp(
          store: MemStore({'token': 'tok'}),
          apiFor: (_) => api,
          links: const Stream.empty(),
          startWake: () async {},
          stopWake: () async {},
          tz: () async => null,
          dashboardFetch: (_) async => sampleDashboard(),
          alarm: () async => null,
          pushInit: () async => order.add('init'),
          fcmToken: () async => 'fcm-1',
          fcmRefresh: () => refresh.stream));
      for (var i = 0; i < 4; i++) {
        await t.pump();
      }
      expect(order, ['init', 'put fcm-1']);
      refresh.add('fcm-2'); // Firebase rotated the token
      await t.pump();
      expect(order, ['init', 'put fcm-1', 'put fcm-2']);
      await t.pumpWidget(const SizedBox());
      unawaited(refresh.close()); // its done future never fires under fake async once the app cancelled its listener
    });
  }

  testWidgets('a failed push registration is silent', (t) async {
    final api = RecApi({'GET /me': meBody(), 'PUT /device/fcm': ApiError(503, null, '/device/fcm')});
    await t.pumpWidget(JarvisApp(
        store: MemStore({'token': 'tok'}),
        apiFor: (_) => api,
        links: const Stream.empty(),
        stopWake: () async {},
        tz: () async => null,
        dashboardFetch: (_) async => sampleDashboard(),
        alarm: () async => null,
        fcmToken: () async => 'fcm-1'));
    for (var i = 0; i < 4; i++) {
      await t.pump();
    }
    expect(api.paths, contains('PUT /device/fcm'));
    expect(find.byType(SessionScreen), findsOneWidget);
    await t.pumpWidget(const SizedBox());
  });
}
