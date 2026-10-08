import 'dart:convert';
import 'dart:io';

import 'config.dart';

/// Thin device-token JSON client for the Jarvis REST endpoints (/calendar, /tasks, /mail, /notes, /chat).
/// Tests inject a fake by implementing this class.
class ApiClient {
  const ApiClient(this.config, {this.timeout = const Duration(seconds: 20)});
  final Config config;
  final Duration timeout;

  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) =>
      _send('GET', config.apiUri(path, query));

  Future<Map<String, dynamic>> post(String path, [Map<String, dynamic>? body]) =>
      _send('POST', config.apiUri(path), body: body);

  Future<Map<String, dynamic>> _send(String method, Uri uri, {Map<String, dynamic>? body}) async {
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
    try {
      final req = await client.openUrl(method, uri);
      req.headers.set('Authorization', 'Bearer ${config.token}');
      if (body != null) {
        req.headers.contentType = ContentType.json;
        req.write(jsonEncode(body));
      }
      final res = await req.close().timeout(timeout);
      final text = await res.transform(utf8.decoder).join().timeout(timeout);
      if (res.statusCode != 200) throw HttpException('${uri.path} HTTP ${res.statusCode}');
      final j = jsonDecode(text);
      if (j is! Map<String, dynamic>) throw FormatException('${uri.path} body');
      return j;
    } finally {
      client.close(force: true);
    }
  }
}
