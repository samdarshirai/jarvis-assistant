import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/people_screen.dart';

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
    calls.add('GET $path');
    return _r(path) as Map<String, dynamic>;
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
  Future<void> pump(WidgetTester t, GraphApi api) async {
    await t.binding.setSurfaceSize(const Size(400, 900));
    await t.pumpWidget(MaterialApp(home: PeopleScreen(api: api)));
    await t.pumpAndSettle();
  }

  testWidgets('list, provenance, history, pending', (t) async {
    final api = GraphApi(routes());
    await pump(t, api);
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
    await pump(t, api);
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
    await pump(t, api);
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

  testWidgets('lift suppression', (t) async {
    final api = GraphApi(routes());
    await pump(t, api);
    await t.tap(find.byKey(const ValueKey('lift-abc')));
    await t.pumpAndSettle();
    expect(api.calls, contains('DELETE /graph/suppressions'));
    expect(api.lastBody, {'key_type': 'name', 'key_hmac': 'abc'});
    expect(find.byKey(const ValueKey('lift-abc')), findsNothing);
  });

  testWidgets('empty and error', (t) async {
    final r = routes()..['/graph/entities'] = {'entities': []};
    await pump(t, GraphApi(r));
    expect(find.text('No people yet'), findsOneWidget);
    await t.pumpWidget(const SizedBox());
    await pump(t, GraphApi({'/graph/entities': Exception('x')}));
    expect(find.text('Retry'), findsOneWidget);
  });
}
