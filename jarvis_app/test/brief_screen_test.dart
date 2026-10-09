import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/brief_screen.dart';

void main() {
  test('splitSentences keeps punctuation', () {
    expect(splitSentences('One. Two? Three! Four'), ['One.', 'Two?', 'Three!', 'Four']);
  });

  testWidgets('play advances, tap seeks, dispose stops', (t) async {
    await t.binding.setSurfaceSize(const Size(320, 640));
    final spoken = <String>[];
    var stops = 0;
    const brief = 'Aaaaaaaaaaaaaaa. Bbbbbbbbbbbbbbb. Ccccccccccccccc.'; // 16 chars ~ 1.07s each
    await t.pumpWidget(MaterialApp(home: BriefScreen(brief: brief, speak: spoken.add, stop: () => stops++)));
    await t.tap(find.byIcon(Icons.play_arrow));
    await t.pump();
    expect(spoken.last, brief);
    await t.pump(const Duration(milliseconds: 1200));
    final style = t.widget<Text>(find.text('Bbbbbbbbbbbbbbb.')).style!;
    expect(style.color, const Color(0xFFFCE6DD));
    await t.tap(find.text('Ccccccccccccccc.'));
    await t.pump();
    expect(spoken.last, 'Ccccccccccccccc.');
    expect(stops, 1);
    await t.tap(find.byIcon(Icons.pause));
    await t.pump();
    expect(stops, 2);
    await t.tap(find.byIcon(Icons.play_arrow));
    await t.pump();
    await t.pumpWidget(const SizedBox());
    expect(stops, 3);
    expect(t.takeException(), isNull);
  });
}
