import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/config.dart';

void main() {
  test('https becomes wss on /voice', () {
    expect(const Config('https://jarvis.example.com', 't').voiceUri.toString(), 'wss://jarvis.example.com/voice');
  });
  test('http becomes ws and a trailing slash is tolerated', () {
    expect(const Config('http://192.168.1.5:8000/', 't').voiceUri.toString(), 'ws://192.168.1.5:8000/voice');
  });
  test('a bare host is treated as https', () {
    expect(const Config('jarvis.example.com', 't').voiceUri.toString(), 'wss://jarvis.example.com/voice');
  });
}
