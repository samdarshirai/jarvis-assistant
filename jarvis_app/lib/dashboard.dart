import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import 'config.dart';

String? _s(Object? v) => v is String ? v : null;

List<T>? _list<T>(Object? v, T Function(Map<String, dynamic>) f) =>
    v is! List ? null : [for (final e in v) if (e is Map<String, dynamic>) f(e)];

/// `Boss <boss@corp.com>` -> `Boss`; a bare address stays as is.
String senderName(String from) {
  final i = from.indexOf('<');
  var n = (i > 0 ? from.substring(0, i) : from.replaceAll(RegExp(r'[<>]'), '')).replaceAll('"', '').trim();
  return n.isEmpty ? 'Unknown sender' : n;
}

class EventItem {
  const EventItem({required this.summary, this.start, this.location, required this.allDay});
  final String summary;
  final DateTime? start;
  final String? location;
  final bool allDay;

  static EventItem from(Map<String, dynamic> j) {
    final s = _s(j['start']);
    return EventItem(
        summary: _s(j['summary']) ?? '(no title)',
        start: s == null ? null : DateTime.tryParse(s)?.toLocal(),
        location: _s(j['location']),
        allDay: j['all_day'] == true);
  }
}

class TaskItem {
  const TaskItem({required this.title, this.due, required this.overdue});
  final String title;
  final String? due;
  final bool overdue;
  static TaskItem from(Map<String, dynamic> j) =>
      TaskItem(title: _s(j['title']) ?? '', due: _s(j['due']), overdue: j['overdue'] == true);
}

class MailItem {
  const MailItem({required this.sender, required this.subject});
  final String sender, subject;
  static MailItem from(Map<String, dynamic> j) =>
      MailItem(sender: senderName(_s(j['from']) ?? ''), subject: _s(j['subject']) ?? '');
}

class UnreadMail {
  const UnreadMail({required this.count, required this.more, required this.items});
  final int count;
  final bool more;
  final List<MailItem> items;
}

class NoteItem {
  const NoteItem({required this.title, this.updatedAt});
  final String title;
  final DateTime? updatedAt;
  static NoteItem from(Map<String, dynamic> j) {
    final u = _s(j['updated_at']);
    return NoteItem(title: _s(j['title']) ?? '', updatedAt: u == null ? null : DateTime.tryParse(u)?.toLocal());
  }
}

class DashboardData {
  const DashboardData({this.events, this.tasks, this.unread, this.notes, this.brief, this.reauth = false});
  final List<EventItem>? events;
  final List<TaskItem>? tasks;
  final UnreadMail? unread;
  final List<NoteItem>? notes;
  final String? brief;
  final bool reauth;

  factory DashboardData.fromJson(Map<String, dynamic> j) {
    final u = j['unread'];
    return DashboardData(
      events: _list(j['events'], EventItem.from),
      tasks: _list(j['tasks'], TaskItem.from),
      unread: u is Map<String, dynamic>
          ? UnreadMail(
              count: u['count'] is int ? u['count'] as int : 0,
              more: u['more'] == true,
              items: _list(u['items'], MailItem.from) ?? const [])
          : null,
      notes: _list(j['notes'], NoteItem.from),
      brief: _s(j['brief']),
      reauth: j['reauth'] == true,
    );
  }
}

Future<DashboardData> fetchDashboard(Config c) async {
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
  try {
    final req = await client.getUrl(c.dashboardUri);
    req.headers.set('Authorization', 'Bearer ${c.token}');
    final res = await req.close().timeout(const Duration(seconds: 20));
    if (res.statusCode != 200) {
      await res.drain<void>();
      throw HttpException('dashboard HTTP ${res.statusCode}');
    }
    final j = jsonDecode(await res.transform(utf8.decoder).join());
    if (j is! Map<String, dynamic>) throw const FormatException('dashboard body');
    return DashboardData.fromJson(j);
  } finally {
    client.close(force: true);
  }
}

class DashboardController extends ChangeNotifier {
  DashboardController({required this.fetch, this.alarm});
  final Future<DashboardData> Function() fetch;
  final Future<DateTime?> Function()? alarm;

  DashboardData? data; // last good snapshot; kept when a refresh fails
  DateTime? nextAlarm;
  bool offline = false, loading = false;
  bool _disposed = false;

  @override
  void notifyListeners() {
    if (!_disposed) super.notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }

  Future<void> refresh() async {
    if (loading) return;
    loading = true;
    notifyListeners();
    try {
      data = await fetch();
      offline = false;
    } catch (_) {
      offline = true;
    }
    try {
      nextAlarm = await alarm?.call();
    } catch (_) {
      nextAlarm = null;
    }
    loading = false;
    notifyListeners();
  }
}
