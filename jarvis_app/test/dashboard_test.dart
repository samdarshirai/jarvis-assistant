import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/dashboard.dart';

import 'dash_fixture.dart';

void main() {
  test('fromJson parses every section', () {
    final d = sampleDashboard();
    expect(d.events!.map((e) => e.summary), ['Standup', 'Holiday']);
    expect(d.events![0].start, DateTime(2026, 10, 5, 9));
    expect(d.events![0].allDay, isFalse);
    expect(d.events![1].allDay, isTrue);
    expect(d.tasks!.first.overdue, isTrue);
    expect(d.tasks!.last.due, isNull);
    expect(d.unread!.count, 5);
    expect(d.unread!.more, isTrue);
    expect(d.unread!.items.single.sender, 'Boss');
    expect(d.notes!.single.title, 'Gym plan');
    expect(d.brief, startsWith('Calendar today'));
    expect(d.reauth, isFalse);
  });

  test('null or missing sections stay null, malformed items are skipped, nothing throws', () {
    final d = DashboardData.fromJson({
      'events': null,
      'tasks': [
        {'title': 7},
        'junk',
        {'title': 'ok'},
      ],
      'unread': 'nope',
      'brief': 5,
      'reauth': 'yes',
    });
    expect(d.events, isNull);
    expect(d.tasks!.map((t) => t.title), ['', 'ok']);
    expect(d.unread, isNull);
    expect(d.notes, isNull);
    expect(d.brief, isNull);
    expect(d.reauth, isFalse);
  });

  test('senderName handles display names, bare addresses and empties', () {
    expect(senderName('Boss <boss@corp.com>'), 'Boss');
    expect(senderName('"Ann, B" <a@b.com>'), 'Ann, B');
    expect(senderName('<a@b.com>'), 'a@b.com');
    expect(senderName('a@b.com'), 'a@b.com');
    expect(senderName(''), 'Unknown sender');
  });

  test('Config.dashboardUri follows the url scheme', () {
    expect(const Config('https://x.test/', 't').dashboardUri.toString(), 'https://x.test/dashboard');
    expect(const Config('x.test', 't').dashboardUri.toString(), 'https://x.test/dashboard');
    expect(const Config('http://10.0.2.2:8000', 't').dashboardUri.toString(), 'http://10.0.2.2:8000/dashboard');
  });

  group('DashboardController', () {
    test('refresh stores data and the alarm; failure keeps the last good data and flags offline', () async {
      var fail = false;
      final c = DashboardController(
          fetch: () async => fail ? throw Exception('down') : sampleDashboard(), alarm: () async => DateTime(2026, 10, 6, 6, 30));
      await c.refresh();
      expect(c.data, isNotNull);
      expect(c.nextAlarm, DateTime(2026, 10, 6, 6, 30));
      expect(c.offline, isFalse);
      fail = true;
      await c.refresh();
      expect(c.data, isNotNull);
      expect(c.offline, isTrue);
      expect(c.loading, isFalse);
    });

    test('a throwing alarm channel just means no alarm', () async {
      final c = DashboardController(fetch: () async => sampleDashboard(), alarm: () async => throw Exception('no channel'));
      await c.refresh();
      expect(c.nextAlarm, isNull);
      expect(c.data, isNotNull);
    });

    test('overlapping refresh calls fetch once', () async {
      var calls = 0;
      final c = DashboardController(fetch: () async {
        calls++;
        return sampleDashboard();
      });
      await Future.wait([c.refresh(), c.refresh()]);
      expect(calls, 1);
    });

    test('a refresh finishing after dispose does not throw', () async {
      final c = DashboardController(fetch: () async => sampleDashboard());
      final f = c.refresh();
      c.dispose();
      await f;
    });
  });
}
