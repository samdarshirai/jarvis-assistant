import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/tasks_screen.dart';
import 'package:jarvis_app/theme.dart';

class FakeApi implements ApiClient {
  FakeApi(this.tasks, {this.failPost = false});
  final Object? tasks;
  final bool failPost;
  final posts = <(String, Map<String, dynamic>?)>[];
  @override
  Config get config => throw UnimplementedError();
  @override
  Duration get timeout => Duration.zero;
  @override
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) async => {'tasks': tasks};
  @override
  Future<Map<String, dynamic>> post(String path, [Map<String, dynamic>? body]) async {
    posts.add((path, body));
    if (failPost) throw Exception('down');
    return path == '/tasks' ? {'task': {'id': 9, 'title': body!['title'], 'due': null, 'overdue': false}} : {};
  }
}

final _now = DateTime(2026, 10, 7, 10);
const _tasks = [
  {'id': 1, 'title': 'Pay rent', 'due': '2026-10-03', 'overdue': true},
  {'id': 2, 'title': 'Call mum', 'due': '2026-10-07', 'overdue': false},
  {'id': 3, 'title': 'Plan trip', 'due': '2026-10-20', 'overdue': false},
  {'id': 4, 'title': 'Buy milk', 'due': null, 'overdue': false},
];

Future<FakeApi> _pump(WidgetTester t, Object? tasks, {bool failPost = false}) async {
  t.view.physicalSize = const Size(320, 800);
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.reset);
  final api = FakeApi(tasks, failPost: failPost);
  await t.pumpWidget(MaterialApp(theme: jarvisTheme(), home: TasksScreen(api: api, now: () => _now)));
  await t.pumpAndSettle();
  return api;
}

void main() {
  testWidgets('groups tasks and counts what is left today', (t) async {
    await _pump(t, _tasks);
    for (final g in ['OVERDUE', 'TODAY', 'UPCOMING', 'NO DATE']) {
      expect(find.text(g), findsOneWidget);
    }
    expect(find.text('DONE'), findsNothing);
    expect(find.text('2 left today'), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  testWidgets('completing moves to Done and posts', (t) async {
    final api = await _pump(t, _tasks);
    await t.tap(find.byKey(const ValueKey('check-2')));
    await t.pumpAndSettle();
    expect(api.posts.single.$1, '/tasks/2/complete');
    expect(find.text('DONE'), findsOneWidget);
    expect(find.text('1 left today'), findsOneWidget);
  });

  testWidgets('failed complete reverts with snackbar', (t) async {
    await _pump(t, _tasks, failPost: true);
    await t.tap(find.byKey(const ValueKey('check-2')));
    await t.pumpAndSettle();
    expect(find.text("Couldn't update"), findsOneWidget);
    expect(find.text('DONE'), findsNothing);
    expect(find.text('2 left today'), findsOneWidget);
  });

  testWidgets('add trims, ignores empty, appends', (t) async {
    final api = await _pump(t, _tasks);
    await t.enterText(find.byType(TextField), '   ');
    await t.testTextInput.receiveAction(TextInputAction.done);
    await t.pumpAndSettle();
    expect(api.posts, isEmpty);
    await t.enterText(find.byType(TextField), '  Water plants ');
    await t.testTextInput.receiveAction(TextInputAction.done);
    await t.pumpAndSettle();
    expect(api.posts.single.$1, '/tasks');
    expect(api.posts.single.$2!['title'], 'Water plants');
    expect(find.text('Water plants'), findsOneWidget);
  });

  testWidgets('null tasks shows Unavailable', (t) async {
    await _pump(t, null);
    expect(find.text('Unavailable'), findsOneWidget);
  });
}
