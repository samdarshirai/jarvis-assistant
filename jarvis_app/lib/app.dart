import 'dart:async';

import 'package:app_links/app_links.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'alarm.dart';
import 'api.dart';
import 'audio.dart';
import 'auth.dart';
import 'config.dart';
import 'dashboard.dart';
import 'onboarding_screen.dart';
import 'phone.dart';
import 'session.dart';
import 'store.dart';
import 'theme.dart';
import 'ui.dart';
import 'ws_socket.dart';

/// Lets the wake-word, assistant and push entry points start a session on the one controller.
class AppHost {
  static SessionController? controller;
  static bool ready = false;
  static DateTime Function() now = DateTime.now; // injectable for tests
  static const pendingTtl = Duration(seconds: 30);
  static ({String? speakText, DateTime at})? _pending;

  /// Cold-start entry points can fire before the controller exists: remember the last request and run it on attach.
  static Future<void> startSession({String? speakText}) async {
    if (!ready) return;
    final c = controller;
    if (c != null) return c.start(speakText: speakText);
    _pending = (speakText: speakText, at: now());
  }

  /// Sets the controller (null clears it and any pending request) and drains a fresh pending request exactly once.
  static void attach(SessionController? c) {
    controller = c;
    if (c == null) {
      _pending = null;
      return;
    }
    final p = _pending;
    _pending = null;
    if (p == null || now().difference(p.at) > pendingTtl) return;
    scheduleMicrotask(() {
      if (controller == c) c.start(speakText: p.speakText);
    });
  }
}

class JarvisApp extends StatefulWidget {
  const JarvisApp(
      {super.key,
      this.onSessionEnded,
      this.onSessionStarted,
      this.fcmToken,
      this.pushInit,
      this.fcmRefresh,
      this.store = const ConfigStore(),
      this.apiFor = ApiClient.new,
      this.links,
      this.startWake,
      this.stopWake,
      this.onReady,
      this.open = openInBrowser,
      this.tz = deviceTimezone,
      this.dashboardFetch,
      this.alarm});
  final VoidCallback? onSessionEnded;
  final VoidCallback? onSessionStarted;
  final Future<String?> Function()? fcmToken;
  final Future<void> Function()? pushInit; // Firebase setup; the token is registered over REST after it
  final Stream<String> Function()? fcmRefresh; // Firebase's token rotations
  final ConfigStore store;
  final ApiClient Function(Config) apiFor;
  final Stream<Uri>? links; // jarvis://signin deep links; null: the platform's (app_links)
  final Future<void> Function()? startWake; // wake-word service; only started once signed in and set up
  final Future<void> Function()? stopWake;
  final Future<void> Function(bool voice)? onReady;
  final Future<void> Function(Uri) open;
  final Future<String?> Function() tz;
  final Future<DashboardData> Function(Config)? dashboardFetch;
  final Future<DateTime?> Function()? alarm;
  @override
  State<JarvisApp> createState() => _JarvisAppState();
}

