import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'config.dart';

class ConfigStore {
  const ConfigStore();
  static const _s = FlutterSecureStorage();
  Future<Config?> load() async {
    try {
      final u = await _s.read(key: 'url'), t = await _s.read(key: 'token');
      return (u == null || t == null) ? null : Config(u, t);
    } catch (_) {
      return null; // unreadable keystore (backup restore etc.): treat as not paired
    }
  }

  Future<void> save(Config c) async {
    await _s.write(key: 'url', value: c.url);
    await _s.write(key: 'token', value: c.token);
  }
}
