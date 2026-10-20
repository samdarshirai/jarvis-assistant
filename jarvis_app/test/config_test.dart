import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/config.dart';
import 'auth_test.dart' show MemStore;

void main() {
  test('stored owner token survives and only the baked URL is used', () async {
    final config = await MemStore({'url': 'https://old.test', 'token': 'owner-token'}).load();
    expect(config.url, 'https://h.test');
    expect(config.token, 'owner-token');
  });

  test('empty token is unsigned even with a baked legacy token', () async {
    expect((await MemStore({'token': '  '}).load()).token, isNull);
    expect((await MemStore().load()).token, isNull);
  });
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
