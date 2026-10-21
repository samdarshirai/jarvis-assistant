import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/api.dart';
import 'package:jarvis_app/auth.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/store.dart';

/// In-memory keystore.
class MemStore extends ConfigStore {
  MemStore([Map<String, String>? m]) : m = m ?? {}, super(url: 'https://h.test');
  final Map<String, String> m;
  @override
  Future<String?> read(String key) async => m[key];
  @override
  Future<void> write(String key, String? value) async => value == null ? m.remove(key) : m[key] = value;
}

/// Records every call; routes are keyed "METHOD /path" and hold a Map, an Exception, or a function of the body.
class RecApi extends ApiClient {
  RecApi(this.routes, {String? token = 'tok'}) : super(Config('https://h.test', token));
  final Map<String, Object> routes;
  final calls = <(String, String, Map<String, dynamic>?)>[];

  Future<Map<String, dynamic>> _do(String method, String path, [Map<String, dynamic>? body]) async {
    calls.add((method, path, body));
    var r = routes['$method $path'];
    if (r is Function) r = r(body) as Object;
    if (r is Future<Map<String, dynamic>>) r = await r;
    if (r is Exception) throw r;
    if (r == null) throw ApiError(404, null, path);
    return r as Map<String, dynamic>;
  }

  List<String> get paths => [for (final c in calls) '${c.$1} ${c.$2}'];
  @override
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) => _do('GET', path);
  @override
  Future<Map<String, dynamic>> post(String path, [Map<String, dynamic>? body]) => _do('POST', path, body);
  @override
  Future<Map<String, dynamic>> put(String path, Map<String, dynamic> body) => _do('PUT', path, body);
  @override
  Future<Map<String, dynamic>> delete(String path) => _do('DELETE', path);
}

Map<String, dynamic> meBody({String? displayName = 'Alex', bool llm = true, String? deepgram = 'wxyz', String? tavily = 'tavy'}) => {
      'email': 'a@b.test',
      'display_name': displayName,
      'tz': 'Asia/Kolkata',
      'setup': {'google': true, 'llm': llm, 'deepgram': deepgram != null, 'tavily': tavily != null},
      'keys': {
        'llm_base_url': llm ? 'https://openrouter.ai/api/v1' : null,
        'llm_key_last4': llm ? 'abcd' : null,
        'model_fast': 'fast/model',
        'model_strong': 'strong/model',
        'deepgram_last4': deepgram,
        'tavily_last4': tavily,
      },
    };

void main() {
  test('start_sends_challenge_not_verifier', () async {
    final store = MemStore();
    final api = RecApi({
      'POST /auth/start': {'nonce': 'n1', 'url': 'https://accounts.google.com/o?x=1'}
    }, token: null);
    final s = await AuthClient(api, store).start();
    expect(s.url, Uri.parse('https://accounts.google.com/o?x=1'));
    final p = (await store.pending())!;
    expect(p.nonce, 'n1');
    expect(p.verifier, matches(RegExp(r'^[A-Za-z0-9._~-]{64}$')));
    final body = api.calls.single.$3!;
    expect(body.keys, ['challenge']);
    expect(body['challenge'], base64Url.encode(sha256.convert(utf8.encode(p.verifier)).bytes).replaceAll('=', ''));
    expect(body['challenge'], hasLength(43));
    expect(jsonEncode(body), isNot(contains(p.verifier)));
  });

  test('claim_sends_verifier_and_stores_token', () async {
    final store = MemStore({'signin_nonce': 'n1', 'signin_verifier': 'v' * 64});
    final api = RecApi({
      'POST /auth/claim': {'token': 'devtok', 'needs_setup': true}
    }, token: null);
    expect(await AuthClient(api, store).claim(Uri.parse('jarvis://signin?nonce=n1&code=c1')), 'devtok');
    expect(api.calls.single.$3, {'nonce': 'n1', 'verifier': 'v' * 64, 'code': 'c1'});
    expect(await store.read('token'), 'devtok');
    expect(await store.pending(), isNull);
  });

  test('ignores_deep_link_for_foreign_nonce', () async {
    final store = MemStore({'signin_nonce': 'n1', 'signin_verifier': 'v' * 64});
    final api = RecApi({'POST /auth/claim': {'token': 'devtok'}}, token: null);
    final auth = AuthClient(api, store);
    expect(await auth.claim(Uri.parse('jarvis://signin?nonce=evil&code=c1')), isNull);
    expect(await auth.claim(Uri.parse('https://other.test/?nonce=n1&code=c1')), isNull);
    expect(await AuthClient(api, MemStore()).claim(Uri.parse('jarvis://signin?nonce=n1&code=c1')), isNull);
    expect(api.calls, isEmpty);
    expect(await store.read('token'), isNull);
    expect((await store.pending())?.nonce, 'n1'); // our own sign-in can still finish
  });

  test('a failed claim throws and forgets the attempt', () async {
    final store = MemStore({'signin_nonce': 'n1', 'signin_verifier': 'v' * 64});
    final api = RecApi({'POST /auth/claim': ApiError(401, {'error': 'unauthorized'}, '/auth/claim')}, token: null);
    await expectLater(AuthClient(api, store).claim(Uri.parse('jarvis://signin?nonce=n1&code=c1')), throwsA(isA<ApiError>()));
    expect(await store.pending(), isNull);
    expect(await store.read('token'), isNull);
  });

  test('duplicate callbacks exchange a finish code only once', () async {
    final store = MemStore({'signin_nonce': 'n1', 'signin_verifier': 'v' * 64});
    final api = RecApi({'POST /auth/claim': {'token': 'devtok'}}, token: null);
    final auth = AuthClient(api, store);
    final link = Uri.parse('jarvis://signin?nonce=n1&code=c1');
    final results = await Future.wait([auth.claim(link), auth.claim(link)]);
    expect(results.whereType<String>(), ['devtok']);
    expect(api.calls.length, 1);
  });

  test('missing finish code is ignored without consuming our pending attempt', () async {
    final store = MemStore({'signin_nonce': 'n1', 'signin_verifier': 'v' * 64});
    final api = RecApi({}, token: null);
    final auth = AuthClient(api, store);
    expect(await auth.claim(Uri.parse('jarvis://signin?nonce=n1')), isNull);
    expect(await auth.claim(Uri.parse('jarvis://signin?nonce=n1&code=')), isNull);
    expect((await store.pending())?.nonce, 'n1');
    expect(api.calls, isEmpty);
  });
}
