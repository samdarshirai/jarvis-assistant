import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/chat_screen.dart';
import 'package:jarvis_app/config.dart';

class FakeApi extends ApiClient {
  FakeApi(this.handler) : super(Config('https://x.test', 't'));
  final Future<Map<String, dynamic>> Function(String, Map<String, dynamic>?) handler;
  final calls = <(String, Map<String, dynamic>?)>[];
  @override
  Future<Map<String, dynamic>> post(String path, [Map<String, dynamic>? body]) {
    calls.add((path, body));
    return handler(path, body);
  }
}

Widget app(FakeApi api, {VoidCallback? onVoice, List<List<Map<String, dynamic>>>? ran}) => MaterialApp(
    home: ChatScreen(api: api, onVoice: onVoice ?? () {}, runPhoneActions: (a) async => ran?.add(a)));

Future<void> send(WidgetTester t, String s) async {
  await t.enterText(find.byType(TextField), s);
  await t.tap(find.byIcon(Icons.send));
  await t.pump();
  await t.pump();
}

void main() {
  testWidgets('empty hint, trims text, ignores empty, shows reply, runs client actions, mic works', (t) async {
    final ran = <List<Map<String, dynamic>>>[];
    var voice = 0;
    final api = FakeApi((p, b) async => {'reply': 'Hi there', 'card': null, 'client_actions': [{'type': 'alarm'}]});
    await t.pumpWidget(app(api, onVoice: () => voice++, ran: ran));
    expect(find.text('Type a request, or tap the mic.'), findsOneWidget);
    await send(t, '   ');
    expect(api.calls, isEmpty);
    await send(t, '  hello  ');
    expect(api.calls.single.$1, '/chat');
    expect(api.calls.single.$2, {'text': 'hello'});
    expect(find.text('hello'), findsOneWidget);
    expect(find.text('Hi there'), findsOneWidget);
    expect(ran.single, [{'type': 'alarm'}]);
    await t.tap(find.byIcon(Icons.mic));
    expect(voice, 1);
  });

  testWidgets('send disabled while in flight with Thinking bubble; error bubble after', (t) async {
    final c = Completer<Map<String, dynamic>>();
    final api = FakeApi((p, b) => c.future);
    await t.pumpWidget(app(api));
    await send(t, 'a');
    expect(find.text('Thinking…'), findsOneWidget);
    await send(t, 'b');
    expect(api.calls.length, 1);
    c.completeError(Exception('down'));
    await t.pump();
    expect(find.text("Couldn't reach Jarvis."), findsOneWidget);
    expect(find.text('Thinking…'), findsNothing);
  });

  testWidgets('confirm card: confirm posts yes, settles Done; cancel posts no', (t) async {
    final api = FakeApi((p, b) async => p == '/chat'
        ? {'reply': null, 'card': {'interrupt_id': 'i1', 'summary': 'Send mail', 'tap_only': true, 'after_untrusted': true}, 'client_actions': []}
        : {'reply': b!['decision'] == 'yes' ? 'Sent.' : 'Okay.', 'card': null, 'client_actions': []});
    await t.pumpWidget(app(api));
    await send(t, 'mail bob');
    expect(find.text('Needs your OK'), findsOneWidget);
    expect(find.textContaining('third-party content'), findsOneWidget);
    await t.tap(find.text('Confirm'));
    await t.pump();
    await t.pump();
    expect(api.calls.last.$1, '/chat/confirm');
    expect(api.calls.last.$2, {'interrupt_id': 'i1', 'decision': 'yes'});
    expect(find.text('Sent.'), findsOneWidget);
    expect(find.text('Done'), findsOneWidget);
    expect(find.text('Confirm'), findsNothing);

    await send(t, 'again');
    await t.tap(find.text('Cancel'));
    await t.pump();
    await t.pump();
    expect(api.calls.last.$2, {'interrupt_id': 'i1', 'decision': 'no'});
    expect(find.text('Cancelled'), findsOneWidget);
  });

  testWidgets('no overflow at 320px with long reply and card', (t) async {
    t.view.physicalSize = const Size(320, 568);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.reset);
    final api = FakeApi((p, b) async => {
          'reply': 'word ' * 100,
          'card': {'interrupt_id': 'i', 'summary': 'S ' * 200, 'tap_only': false, 'after_untrusted': true},
          'client_actions': []
        });
    await t.pumpWidget(app(api));
    await send(t, 'x' * 50);
    expect(t.takeException(), isNull);
  });
}
