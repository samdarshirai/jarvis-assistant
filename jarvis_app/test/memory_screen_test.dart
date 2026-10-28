import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/memory_screen.dart';

class MemApi extends ApiClient {
  MemApi({this.graphOn = true}) : super(Config('https://h.test', 't'));
  final bool graphOn;

  @override
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) async {
    if (path == '/memory') {
      return {
        'memories': [
          {'id': 1, 'text': 'I like tea'}
        ]
      };
    }
    if (path == '/graph/entities' && graphOn) return {'entities': []};
    throw ApiError(404, {'error': 'not_found'}, path);
  }
}

void main() {
  Future<void> pump(WidgetTester t, ApiClient api) async {
    await t.pumpWidget(MaterialApp(home: MemoryScreen(api: api)));
    await t.pumpAndSettle();
  }

  testWidgets('People button shows when the server has the graph', (t) async {
    await pump(t, MemApi());
    expect(find.text('I like tea'), findsOneWidget);
    expect(find.text('People'), findsOneWidget);
  });

  testWidgets('People button hidden when the graph is off (404)', (t) async {
    await pump(t, MemApi(graphOn: false));
    expect(find.text('I like tea'), findsOneWidget);
    expect(find.text('People'), findsNothing);
  });
}
