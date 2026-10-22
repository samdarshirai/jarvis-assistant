import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/wake_gate.dart';

void main() {
  test('open only when enabled and (charging or android auto)', () {
    for (final e in [false, true]) {
      for (final c in [false, true]) {
        for (final a in [false, true]) {
          expect(wakeGateOpen(enabled: e, charging: c, androidAuto: a),
              e && (c || a),
              reason: 'enabled=$e charging=$c auto=$a');
        }
      }
    }
  });
}
