import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/graph3d.dart';
import 'package:jarvis_app/memory_graph.dart';
import 'package:jarvis_app/memory_screen.dart';

class GraphApi extends ApiClient {
  GraphApi(this.routes) : super(Config('https://h.test', 't'));
  final Map<String, Object> routes;
  final calls = <String>[];
  Map<String, dynamic>? lastBody;
  Map<String, String>? lastQuery;
  Object _r(String k) {
    final r = routes[k];
    if (r is Exception) throw r;
    return r ?? <String, dynamic>{'ok': true};
  }

  @override
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) async {
    final k = query == null ? path : '$path?${[for (final e in query.entries) '${e.key}=${e.value}'].join('&')}';
    calls.add('GET $k');
    return _r(routes.containsKey(k) ? k : path) as Map<String, dynamic>;
  }

  @override
  Future<Map<String, dynamic>> patch(String path, Map<String, dynamic> body) async {
    calls.add('PATCH $path');
    lastBody = body;
    return _r('PATCH $path') as Map<String, dynamic>;
  }

  @override
  Future<Map<String, dynamic>> delete(String path) async {
    calls.add('DELETE $path');
    return _r('DELETE $path') as Map<String, dynamic>;
  }

  @override
  Future<Map<String, dynamic>> deleteWith(String path,
      {Map<String, String>? query, Map<String, dynamic>? body}) async {
    calls.add('DELETE $path');
    lastQuery = query;
    lastBody = body;
    return _r('DELETE $path') as Map<String, dynamic>;
  }
}

Map<String, dynamic> fact(int id, String pred, String value, {String status = 'active'}) => {
      'id': id,
      'predicate': pred,
      'value': value,
      'source': 'email',
      'excerpt': 'ex',
      'observed_at': '2026-10-05T09:30:00',
      'recorded_at': '2026-10-06T09:30:00',
      'status': status,
    };

Map<String, Object> routes() => {
      '/graph/entities': {
        'entities': [
          {'id': 1, 'name': 'Priya', 'kind': 'person', 'fact_count': 2}
        ]
      },
      '/graph/entities/1': {
        'id': 1,
        'name': 'Priya',
        'kind': 'person',
        'facts': [fact(10, 'works_at', 'Acme')],
        'history': [fact(11, 'works_at', 'OldCo', status: 'superseded')],
        'pending': [fact(12, 'lives_in', 'Pune', status: 'pending')],
      },
      '/graph/entities/1/forget-preview': {
        'entity': 'Priya',
        'facts': 3,
        'redacted_excerpts': 4,
        'mentions_deleted': 5,
        'suppressed': 1
      },
      '/graph/suppressions': {
        'suppressions': [
          {'kind': 'person', 'key_type': 'name', 'key_hmac': 'abc', 'created_at': '2026-10-07T08:00:00'}
        ]
      },
    };

