import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/push.dart';

void main() {
  test('takes the speak text from the data payload', () {
    expect(speakTextOf({'speak': 'Good morning. Brief here.'}), 'Good morning. Brief here.');
  });
  test('missing, blank or non-string text is ignored', () {
    expect(speakTextOf({}), isNull);
    expect(speakTextOf({'speak': '   '}), isNull);
    expect(speakTextOf({'speak': 5}), isNull);
  });
}
