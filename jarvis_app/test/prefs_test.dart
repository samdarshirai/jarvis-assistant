import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/prefs.dart';

import 'auth_test.dart' show MemStore;

class ThrowStore extends MemStore {
  @override
  Future<String?> read(String key) => throw Exception('unreadable');
}

DateTime at(int h, [int m = 0]) => DateTime(2026, 1, 1, h, m);

void main() {
  test('isNightAt crossing midnight', () {
    bool n(DateTime t) => isNightAt(t, 1380, 420);
    expect(n(at(22, 59)), false);
    expect(n(at(23)), true);
    expect(n(at(0)), true);
    expect(n(at(6, 59)), true);
    expect(n(at(7)), false);
    expect(n(at(12)), false);
  });

  test('isNightAt same-day range', () {
    expect(isNightAt(at(3), 60, 300), true);
    expect(isNightAt(at(5), 60, 300), false);
  });

  test('isNightAt start == end is never night', () {
    expect(isNightAt(at(3), 0, 0), false);
  });

  test('isNight is true before load', () {
    expect(Prefs(store: MemStore(), clock: () => at(12)).isNight, true);
  });

  test('load with empty store applies defaults', () async {
    final p = Prefs(store: MemStore(), clock: () => at(2));
    await p.load();
    expect(p.loaded, true);
    expect(p.nightStart, 1380);
    expect(p.nightEnd, 420);
    expect(p.wakeEnabled, true);
    expect(p.wakeChosen, false);
    expect(p.isNight, true);
    final d = Prefs(store: MemStore(), clock: () => at(12));
    await d.load();
    expect(d.isNight, false);
  });

  test('out-of-range or garbage night hours fall back to defaults', () async {
    for (final (s, e) in [('99999', '-5'), ('abc', '7am')]) {
      final st = MemStore();
      await st.write('night_start', s);
      await st.write('night_end', e);
      final p = Prefs(store: st, clock: () => at(2));
      await p.load();
      expect((p.nightStart, p.nightEnd), (1380, 420));
      expect(p.isNight, true);
    }
  });

  test('unreadable store stays unloaded and night', () async {
    final p = Prefs(store: ThrowStore(), clock: () => at(12));
    await p.load();
    expect(p.loaded, false);
    expect(p.isNight, true);
  });

  test('setNight persists and 0,0 disables night', () async {
    final s = MemStore();
    final p = Prefs(store: s, clock: () => at(3));
    await p.load();
    var notified = 0;
    p.addListener(() => notified++);
    await p.setNight(0, 0);
    expect(p.isNight, false);
    expect(notified, 1);
    final q = Prefs(store: s, clock: () => at(3));
    await q.load();
    expect((q.nightStart, q.nightEnd), (0, 0));
    expect(q.isNight, false);
  });

  test('wake flags persist', () async {
    final s = MemStore();
    final p = Prefs(store: s);
    await p.load();
    await p.setWakeEnabled(false);
    await p.markWakeChosen();
    expect(await storedWakeEnabled(s), false);
    expect(await storedWakeEnabled(MemStore()), true);
    final q = Prefs(store: s);
    await q.load();
    expect(q.wakeEnabled, false);
    expect(q.wakeChosen, true);
  });
}
