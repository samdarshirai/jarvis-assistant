import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/alarm.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const ch = MethodChannel('jarvis/alarm');
  void mock(Future<Object?> Function(MethodCall) h) =>
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(ch, h);
  tearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(ch, null));

  test('returns the alarm time from epoch milliseconds', () async {
    final at = DateTime(2026, 10, 6, 6, 30);
    mock((c) async => c.method == 'next' ? at.millisecondsSinceEpoch : null);
    expect(await nextAlarm(), at);
  });

  test('null means no alarm', () async {
    mock((_) async => null);
    expect(await nextAlarm(), isNull);
  });

  test('a channel error means no alarm', () async {
    mock((_) async => throw PlatformException(code: 'x'));
    expect(await nextAlarm(), isNull);
  });
}
