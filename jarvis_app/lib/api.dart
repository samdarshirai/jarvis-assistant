import 'dart:convert';
import 'dart:io';

import 'config.dart';

/// A non-200 answer; [body] is the decoded JSON error (e.g. `{error, model}`) when there was one.
class ApiError extends HttpException {
  ApiError(this.status, this.body, String path) : super('$path HTTP $status');
  final int status;
  final Map<String, dynamic>? body;
}

/// Thin device-token JSON client for the Jarvis REST endpoints (/calendar, /tasks, /mail, /notes, /chat, /me, /settings).
/// Tests inject a fake by implementing this class.
class ApiClient {
  const ApiClient(this.config, {this.timeout = const Duration(seconds: 90)});
  final Config config;
  final Duration timeout;

  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) =>
      _send('GET', config.apiUri(path, query));

  Future<Map<String, dynamic>> post(String path, [Map<String, dynamic>? body]) =>
      _send('POST', config.apiUri(path), body: body);

  Future<Map<String, dynamic>> put(String path, Map<String, dynamic> body) =>
      _send('PUT', config.apiUri(path), body: body);

  Future<Map<String, dynamic>> delete(String path) => _send('DELETE', config.apiUri(path));

  Future<Map<String, dynamic>> _send(String method, Uri uri, {Map<String, dynamic>? body}) async {
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
    try {
      final req = await client.openUrl(method, uri);
      if (config.token != null) req.headers.set('Authorization', 'Bearer ${config.token}');
      if (body != null) {
        req.headers.contentType = ContentType.json;
        req.write(jsonEncode(body));
      }
      final res = await req.close().timeout(timeout);
      final text = await res.transform(utf8.decoder).join().timeout(timeout);
      Object? j;
      try {
        j = jsonDecode(text);
      } on FormatException {
        j = null;
      }
      if (res.statusCode != 200) throw ApiError(res.statusCode, j is Map<String, dynamic> ? j : null, uri.path);
      if (j is! Map<String, dynamic>) throw FormatException('${uri.path} body');
      return j;
    } finally {
      client.close(force: true);
    }
  }
}
