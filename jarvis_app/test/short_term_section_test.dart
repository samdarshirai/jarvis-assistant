import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/short_term_section.dart';

class ItemsApi extends ApiClient {
  ItemsApi(this.items) : super(Config('https://h.test', 't'));
  List<Map<String, dynamic>> items;
  final calls = <String>[];
  final bodies = <String, Map<String, dynamic>?>{};

  @override
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) async {
    calls.add('GET $path');
    return path == '/memory/items' ? {'items': items} : {'ok': true};
  }

  @override
  Future<Map<String, dynamic>> post(String path, [Map<String, dynamic>? body]) async {
    calls.add('POST $path');
    return {'fact_ids': [1]};
  }

  @override
  Future<Map<String, dynamic>> delete(String path) async {
    calls.add('DELETE $path');
    return {'ok': true};
  }

  @override
  Future<Map<String, dynamic>> patch(String path, Map<String, dynamic> body) async {
    calls.add('PATCH $path');
    bodies[path] = body;
    return {'item': {...items.first, ...body}};
  }
}

Map<String, dynamic> item(int id, String text) =>
    {'id': id, 'kind': 'context', 'text': text, 'expires_at': '2026-11-06T22:59:00+00:00', 'status': 'active'};

void main() {
  Future<void> pump(WidgetTester t, ItemsApi api, {VoidCallback? onGraph}) async {
    await t.pumpWidget(MaterialApp(home: Scaffold(body: ShortTermSection(api: api, onGraphChanged: onGraph))));
    await t.pumpAndSettle();
  }

  testWidgets('hidden when Jarvis keeps no short-term notes', (t) async {
    await pump(t, ItemsApi([]));
    expect(find.textContaining('Right now'), findsNothing);
  });

  testWidgets('lists notes with their end date, and folds', (t) async {
    await pump(t, ItemsApi([item(1, 'In Pune until Friday'), item(2, 'Waiting for the plumber')]));
    expect(find.text('Right now · 2'), findsOneWidget);
    expect(find.text('In Pune until Friday'), findsOneWidget);
    expect(find.textContaining('until '), findsNWidgets(2));
    await t.tap(find.byKey(const Key('right-now-header')));
    await t.pumpAndSettle();
    expect(find.text('In Pune until Friday'), findsNothing);
  });

  testWidgets('delete and keep forever remove the note; keeping reloads the graph', (t) async {
    var graph = 0;
    final api = ItemsApi([item(1, 'In Pune until Friday'), item(2, 'Works from Pune on Fridays')]);
    await pump(t, api, onGraph: () => graph++);
    await t.tap(find.byKey(const ValueKey('right-now-menu-1')));
    await t.pumpAndSettle();
    await t.tap(find.text('Delete'));
    await t.pumpAndSettle();
    expect(api.calls, contains('DELETE /memory/items/1'));
    expect(find.text('In Pune until Friday'), findsNothing);
    await t.tap(find.byKey(const ValueKey('right-now-menu-2')));
    await t.pumpAndSettle();
    await t.tap(find.text('Keep forever'));
    await t.pumpAndSettle();
    expect(api.calls, contains('POST /memory/items/2/promote'));
    expect(graph, 1);
    expect(find.textContaining('Right now'), findsNothing);
  });

  testWidgets('edit sends only what changed', (t) async {
    final api = ItemsApi([item(1, 'In Pune until Friday')]);
    await pump(t, api);
    await t.tap(find.byKey(const ValueKey('right-now-menu-1')));
    await t.pumpAndSettle();
    await t.tap(find.text('Edit'));
    await t.pumpAndSettle();
    await t.enterText(find.byKey(const Key('right-now-text')), 'In Pune until Saturday');
    await t.tap(find.byKey(const Key('right-now-save')));
    await t.pumpAndSettle();
    expect(api.bodies['/memory/items/1'], {'text': 'In Pune until Saturday'});
    expect(find.text('In Pune until Saturday'), findsOneWidget);
  });

  test('untilText names the local day', () {
    final d = DateTime(2026, 11, 6, 12);
    expect(untilText(d.toIso8601String()), 'until Fri 6 Nov');
    expect(untilText('nope'), '');
  });
}
