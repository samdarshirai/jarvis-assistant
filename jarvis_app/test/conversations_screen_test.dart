import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/conversations_screen.dart';

class Fake extends ApiClient {
  Fake(this.list, [this.resume]) : super(Config('https://x.test', 't'));
  final List<Map<String, dynamic>> list;
  final Future<Map<String, dynamic>> Function()? resume;
  final posts = <String>[];
  @override
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) async => {'conversations': list};
  @override
  Future<Map<String, dynamic>> post(String path, [Map<String, dynamic>? body]) {
    posts.add(path);
    return resume!();
  }
}

Map<String, dynamic> c(int id, String? title, String ch) =>
    {'id': id, 'title': title, 'channel': ch, 'last_at': DateTime.now().toUtc().toIso8601String(), 'open': false};

Future<Object?> open(WidgetTester t, Fake api) async {
  Object? popped;
  await t.pumpWidget(MaterialApp(
      home: Builder(
          builder: (ctx) => TextButton(
              onPressed: () async => popped = await Navigator.push(ctx, MaterialPageRoute(builder: (_) => ConversationsScreen(api: api))),
              child: const Text('go')))));
  await t.tap(find.text('go'));
  await t.pumpAndSettle();
  return () => popped;
}

void main() {
  testWidgets('lists rows in order with icons; Untitled fallback', (t) async {
    await open(t, Fake([c(2, 'Alpha', 'voice'), c(1, null, 'telegram')]));
    expect(find.text('Alpha'), findsOneWidget);
    expect(find.text('Untitled'), findsOneWidget);
    expect(find.byIcon(Icons.mic), findsOneWidget);
    expect(find.byIcon(Icons.send), findsOneWidget);
    expect(t.getTopLeft(find.text('Alpha')).dy, lessThan(t.getTopLeft(find.text('Untitled')).dy));
  });

  testWidgets('tap resumes and pops payload', (t) async {
    final api = Fake([c(7, 'Seven', 'chat')], () async => {'conversation': c(7, 'Seven', 'chat'), 'messages': []});
    final get = await open(t, api) as Object? Function();
    await t.tap(find.text('Seven'));
    await t.pumpAndSettle();
    expect(api.posts, ['/conversations/7/resume']);
    expect((get() as Map)['conversation']['id'], 7);
  });

  testWidgets('409 shows snackbar and stays', (t) async {
    final api = Fake([c(7, 'Seven', 'voice')], () async => throw ApiError(409, {'error': 'in_progress'}, '/x'));
    await open(t, api);
    await t.tap(find.text('Seven'));
    await t.pumpAndSettle();
    expect(find.text('Still in a voice session'), findsOneWidget);
    expect(find.text('Seven'), findsOneWidget);
  });

  testWidgets('empty list', (t) async {
    await open(t, Fake([]));
    expect(find.text('No past chats yet.'), findsOneWidget);
  });
}
