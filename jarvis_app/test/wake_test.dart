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
}
