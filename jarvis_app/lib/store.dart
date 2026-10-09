import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'config.dart';

// Optional build-time pairing: flutter build apk --dart-define=JARVIS_URL=... --dart-define=JARVIS_TOKEN=...
const _builtUrl = String.fromEnvironment('JARVIS_URL'), _builtToken = String.fromEnvironment('JARVIS_TOKEN');

class ConfigStore {
  const ConfigStore();
  static const _s = FlutterSecureStorage();
  Future<Config?> load() async {
    try {
      final u = await _s.read(key: 'url'), t = await _s.read(key: 'token');
      if (u != null && t != null) return Config(u, t); // a manual pairing wins over the built-in one
    } catch (_) {} // unreadable keystore (backup restore etc.): fall through to the built-in pairing
    return _builtUrl.isEmpty || _builtToken.isEmpty ? null : const Config(_builtUrl, _builtToken);
  }

  Future<void> save(Config c) async {
    await _s.write(key: 'url', value: c.url);
    await _s.write(key: 'token', value: c.token);
  }
}
