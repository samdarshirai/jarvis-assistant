import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_gate/jarvis_gate.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;

  tearDown(() {
    messenger.setMockMethodCallHandler(const MethodChannel('jarvis_gate'), null);
    messenger.setMockStreamHandler(const EventChannel('jarvis_gate/changes'), null);
  });

  test('state() maps the channel reply', () async {
    messenger.setMockMethodCallHandler(const MethodChannel('jarvis_gate'),
        (call) async {
      expect(call.method, 'state');
      return {'charging': true, 'androidAuto': false};
    });
    final s = await JarvisGate.state();
    expect(s.charging, isTrue);
    expect(s.androidAuto, isFalse);
  });

  test('changes emits a GateState per event', () async {
    messenger.setMockStreamHandler(
        const EventChannel('jarvis_gate/changes'),
        MockStreamHandler.inline(onListen: (args, sink) {
          sink.success({'charging': false, 'androidAuto': true});
          sink.success({'charging': true, 'androidAuto': true});
          sink.endOfStream();
        }));
    final got = await JarvisGate.changes
        .map((s) => (s.charging, s.androidAuto))
        .toList();
    expect(got, [(false, true), (true, true)]);
  });
}