class _JarvisAppState extends State<JarvisApp> with WidgetsBindingObserver {
  ConfigStore get _store => widget.store;
  Config? _config; // null while loading; a null token shows the wizard
  bool _ready = false; // signed in and /me says the LLM key is set: chat and voice unlock
  bool _voiceReady = false;
  String? _setupError;
  int _routeGeneration = 0;
  String? _signInError;
  SessionController? _controller;
  DashboardController? _dashboard;
  StreamSubscription<Uri>? _links;
  StreamSubscription<String>? _fcmRefresh;
  final _messenger = GlobalKey<ScaffoldMessengerState>();
  late final _auth = AuthClient(widget.apiFor(Config(_store.url, null)), _store);
  late final Future<void> _initialized;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _initialized = _store.load().catchError((Object _) => Config(_store.url, null)).then(_route);
    _links = (widget.links ?? AppLinks().uriLinkStream).listen((link) async {
      await _initialized;
      if (mounted) await _onLink(link);
    });
    _initialized.then((_) async {
      if (!mounted) return;
      if (widget.links == null) {
        final initial = await AppLinks().getInitialLink();
        if (initial != null && mounted) await _onLink(initial);
      }
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _links?.cancel();
    _fcmRefresh?.cancel();
    _controller?.dispose(); // before the dashboard: its onEnded refreshes it
    _dashboard?.dispose();
    if (AppHost.controller == _controller) AppHost.attach(null);
    AppHost.ready = false;
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _dashboard?.refresh();
  }

  /// Decides wizard or home for [c]: no token is sign-in, a token without an LLM key is the key step.
  Future<void> _route(Config c) async {
    final generation = ++_routeGeneration;
    var ready = false, voiceReady = false;
    String? setupError;
    if (c.token != null) {
      try {
        final setup = (await widget.apiFor(c).get('/me'))['setup'] as Map?;
        if (setup == null) throw const FormatException('Missing setup');
        ready = setup['llm'] == true;
        voiceReady = ready && setup['deepgram'] == true;
      } on ApiError catch (e) {
        if (e.status != 401) {
          setupError = 'Could not check your setup. Check your connection and try again.';
        } else {
          if (generation != _routeGeneration) return;
          c = Config(c.url, null); // revoked: sign in again
          await _store.saveToken(null).catchError((Object _) {});
        }
      } catch (_) {
        setupError = 'Could not check your setup. Check your connection and try again.';
      }
    }
    if (!mounted || generation != _routeGeneration) return;
    setState(() {
      _setupError = setupError;
      _bind(c, ready, voiceReady);
    });
    if (c.token != null) unawaited(_registerPush(c)); // whatever the key setup: push must reach a friend without Deepgram
    unawaited(const MethodChannel('jarvis/readiness').invokeMethod<void>('set', voiceReady)
        .catchError((Object _) {})); // Android-only; no credentials cross this channel
    if (ready) unawaited(widget.onReady?.call(voiceReady).catchError((Object _) {}));
  }

  /// Sends this phone's push token for the signed-in device, now and on every rotation. Silent on failure: the next
  /// launch retries.
  Future<void> _registerPush(Config c) async {
    Future<void> put(String? token) async {
      if (token == null || token.isEmpty || _config != c) return; // signed out or re-signed in meanwhile
      await widget.apiFor(c).put('/device/fcm', {'token': token});
    }

    try {
      await widget.pushInit?.call();
      await _fcmRefresh?.cancel();
      _fcmRefresh = widget.fcmRefresh?.call().listen((t) => put(t).catchError((Object _) {}));
      await put(await widget.fcmToken?.call());
    } catch (_) {}
  }

  Future<void> _onLink(Uri link) async {
    try {
      final token = await _auth.claim(link);
      if (token == null) return; // not a sign-in this install started: ignore silently
      _routeGeneration++; // a late /me for the previous token must not replace this sign-in
      final c = Config(_store.url, token);
      try {
        final tz = await widget.tz();
        if (tz != null) await widget.apiFor(c).put('/me', {'tz': tz});
      } catch (_) {} // best effort: the backend keeps its default timezone
      _signInError = null;
      _navigator.currentState?.popUntil((r) => r.isFirst);
      await _route(c);
    } catch (_) {
      if (!mounted) return;
      setState(() => _signInError = 'Sign-in failed, try again');
      if (_ready) _messenger.currentState?.showSnackBar(const SnackBar(content: Text('Sign-in failed, try again')));
    }
  }

  Future<Uri> _startSignIn() async => (await _auth.start()).url;

  void _bind(Config c, bool ready, bool voiceReady) {
    _config = c;
    _ready = ready;
    _voiceReady = voiceReady;
    _controller?.dispose(); // before the dashboard: its onEnded refreshes it
    _dashboard?.dispose();
    _dashboard = !ready
        ? null
        : (DashboardController(fetch: () => (widget.dashboardFetch ?? fetchDashboard)(c), alarm: widget.alarm ?? nextAlarm)
          ..refresh());
    _controller = !ready
        ? null
        : SessionController(
            connect: () => WsVoiceSocket.open(c),
            mic: RecordMic(),
            player: PcmPlayer(),
            phone: AndroidPhoneActions(),
            speaker: TtsSpeaker(),
            fcmToken: widget.fcmToken,
            onEnded: () {
              widget.onSessionEnded?.call();
              _dashboard?.refresh(); // a spoken "add task" should show up
            },
            onStarted: widget.onSessionStarted,
          );
    AppHost.ready = voiceReady;
    if (!voiceReady) {
      AppHost.attach(null);
      unawaited(widget.stopWake?.call().catchError((Object _) {}));
    } else {
      AppHost.attach(_controller);
      widget.startWake?.call().catchError((Object e) => debugPrint('wake word unavailable: $e'));
    }
  }

  Widget _home() {
    final c = _config!;
    if (c.token == null) {
      return OnboardingScreen(
          key: const ValueKey('signin'),
          signIn: () async => widget.open(await _startSignIn()),
          error: _signInError,
          onDone: () {});
    }
    if (_setupError != null) {
      return Scaffold(body: CocoaBackground(child: Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
        Text(_setupError!, textAlign: TextAlign.center, style: const TextStyle(color: cream)),
        TextButton(onPressed: () => _route(c), child: const Text('Retry')),
      ]))));
    }
    if (!_ready) {
      return OnboardingScreen(
          key: const ValueKey('setup'),
          step: OnboardingStep.llm,
          signIn: () async {},
          api: widget.apiFor(c),
          onDone: () => _route(c));
    }
    return SessionScreen(
        controller: _controller!,
        dashboard: _dashboard,
        api: widget.apiFor(c),
        startSignIn: _startSignIn,
        voiceReady: _voiceReady,
        onSettingsReturn: () => _route(c),
        onNeedsSetup: () { // /me without an LLM key swaps home for the key step
          _navigator.currentState?.popUntil((r) => r.isFirst);
          _messenger.currentState?.showSnackBar(const SnackBar(content: Text('Add your AI key to keep using Jarvis.')));
          _route(c);
        },
        open: widget.open,
        alarm: widget.alarm ?? nextAlarm,
        runPhoneActions: (actions) async {
          for (final a in actions) {
            await AndroidPhoneActions().run(a);
          }
        });
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Jarvis',
        theme: jarvisTheme(),
        scaffoldMessengerKey: _messenger,
        navigatorKey: _navigator,
        home: _config == null ? const Scaffold(body: Center(child: CircularProgressIndicator())) : _home(),
      );
  final _navigator = GlobalKey<NavigatorState>();
}