void main() {
  Future<void> pump(WidgetTester t, GraphApi api, {bool list = false}) async {
    await t.binding.setSurfaceSize(const Size(400, 900));
    await t.pumpWidget(MaterialApp(home: MemoryScreen(api: api, autoRotate: false)));
    await t.pumpAndSettle();
    if (list) {
      await t.tap(find.byTooltip('List view'));
      await t.pumpAndSettle();
    }
  }

  // global position of entity [id] in the rendered graph
  Offset nodePos(WidgetTester t, GraphApi api, int id) {
    final j = api.routes['/graph/entities'] as Map<String, dynamic>;
    final all = (j['entities'] as List).cast<Map<String, dynamic>>();
    final p = [for (final e in all) if (e['kind'] == 'self') e, for (final e in all) if (e['kind'] != 'self') e];
    final scene = buildScene(p, ((j['relations'] ?? []) as List).cast<Map<String, dynamic>>());
    final box = find.byKey(const Key('memory-graph'));
    final pr = project(scene, yaw: initialYaw, pitch: initialPitch, zoom: 1, size: t.getSize(box));
    return t.getTopLeft(box) + pr.firstWhere((e) => e.node.entityId == id).pos;
  }

  testWidgets('list, provenance, history, pending', (t) async {
    final api = GraphApi(routes());
    await pump(t, api, list: true);
    expect(find.text('Priya'), findsOneWidget);
    expect(find.text('Forgotten'), findsOneWidget);
    expect(find.textContaining('abc'), findsNothing);
    await t.tap(find.text('Priya'));
    await t.pumpAndSettle();
    expect(find.text('works_at: Acme'), findsOneWidget);
    expect(find.textContaining('email'), findsWidgets);
    expect(find.textContaining('5 Oct 2026'), findsWidgets);
    expect(find.text('History'), findsOneWidget);
    expect(find.text('works_at: OldCo'), findsOneWidget);
    expect(find.text('Pending'), findsOneWidget);
    expect(find.text('lives_in: Pune'), findsOneWidget);
  });

  testWidgets('edit and delete a fact', (t) async {
    final api = GraphApi(routes()..['PATCH /graph/facts/10'] = fact(13, 'works_at', 'NewCo'));
    await pump(t, api, list: true);
    await t.tap(find.text('Priya'));
    await t.pumpAndSettle();
    await t.tap(find.byKey(const ValueKey('edit-10')));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField), 'NewCo');
    await t.tap(find.text('Save'));
    await t.pumpAndSettle();
    expect(api.calls, contains('PATCH /graph/facts/10'));
    expect(api.lastBody, {'value': 'NewCo'});
    await t.tap(find.byKey(const ValueKey('delete-13')));
    await t.pumpAndSettle();
    expect(api.calls, contains('DELETE /graph/facts/13'));
  });

  testWidgets('forget dialog shows preview and confirm deletes', (t) async {
    final api = GraphApi(routes()..['DELETE /graph/entities/1'] = {});
    await pump(t, api, list: true);
    await t.tap(find.text('Priya'));
    await t.pumpAndSettle();
    await t.tap(find.text('Forget person'));
    await t.pumpAndSettle();
    expect(find.textContaining('3 facts'), findsOneWidget);
    expect(find.textContaining('4 excerpts'), findsOneWidget);
    expect(find.textContaining('5 mentions'), findsOneWidget);
    expect(find.text('Chat history, notes and the activity log are not erased'), findsOneWidget);
    await t.tap(find.text('Forget'));
    await t.pumpAndSettle();
    expect(api.calls, contains('DELETE /graph/entities/1'));
    expect(api.lastQuery, {'mentions': 'true'});
  });

  testWidgets('forget dialog lists mentions and refetches the preview when mentions are toggled', (t) async {
    final r = routes()
      ..['/graph/entities/1/forget-preview?mentions=true'] = {
        'entity': 'Priya', 'facts': 3, 'redacted_excerpts': 4, 'mentions_deleted': 22, 'suppressed': 1,
        'mention_facts': [
          {'id': 20, 'entity': 'Me', 'predicate': 'note', 'value': 'lunch with Priya Rao'},
          {'id': 21, 'entity': 'Sam', 'predicate': 'note', 'value': 'met Priya Rao at work'},
        ],
        'mention_facts_more': 20,
      }
      ..['/graph/entities/1/forget-preview?mentions=false'] = {
        'entity': 'Priya', 'facts': 3, 'redacted_excerpts': 4, 'mentions_deleted': 0, 'suppressed': 1,
        'mention_facts': [],
      }
      ..['DELETE /graph/entities/1'] = {};
    final api = GraphApi(r);
    await pump(t, api, list: true);
    await t.tap(find.text('Priya'));
    await t.pumpAndSettle();
    await t.tap(find.text('Forget person'));
    await t.pumpAndSettle();
    expect(find.textContaining('Me — note: lunch with Priya Rao'), findsOneWidget);
    expect(find.textContaining('Sam — note: met Priya Rao at work'), findsOneWidget);
    expect(find.textContaining('and 20 more'), findsOneWidget);
    expect(find.text('Chat history, notes and the activity log are not erased'), findsOneWidget);
    await t.tap(find.text('Also delete mentions'));
    await t.pumpAndSettle();
    expect(api.calls, contains('GET /graph/entities/1/forget-preview?mentions=false'));
    expect(find.textContaining('lunch with Priya Rao'), findsNothing);
    expect(find.textContaining('0 mentions'), findsOneWidget);
    await t.tap(find.text('Forget'));
    await t.pumpAndSettle();
    expect(api.lastQuery, {'mentions': 'false'});
  });

  testWidgets('history rows can be deleted', (t) async {
    final api = GraphApi(routes());
    await pump(t, api, list: true);
    await t.tap(find.text('Priya'));
    await t.pumpAndSettle();
    expect(find.byKey(const ValueKey('edit-11')), findsNothing);
    await t.tap(find.byKey(const ValueKey('delete-11')));
    await t.pumpAndSettle();
    expect(api.calls, contains('DELETE /graph/facts/11'));
    expect(find.text('works_at: OldCo'), findsNothing);
    expect(find.text('History'), findsNothing);
  });

  testWidgets('a failed history delete shows a snackbar and keeps the row', (t) async {
    final api = GraphApi(routes()..['DELETE /graph/facts/11'] = Exception('x'));
    await pump(t, api, list: true);
    await t.tap(find.text('Priya'));
    await t.pumpAndSettle();
    await t.tap(find.byKey(const ValueKey('delete-11')));
    await t.pumpAndSettle();
    expect(find.text("Couldn't delete that"), findsOneWidget);
    expect(find.text('works_at: OldCo'), findsOneWidget);
  });

  testWidgets('self entity is listed first as Me and cannot be forgotten', (t) async {
    final r = routes()
      ..['/graph/entities'] = {
        'entities': [
          {'id': 1, 'name': 'Aaron', 'kind': 'person', 'fact_count': 2},
          {'id': 5, 'name': 'Me', 'kind': 'self', 'fact_count': 1},
        ]
      }
      ..['/graph/entities/5'] = {'id': 5, 'name': 'Me', 'kind': 'self', 'facts': [fact(30, 'home', 'Pune')], 'history': [], 'pending': []};
    await pump(t, GraphApi(r), list: true);
    final tiles = t.widgetList<ListTile>(find.byType(ListTile)).map((l) => (l.title as Text).data).toList();
    expect(tiles.take(2), ['Me', 'Aaron']);
    await t.tap(find.text('Me'));
    await t.pumpAndSettle();
    expect(find.text('home: Pune'), findsOneWidget);
    expect(find.text('Forget person'), findsNothing);
  });

  testWidgets('lift suppression', (t) async {
    final api = GraphApi(routes());
    await pump(t, api);
    await t.tap(find.byKey(const Key('forgotten-button')));
    await t.pumpAndSettle();
    await t.tap(find.byKey(const ValueKey('lift-abc')));
    await t.pumpAndSettle();
    expect(api.calls, contains('DELETE /graph/suppressions'));
    expect(api.lastBody, {'key_type': 'name', 'key_hmac': 'abc'});
    expect(find.byKey(const ValueKey('lift-abc')), findsNothing);
  });

  testWidgets('empty and error', (t) async {
    final r = routes()..['/graph/entities'] = {'entities': []};
    await pump(t, GraphApi(r));
    expect(find.text('Nothing remembered yet. Tell Jarvis "remember that…".'), findsOneWidget);
    await t.pumpWidget(const SizedBox());
    await pump(t, GraphApi({'/graph/entities': Exception('x')}));
    expect(find.text('Retry'), findsOneWidget);
  });

  testWidgets('graph is the default and tapping a node twice opens its detail', (t) async {
    final api = GraphApi(routes());
    await pump(t, api);
    expect(find.byKey(const Key('memory-graph')), findsOneWidget);
    await t.tapAt(nodePos(t, api, 1));
    await t.pumpAndSettle();
    expect(find.descendant(of: find.byKey(const Key('open-selected')), matching: find.text('Priya · 2 facts')), findsOneWidget);
    await t.tapAt(nodePos(t, api, 1));
    await t.pumpAndSettle();
    expect(find.text('works_at: Acme'), findsOneWidget);
  });

  testWidgets('tapping the pill opens the detail', (t) async {
    final api = GraphApi(routes());
    await pump(t, api);
    await t.tapAt(nodePos(t, api, 1));
    await t.pumpAndSettle();
    await t.tap(find.byKey(const Key('open-selected')));
    await t.pumpAndSettle();
    expect(find.text('works_at: Acme'), findsOneWidget);
  });

  Map<String, Object> withSelf() => routes()
    ..['/graph/entities'] = {
      'entities': [
        {'id': 1, 'name': 'Aaron', 'kind': 'person', 'fact_count': 2},
        {'id': 5, 'name': 'Me', 'kind': 'self', 'fact_count': 1},
      ]
    }
    ..['/graph/entities/5'] = {'id': 5, 'name': 'Me', 'kind': 'self', 'facts': [fact(30, 'home', 'Pune')], 'history': [], 'pending': []};

  testWidgets('Me opens detail with no forget', (t) async {
    final api = GraphApi(withSelf());
    await pump(t, api);
    await t.tapAt(nodePos(t, api, 5));
    await t.pumpAndSettle();
    expect(find.descendant(of: find.byKey(const Key('open-selected')), matching: find.text('Me · 1 facts')), findsOneWidget);
    await t.tap(find.byKey(const Key('open-selected')));
    await t.pumpAndSettle();
    expect(find.text('home: Pune'), findsOneWidget);
    expect(find.text('Forget person'), findsNothing);
  });

  testWidgets('works without a self row', (t) async {
    final api = GraphApi(routes());
    await pump(t, api);
    final box = find.byKey(const Key('memory-graph'));
    await t.tapAt(t.getCenter(box));
    await t.pumpAndSettle();
    expect(find.byKey(const Key('open-selected')), findsNothing);
  });

  testWidgets('relations from the server are tolerated when missing', (t) async {
    await pump(t, GraphApi(routes()));
    expect(t.takeException(), isNull);
    final r = withSelf();
    (r['/graph/entities'] as Map)['relations'] = [
      {'from': 1, 'to': 5, 'predicate': 'works_with'}
    ];
    final api = GraphApi(r);
    await t.pumpWidget(const SizedBox());
    await pump(t, api);
    await t.tapAt(nodePos(t, api, 1));
    await t.pumpAndSettle();
    expect(t.takeException(), isNull);
    expect(find.byKey(const Key('open-selected')), findsOneWidget);
  });

  testWidgets('toggle switches to list and back', (t) async {
    await pump(t, GraphApi(routes()));
    await t.tap(find.byTooltip('List view'));
    await t.pumpAndSettle();
    expect(find.byKey(const Key('memory-graph')), findsNothing);
    expect(find.byType(ListTile), findsWidgets);
    await t.tap(find.byTooltip('Graph view'));
    await t.pumpAndSettle();
    expect(find.byKey(const Key('memory-graph')), findsOneWidget);
  });
}
