import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'config.dart';

// The backend URL is baked in (flutter build apk --dart-define-from-file=jarvis.env.json); the token never is:
// it comes from Google sign-in and lives in the keystore.
const _builtUrl = String.fromEnvironment('JARVIS_URL');

class ConfigStore {
  const ConfigStore({this.url = _builtUrl});
  final String url;
  static const _s = FlutterSecureStorage();

  /// Raw keystore access; tests override these two with a map.
  Future<String?> read(String key) => _s.read(key: key);
  Future<void> write(String key, String? value) => value == null ? _s.delete(key: key) : _s.write(key: key, value: value);

  /// A null token means "not signed in yet": the app shows the wizard.
  Future<Config> load() async {
    try {
      final token = await read('token');
      return Config(url, token == null || token.trim().isEmpty ? null : token);
    } catch (_) {
      return Config(url, null); // unreadable keystore (backup restore etc.): sign in again
    }
  }

  Future<void> saveToken(String? token) => write('token', token);

  /// The sign-in this install started: only its nonce may be claimed, with its verifier.
  Future<({String nonce, String verifier})?> pending() async {
    final n = await read('signin_nonce'), v = await read('signin_verifier');
    return n == null || v == null ? null : (nonce: n, verifier: v);
  }

  Future<void> savePending(({String nonce, String verifier})? p) async {
    await write('signin_nonce', p?.nonce);
    await write('signin_verifier', p?.verifier);
  }
}
