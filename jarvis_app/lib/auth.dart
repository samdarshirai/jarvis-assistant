import 'dart:convert';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';

import 'api.dart';
import 'store.dart';

class SignInStart {
  const SignInStart(this.nonce, this.url);
  final String nonce;
  final Uri url; // Google consent, opened in the phone's browser
}

String _b64(List<int> bytes) => base64Url.encode(bytes).replaceAll('=', '');

/// Google sign-in, PKCE style: [start] sends only sha256(verifier); [claim] sends the verifier with the one-time
/// code from the `jarvis://signin` deep link, so only this install can collect the device token.
class AuthClient {
  AuthClient(this.api, this.store);
  final ApiClient api; // unauthenticated: the /auth routes take no token
  final ConfigStore store;
  bool _claiming = false;

  Future<SignInStart> start() async {
    final verifier = _b64(List.generate(48, (_) => Random.secure().nextInt(256))); // 64 chars of [A-Za-z0-9_-]
    final r = await api.post('/auth/start', {'challenge': _b64(sha256.convert(utf8.encode(verifier)).bytes)});
    final nonce = r['nonce'] as String;
    await store.savePending((nonce: nonce, verifier: verifier));
    return SignInStart(nonce, Uri.parse(r['url'] as String));
  }

  /// The device token for a deep link to the sign-in this install started; null (and no request) for any other link.
  /// A rejected claim throws [ApiError]; either way the attempt is forgotten, so nothing retries it.
  Future<String?> claim(Uri link) async {
    if (_claiming || link.scheme != 'jarvis' || link.host != 'signin') return null;
    final nonce = link.queryParameters['nonce'], code = link.queryParameters['code'];
    if (code == null || code.isEmpty) return null;
    _claiming = true;
    try {
      final p = await store.pending();
      if (p == null || nonce != p.nonce) return null;
      await store.savePending(null);
      final r = await api.post('/auth/claim', {'nonce': nonce, 'verifier': p.verifier, 'code': code});
      final token = r['token'] as String;
      if (token.trim().isEmpty) throw const FormatException('Missing device token');
      await store.saveToken(token);
      return token;
    } finally {
      _claiming = false;
    }
  }
}

/// The phone's IANA timezone (e.g. "Asia/Kolkata"); DateTime only knows abbreviations like "IST".
Future<String?> deviceTimezone() => const MethodChannel('jarvis/tz').invokeMethod<String>('get');
