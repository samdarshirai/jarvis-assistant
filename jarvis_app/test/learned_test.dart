import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/app.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/dashboard.dart';
import 'package:jarvis_app/dashboard_cards.dart';
import 'package:jarvis_app/learned.dart';
import 'package:jarvis_app/learned_section.dart';
import 'package:jarvis_app/memory_screen.dart';
import 'package:jarvis_app/push.dart';
import 'package:jarvis_app/theme.dart';

import 'dash_fixture.dart';

Map<String, dynamic> cand(int id, {String subject = 'Anna', String? relation = 'wife', String predicate = 'occupation',
        String value = 'a teacher', String? replaces, String kind = 'person'}) =>
    {
      'id': id, 'subject': subject, 'subject_kind': kind, 'relation': relation, 'predicate': predicate, 'value': value,
      'quote': 'Anna is a teacher', 'replaces': replaces,
    };

Map<String, dynamic> grp(int conv, List<Map<String, dynamic>> items, {String? title = 'Dinner plans'}) => {
      'conversation': {'id': conv, 'title': title, 'last_at': '2026-11-03T12:00:00+00:00'},
      'items': items,
    };

class LearnedApi extends ApiClient {
  LearnedApi(this.groups) : super(Config('https://h.test', 't'));
  List<Map<String, dynamic>> groups;
  final calls = <String>[];
  final bodies = <String, Map<String, dynamic>?>{};
  final results = <String, Object>{}; // 'POST /path' -> response map or Exception
  int count = 0;

  Map<String, dynamic> _r(String k, [Map<String, dynamic>? body]) {
    calls.add(k);
    bodies[k] = body;
    final r = results[k];
    if (r is Exception) throw r;
    return (r as Map<String, dynamic>?) ?? {'ok': true};
  }

  @override
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) async {
    calls.add('GET $path');
    if (path == '/graph/candidates') return {'groups': groups};
    if (path == '/graph/candidates/count') return {'count': count, 'enabled': true};
    if (path == '/graph/entities') return {'entities': [], 'relations': []};
    if (path == '/graph/suppressions') return {'suppressions': []};
    return {'ok': true};
  }

  @override
  Future<Map<String, dynamic>> post(String path, [Map<String, dynamic>? body]) async => _r('POST $path', body);

  @override
  Future<Map<String, dynamic>> delete(String path) async => _r('DELETE $path');
}

