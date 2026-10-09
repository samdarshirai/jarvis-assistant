import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/confirm_card.dart';

void main() {
  testWidgets('shows summary, warning when untrusted, and fires callbacks', (t) async {
    var ok = 0, no = 0;
    await t.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ConfirmCard(summary: 'Create Gym', afterUntrusted: true, onConfirm: () => ok++, onCancel: () => no++))));
    expect(find.text('Needs your OK'), findsOneWidget);
    expect(find.text('Create Gym'), findsOneWidget);
    expect(find.textContaining('after reading third-party content (email or web)'), findsOneWidget);
    await t.tap(find.text('Confirm'));
    await t.tap(find.text('Cancel'));
    expect([ok, no], [1, 1]);
  });

  testWidgets('no warning when trusted', (t) async {
    await t.pumpWidget(MaterialApp(
        home: Scaffold(body: ConfirmCard(summary: 'x', afterUntrusted: false, onConfirm: () {}, onCancel: () {}))));
    expect(find.textContaining('third-party'), findsNothing);
  });
}
