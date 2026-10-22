import 'package:flutter/foundation.dart';

import 'store.dart';

const _defStart = 23 * 60, _defEnd = 7 * 60;

/// Night is [startMin, endMin) in minutes since midnight; may cross midnight; start == end means no night.
bool isNightAt(DateTime now, int startMin, int endMin) {
  final m = now.hour * 60 + now.minute;
  if (startMin == endMin) return false;
  return startMin < endMin ? m >= startMin && m < endMin : m >= startMin || m < endMin;
}

/// Readable from the wake service isolate (no Prefs instance there); defaults to on.
Future<bool> storedWakeEnabled([ConfigStore store = const ConfigStore()]) async {
  try {
    return await store.read('wake_enabled') != '0';
  } catch (_) {
    return true;
  }
}

/// Fails closed: until prefs are loaded (or if the store is unreadable) it is night.
class Prefs extends ChangeNotifier {
  Prefs({ConfigStore store = const ConfigStore(), DateTime Function() clock = DateTime.now}) : _store = store, _clock = clock;
  final ConfigStore _store;
  final DateTime Function() _clock;

  bool _loaded = false, _wakeEnabled = true, _wakeChosen = false;
  int _start = _defStart, _end = _defEnd;

  bool get loaded => _loaded;
  bool get isNight => !_loaded || isNightAt(_clock(), _start, _end);
  bool get wakeEnabled => _wakeEnabled;
  int get nightStart => _start;
  int get nightEnd => _end;
  bool get wakeChosen => _wakeChosen;

  Future<void> load() async {
    try {
      _wakeEnabled = await _store.read('wake_enabled') != '0';
      _start = int.tryParse(await _store.read('night_start') ?? '') ?? _defStart;
      _end = int.tryParse(await _store.read('night_end') ?? '') ?? _defEnd;
      _wakeChosen = await _store.read('wake_chosen') == '1';
      _loaded = true;
    } catch (_) {
      _loaded = false;
    }
    notifyListeners();
  }

  Future<void> setWakeEnabled(bool v) async {
    _wakeEnabled = v;
    await _store.write('wake_enabled', v ? '1' : '0');
    notifyListeners();
  }

  Future<void> setNight(int startMin, int endMin) async {
    _start = startMin;
    _end = endMin;
    await _store.write('night_start', '$startMin');
    await _store.write('night_end', '$endMin');
    notifyListeners();
  }

  Future<void> markWakeChosen() async {
    _wakeChosen = true;
    await _store.write('wake_chosen', '1');
    notifyListeners();
  }
}