void main() {
  Future<void> pumpSection(WidgetTester t, LearnedApi api, {LearnedController? learned, VoidCallback? onGraph}) async {
    t.view.devicePixelRatio = 1; // tall: the section's list is capped at 45% of the height
    t.view.physicalSize = const Size(400, 2400);
    addTearDown(t.view.reset);
    await t.pumpWidget(MaterialApp(
        theme: jarvisTheme(),
        home: Scaffold(body: SingleChildScrollView(child: LearnedSection(api: api, learned: learned, onGraphChanged: onGraph)))));
    await t.pumpAndSettle();
  }

  group('sentence', () {
    test('reads as plain language', () {
      expect(learnedSentence(cand(1)), 'Anna, your wife: works as a teacher');
      expect(learnedSentence(cand(1, relation: null, predicate: 'city', value: 'Pune')), 'Anna: lives in Pune');
      expect(learnedSentence(cand(1, subject: 'me', kind: 'self', relation: null, predicate: 'employer', value: 'Acme')),
          'You: works at Acme');
      expect(learnedSentence(cand(1, predicate: 'note', value: 'likes tea')), 'Anna, your wife: likes tea');
    });
  });

  group('Learned section', () {
    testWidgets('is hidden when nothing is waiting', (t) async {
      await pumpSection(t, LearnedApi([]));
      expect(find.byKey(const Key('learned-header')), findsNothing);
      expect(find.textContaining('Learned'), findsNothing);
    });

    testWidgets('lists candidates by conversation with the quote as evidence and the current value', (t) async {
      final api = LearnedApi([grp(7, [cand(1), cand(2, predicate: 'city', value: 'Pune', replaces: 'Delhi')])]);
      final learned = LearnedController(api);
      await pumpSection(t, api, learned: learned);
      expect(find.text('Learned · 2'), findsOneWidget);
      expect(learned.count, 2);
      expect(find.textContaining('Dinner plans'), findsOneWidget);
      expect(find.text('Anna, your wife: works as a teacher'), findsOneWidget);
      expect(find.text('Currently: Delhi'), findsOneWidget);
      expect(find.text('You said: “Anna is a teacher”'), findsNWidgets(2));
      for (final id in [1, 2]) {
        for (final a in ['approve', 'edit', 'dismiss']) {
          expect(find.byKey(ValueKey('$a-$id')), findsOneWidget);
        }
      }
    });

    testWidgets('collapses and expands from the header', (t) async {
      await pumpSection(t, LearnedApi([grp(7, [cand(1)])]));
      await t.tap(find.byKey(const Key('learned-header')));
      await t.pumpAndSettle();
      expect(find.byKey(const ValueKey('candidate-1')), findsNothing);
      expect(find.text('Learned · 1'), findsOneWidget);
      await t.tap(find.byKey(const Key('learned-header')));
      await t.pumpAndSettle();
      expect(find.byKey(const ValueKey('candidate-1')), findsOneWidget);
    });

    testWidgets('Approve posts to the candidate, refreshes the graph and offers Undo for 5 seconds', (t) async {
      final api = LearnedApi([grp(7, [cand(1), cand(2, value: 'a nurse')])]);
      api.results['POST /graph/candidates/1/approve'] = {'entity': {'id': 9, 'name': 'Anna'}, 'created': true, 'fact_ids': [41, 42]};
      final learned = LearnedController(api);
      var graph = 0;
      await pumpSection(t, api, learned: learned, onGraph: () => graph++);
      await t.tap(find.byKey(const ValueKey('approve-1')));
      await t.pumpAndSettle();
      expect(api.calls, contains('POST /graph/candidates/1/approve'));
      expect(api.bodies['POST /graph/candidates/1/approve'], isNull);
      expect(find.byKey(const ValueKey('candidate-1')), findsNothing);
      expect(find.byKey(const ValueKey('candidate-2')), findsOneWidget);
      expect(learned.count, 1);
      expect(graph, 1);
      expect(find.text('Saved: Anna, your wife: works as a teacher'), findsOneWidget);
      // Undo retracts exactly what the approval wrote
      await t.tap(find.text('Undo'));
      await t.pumpAndSettle();
      expect(api.calls, containsAllInOrder(['DELETE /graph/facts/41', 'DELETE /graph/facts/42']));
      expect(graph, 2);
    });

    testWidgets('the Undo bar goes away after 5 seconds', (t) async {
      final api = LearnedApi([grp(7, [cand(1)])]);
      api.results['POST /graph/candidates/1/approve'] = {'fact_ids': [41]};
      await pumpSection(t, api);
      await t.tap(find.byKey(const ValueKey('approve-1')));
      await t.pump();
      await t.pump(const Duration(milliseconds: 500));
      expect(find.text('Undo'), findsOneWidget);
      await t.pump(const Duration(seconds: 6));
      await t.pumpAndSettle();
      expect(find.text('Undo'), findsNothing);
    });

    testWidgets('Dismiss posts to the dismiss endpoint and removes the card; the last one hides the section', (t) async {
      final api = LearnedApi([grp(7, [cand(1)])]);
      final learned = LearnedController(api);
      await pumpSection(t, api, learned: learned);
      await t.tap(find.byKey(const ValueKey('dismiss-1')));
      await t.pumpAndSettle();
      expect(api.calls, contains('POST /graph/candidates/1/dismiss'));
      expect(find.byKey(const Key('learned-header')), findsNothing);
      expect(learned.count, 0);
    });

    testWidgets('a failed approve keeps the card and says so', (t) async {
      final api = LearnedApi([grp(7, [cand(1)])]);
      api.results['POST /graph/candidates/1/approve'] = ApiError(502, null, '/graph/candidates/1/approve');
      await pumpSection(t, api);
      await t.tap(find.byKey(const ValueKey('approve-1')));
      await t.pumpAndSettle();
      expect(find.byKey(const ValueKey('candidate-1')), findsOneWidget);
      expect(find.text("Couldn't save that"), findsOneWidget);
    });

    testWidgets('an ambiguous subject asks which one, then retries with the pick', (t) async {
      final api = LearnedApi([grp(7, [cand(1)])]);
      api.results['POST /graph/candidates/1/approve'] = ApiError(409, {
        'error': 'ambiguous_subject',
        'choice': [{'id': 3, 'name': 'Anna', 'kind': 'person'}, {'id': 4, 'name': 'Anna', 'kind': 'person'}],
      }, '/graph/candidates/1/approve');
      await pumpSection(t, api);
      await t.tap(find.byKey(const ValueKey('approve-1')));
      await t.pumpAndSettle();
      expect(find.text('Which Anna?'), findsOneWidget);
      expect(find.byKey(const ValueKey('candidate-1')), findsOneWidget, reason: 'nothing is written before the pick');
      api.results['POST /graph/candidates/1/approve'] = {'fact_ids': [5]};
      await t.tap(find.byKey(const ValueKey('pick-4')));
      await t.pumpAndSettle();
      expect(api.bodies['POST /graph/candidates/1/approve'], {'entity_id': 4});
      expect(find.byKey(const ValueKey('candidate-1')), findsNothing);
    });

    testWidgets('"a new person" is sent as new, and backing out writes nothing', (t) async {
      final api = LearnedApi([grp(7, [cand(1)])]);
      api.results['POST /graph/candidates/1/approve'] = ApiError(409, {
        'error': 'ambiguous_subject',
        'choice': [{'id': 3, 'name': 'Anna', 'kind': 'person'}, {'id': 4, 'name': 'Anna', 'kind': 'person'}],
      }, '/x');
      await pumpSection(t, api);
      await t.tap(find.byKey(const ValueKey('approve-1')));
      await t.pumpAndSettle();
      await t.tapAt(const Offset(5, 5)); // dismiss the dialog by tapping the barrier
      await t.pumpAndSettle();
      expect(api.calls.where((c) => c == 'POST /graph/candidates/1/approve').length, 1);
      expect(find.byKey(const ValueKey('candidate-1')), findsOneWidget);
      await t.tap(find.byKey(const ValueKey('approve-1')));
      await t.pumpAndSettle();
      api.results['POST /graph/candidates/1/approve'] = {'fact_ids': [5]};
      await t.tap(find.byKey(const Key('pick-new')));
      await t.pumpAndSettle();
      expect(api.bodies['POST /graph/candidates/1/approve'], {'new': true});
    });

    testWidgets('Edit sends only what changed', (t) async {
      final api = LearnedApi([grp(7, [cand(1)])]);
      api.results['POST /graph/candidates/1/approve'] = {'fact_ids': [5]};
      await pumpSection(t, api);
      await t.tap(find.byKey(const ValueKey('edit-1')));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('edit-value')), 'a nurse');
      await t.tap(find.byKey(const Key('edit-save')));
      await t.pumpAndSettle();
      expect(api.bodies['POST /graph/candidates/1/approve'], {'value': 'a nurse'});
    });

    testWidgets('Approve all states the count, needs a confirmation and posts the group ids', (t) async {
      final api = LearnedApi([grp(7, [cand(1), cand(2), cand(3)]), grp(8, [cand(4)], title: null)]);
      api.results['POST /graph/candidates/bulk'] = {'approved': [], 'needs_choice': [], 'skipped': []};
      await pumpSection(t, api);
      await t.tap(find.byKey(const ValueKey('approve-all-7')));
      await t.pumpAndSettle();
      expect(find.text('Approve all 3?'), findsOneWidget);
      expect(find.textContaining('Saves 3 facts from "Dinner plans"'), findsOneWidget);
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      expect(api.calls.where((c) => c.contains('bulk')), isEmpty, reason: 'nothing enters the graph before the confirmation');
      await t.tap(find.byKey(const ValueKey('approve-all-7')));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('bulk-confirm')));
      await t.pumpAndSettle();
      expect(api.bodies['POST /graph/candidates/bulk'], {'ids': [1, 2, 3], 'decision': 'approve'});
    });

    testWidgets('Dismiss all posts the dismiss decision for the group', (t) async {
      final api = LearnedApi([grp(7, [cand(1), cand(2)])]);
      api.results['POST /graph/candidates/bulk'] = {'dismissed': [1, 2], 'skipped': []};
      await pumpSection(t, api);
      await t.tap(find.byKey(const ValueKey('dismiss-all-7')));
      await t.pumpAndSettle();
      expect(find.text('Dismiss all 2?'), findsOneWidget);
      await t.tap(find.byKey(const Key('bulk-confirm')));
      await t.pumpAndSettle();
      expect(api.bodies['POST /graph/candidates/bulk'], {'ids': [1, 2], 'decision': 'dismiss'});
    });

    testWidgets('the header has an accessible label with the count', (t) async {
      final h = t.ensureSemantics();
      await pumpSection(t, LearnedApi([grp(7, [cand(1), cand(2)])]));
      expect(find.bySemanticsLabel(RegExp('Learned, 2 to review')), findsOneWidget);
      h.dispose();
    });
  });

  group('Memory screen', () {
    testWidgets('shows the Learned section above the graph or list, and nothing when empty', (t) async {
      await t.binding.setSurfaceSize(const Size(400, 900));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final api = LearnedApi([grp(7, [cand(1)])]);
      await t.pumpWidget(MaterialApp(theme: jarvisTheme(), home: MemoryScreen(api: api, autoRotate: false)));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('learned-header')), findsOneWidget);
      expect(find.text('Nothing remembered yet. Tell Jarvis "remember that…".'), findsOneWidget);
      api.groups = [];
      await t.pumpWidget(MaterialApp(theme: jarvisTheme(), home: MemoryScreen(key: UniqueKey(), api: api, autoRotate: false)));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('learned-header')), findsNothing);
    });
  });

  group('badge', () {
    Future<void> pumpHome(WidgetTester t, LearnedController? learned) async {
      final c = DashboardController(fetch: () async => sampleDashboard(), alarm: () async => null);
      await c.refresh();
      await t.pumpWidget(MaterialApp(
          theme: jarvisTheme(),
          home: Scaffold(
              body: SingleChildScrollView(
                  child: DashboardSections(controller: c, learned: learned, onPlayBrief: (_) {}, now: () => DateTime(2026, 10, 5, 8))))));
      await t.pump();
    }

    testWidgets('the Memory pill shows the count and follows it', (t) async {
      final api = LearnedApi([])..count = 3;
      final learned = LearnedController(api);
      await pumpHome(t, learned);
      expect(find.byKey(const Key('learned-badge')), findsNothing);
      await learned.refresh();
      await t.pump();
      expect(find.descendant(of: find.byKey(const Key('learned-badge')), matching: find.text('3')), findsOneWidget);
      learned.set(120);
      await t.pump();
      expect(find.text('99+'), findsOneWidget);
      learned.set(0);
      await t.pump();
      expect(find.byKey(const Key('learned-badge')), findsNothing);
    });

    testWidgets('the badge has a text label for screen readers, not just a colour', (t) async {
      final h = t.ensureSemantics();
      final learned = LearnedController(LearnedApi([]))..set(2);
      await pumpHome(t, learned);
      expect(find.bySemanticsLabel(RegExp('2 to review')), findsOneWidget);
      h.dispose();
    });

    test('a failed refresh keeps the last count', () async {
      final api = LearnedApi([])..count = 4;
      final learned = LearnedController(api);
      await learned.refresh();
      expect(learned.count, 4);
      final offline = LearnedController(_Down())..set(4);
      await offline.refresh();
      expect(offline.count, 4);
    });

    test('a disabled extractor reads as zero', () async {
      final learned = LearnedController(_Disabled())..set(5);
      await learned.refresh();
      expect(learned.count, 0);
    });
  });

  group('push', () {
    test('only the typed candidates payload is a Learned push, and it is not a speak push', () {
      expect(isLearnedPush({'type': 'candidates', 'count': '3'}), isTrue);
      expect(isLearnedPush({'speak': 'hello'}), isFalse);
      expect(isLearnedPush({}), isFalse);
      expect(speakTextOf({'type': 'candidates', 'count': '3'}), isNull);
    });

    test('a tap before the app is ready opens Learned once on attach; a stale one is dropped', () async {
      var now = DateTime(2026, 11, 3, 12);
      AppHost.now = () => now;
      addTearDown(() {
        AppHost.attachLearned(null);
        AppHost.now = DateTime.now;
      });
      var opened = 0;
      AppHost.openLearned();
      AppHost.attachLearned(() => opened++);
      await Future<void>.delayed(Duration.zero);
      expect(opened, 1);
      AppHost.attachLearned(() => opened++);
      await Future<void>.delayed(Duration.zero);
      expect(opened, 1, reason: 'drained exactly once');
      AppHost.attachLearned(null);
      AppHost.openLearned();
      now = now.add(const Duration(minutes: 1));
      AppHost.attachLearned(() => opened++);
      await Future<void>.delayed(Duration.zero);
      expect(opened, 1, reason: 'a tap older than the pending window is discarded');
    });

    test('with the app open the tap opens Learned immediately and never starts a session', () {
      var opened = 0;
      AppHost.attachLearned(() => opened++);
      addTearDown(() => AppHost.attachLearned(null));
      AppHost.openLearned();
      expect(opened, 1);
    });
  });
}

class _Disabled extends ApiClient {
  _Disabled() : super(Config('https://h.test', 't'));
  @override
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) async => {'count': 0, 'enabled': false};
}

class _Down extends ApiClient {
  _Down() : super(Config('https://h.test', 't'));
  @override
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) async => throw Exception('offline');
}
