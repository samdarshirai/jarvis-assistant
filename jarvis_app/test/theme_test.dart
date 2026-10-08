import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/theme.dart';

void main() {
  test('theme is dark Material 3', () {
    final t = jarvisTheme();
    expect(t.brightness, Brightness.dark);
    expect(t.useMaterial3, isTrue);
  });

  testWidgets('GlassCard shows title, trailing and child over the background', (t) async {
    await t.pumpWidget(MaterialApp(
        theme: jarvisTheme(),
        home: const Scaffold(
            body: GlassBackground(
                child: GlassCard(
                    title: 'Tasks', icon: Icons.check, accent: accentTasks, trailing: Text('3'), child: Text('body'))))));
    expect(find.text('Tasks'), findsOneWidget);
    expect(find.text('3'), findsOneWidget);
    expect(find.text('body'), findsOneWidget);
  });
}
