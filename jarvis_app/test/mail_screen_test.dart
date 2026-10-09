import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/mail_screen.dart';

class FakeApi extends ApiClient {
  FakeApi(this.routes) : super(Config('https://h.test', 't'));
  final Map<String, Object> routes; // value: Map or Exception
  final calls = <String>[];
  @override
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) async {
    calls.add(path);
    final r = routes[path];
    if (r is Exception) throw r;
    return r as Map<String, dynamic>;
  }
}

void main() {
  Future<void> pump(WidgetTester t, FakeApi api, {VoidCallback? reply, List<String?>? opened}) async {
    await t.binding.setSurfaceSize(const Size(320, 640));
    await t.pumpWidget(const SizedBox());
    await t.pumpWidget(MaterialApp(
        home: MailScreen(api: api, onReplyByVoice: reply ?? () {}, openEmail: (id) async => opened?.add(id))));
    await t.pumpAndSettle();
  }

  testWidgets('list, expand, actions, more', (t) async {
    final opened = <String?>[];
    var replied = 0;
    final api = FakeApi({
      '/mail': {
        'items': [
          {'id': 'a1', 'from': 'Boss <b@c.com>', 'subject': 'Contract', 'date': '2026-10-05T09:30:00', 'snippet': 's'}
        ],
        'count': 7,
        'more': true
      },
      '/mail/a1': {'body': 'Hello body', 'truncated': true},
    });
    await pump(t, api, reply: () => replied++, opened: opened);
    expect(find.text('7'), findsOneWidget);
    expect(find.text('Boss'), findsOneWidget);
    expect(find.text('Contract'), findsOneWidget);
    await t.tap(find.text('Contract'));
    await t.pumpAndSettle();
    expect(find.text('Hello body'), findsOneWidget);
    expect(find.text('Message truncated'), findsOneWidget);
    await t.tap(find.text('Reply by voice'));
    await t.tap(find.text('Open in Gmail'));
    await t.tap(find.text('+ more in Gmail'));
    expect(replied, 1);
    expect(opened, ['a1', null]);
    expect(t.takeException(), isNull);
  });

  testWidgets('empty, unavailable, error+retry', (t) async {
    await pump(t, FakeApi({'/mail': {'items': [], 'count': 0, 'more': false}}));
    expect(find.text('Inbox zero'), findsOneWidget);
    await pump(t, FakeApi({'/mail': {'items': null}}));
    expect(find.text('Mail unavailable'), findsOneWidget);
    final api = FakeApi({'/mail': Exception('x')});
    await pump(t, api);
    expect(find.text('Retry'), findsOneWidget);
    await t.tap(find.text('Retry'));
    await t.pumpAndSettle();
    expect(api.calls.length, 2);
  });
}
