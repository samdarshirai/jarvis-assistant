import 'package:jarvis_app/dashboard.dart';

const sampleJson = <String, dynamic>{
  'events': [
    {'summary': 'Standup', 'start': '2026-10-05T09:00:00', 'end': '2026-10-05T09:15:00', 'location': 'Office', 'all_day': false},
    {'summary': 'Holiday', 'start': '2026-10-05', 'end': '2026-10-06', 'location': null, 'all_day': true},
  ],
  'tasks': [
    {'title': 'Pay rent', 'due': '2026-10-03', 'overdue': true},
    {'title': 'Buy milk', 'due': null, 'overdue': false},
  ],
  'unread': {
    'count': 5,
    'more': true,
    'items': [
      {'from': 'Boss <boss@corp.com>', 'subject': 'Contract'},
    ],
  },
  'notes': [
    {'id': 1, 'title': 'Gym plan', 'updated_at': '2026-10-05T08:00:00'},
  ],
  'brief': 'Calendar today: 09:00 Standup.',
  'reauth': false,
};

DashboardData sampleDashboard() => DashboardData.fromJson(Map<String, dynamic>.from(sampleJson));
