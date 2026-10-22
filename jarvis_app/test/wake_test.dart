import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/wake.dart';

void main() {
  final now = DateTime(2026, 10, 2, 7, 30, 0);
  test('a wake saved a few seconds ago is fresh', () {
    expect(wakeIsFresh(now.subtract(const Duration(seconds: 5)).millisecondsSinceEpoch, now), isTrue);
  });
  test('an old or missing wake is not', () {
    expect(wakeIsFresh(now.subtract(const Duration(seconds: 16)).millisecondsSinceEpoch, now), isFalse);
    expect(wakeIsFresh(null, now), isFalse);
  });
  test('a timestamp from the future is not fresh', () {
    expect(wakeIsFresh(now.add(const Duration(seconds: 5)).millisecondsSinceEpoch, now), isFalse);
  });
  test('listens only when the gate is open and no session holds the mic', () {
    expect(shouldListen(gateOpen: true, held: false), isTrue);
    expect(shouldListen(gateOpen: true, held: true), isFalse); // charger plugged in during a session
    expect(shouldListen(gateOpen: false, held: false), isFalse);
    expect(shouldListen(gateOpen: false, held: true), isFalse);
  });
  test('wake notification is silent on a fresh channel and still wakes the screen', () {
    final a = wakeNotification.android!;
    expect(a.channelId, 'wake_silent'); // the old 'wake' channel's sound cannot be changed after creation
    expect(a.playSound, isFalse);
    expect(a.enableVibration, isFalse);
    expect(a.importance, Importance.max);
    expect(a.fullScreenIntent, isTrue);
  });
}
