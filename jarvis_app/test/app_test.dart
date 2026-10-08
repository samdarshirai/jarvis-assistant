import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/app.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/store.dart';
import 'package:jarvis_app/ui.dart';

import 'dash_fixture.dart';

class _Store extends ConfigStore {
  const _Store(this.result, {this.boom = false});
  final Config? result;
  final bool boom;
  @override
  Future<Config?> load() async {
    if (boom) throw Exception('keystore');
    return result;
  }
}

void main() {
  testWidgets('throwing store shows pairing, not a spinner', (t) async {
    await t.pumpWidget(const JarvisApp(store: _Store(null, boom: true)));
    await t.pump();
    expect(find.byType(PairingScreen), findsOneWidget);
  });

  testWidgets('empty store shows pairing', (t) async {
    await t.pumpWidget(const JarvisApp(store: _Store(null)));
    await t.pump();
    expect(find.byType(PairingScreen), findsOneWidget);
  });

  testWidgets('dispose clears AppHost.controller', (t) async {
    await t.pumpWidget(JarvisApp(
        store: const _Store(Config('https://x.test', 'tok')),
        dashboardFetch: (_) async => sampleDashboard(),
        alarm: () async => null));
    await t.pump();
    expect(find.byType(SessionScreen), findsOneWidget);
    expect(AppHost.controller, isNotNull);
    await t.pumpWidget(const SizedBox());
    expect(AppHost.controller, isNull);
  });

  testWidgets('home loads the dashboard once paired', (t) async {
    await t.pumpWidget(JarvisApp(
        store: const _Store(Config('https://x.test', 'tok')),
        dashboardFetch: (_) async => sampleDashboard(),
        alarm: () async => null));
    await t.pump();
    await t.pump();
    expect(find.text('Standup'), findsOneWidget);
    await t.pumpWidget(const SizedBox());
  });
}
