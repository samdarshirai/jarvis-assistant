import 'package:flutter/material.dart';

import 'dashboard.dart';
import 'theme.dart';

const _weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const _months = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

String greeting(DateTime n) => n.hour < 12 ? 'Good morning' : (n.hour < 18 ? 'Good afternoon' : 'Good evening');
String dateLine(DateTime n) => '${_weekdays[n.weekday - 1]}, ${n.day} ${_months[n.month - 1]}';
String hhmm(DateTime d) => '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';

String dayLabel(DateTime at, DateTime now) {
  final d = DateTime.utc(at.year, at.month, at.day).difference(DateTime.utc(now.year, now.month, now.day)).inDays;
  return d == 0 ? 'Today' : (d == 1 ? 'Tomorrow' : _weekdays[at.weekday - 1]);
}

Widget _muted(BuildContext context, String t) =>
    Text(t, style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: mutedText));

Widget _one(String t, {TextStyle? style, int lines = 1}) =>
    Text(t, maxLines: lines, overflow: TextOverflow.ellipsis, style: style);

class DashboardSections extends StatelessWidget {
  const DashboardSections({super.key, required this.controller, required this.onPlayBrief, this.now = DateTime.now});
  final DashboardController controller;
  final void Function(String text) onPlayBrief;
  final DateTime Function() now;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final d = controller.data, n = now(), th = Theme.of(context).textTheme;
          if (d == null) {
            if (!controller.offline) return const Padding(padding: EdgeInsets.all(48), child: Center(child: CircularProgressIndicator()));
            return Padding(
              padding: const EdgeInsets.all(32),
              child: Column(children: [
                Text("Couldn't reach Jarvis", style: th.titleMedium),
                const SizedBox(height: 12),
                FilledButton.tonal(onPressed: controller.refresh, child: const Text('Retry')),
              ]),
            );
          }
          return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(greeting(n), style: th.headlineMedium?.copyWith(fontWeight: FontWeight.w300, letterSpacing: -0.5)),
            _muted(context, dateLine(n)),
            if (controller.offline)
              const Padding(padding: EdgeInsets.only(top: 8), child: Chip(avatar: Icon(Icons.cloud_off, size: 16), label: Text('Offline'))),
            if (d.reauth)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text('Google needs re-consent — some sections may be missing.',
                    style: th.bodyMedium?.copyWith(color: accentMail)),
              ),
            const SizedBox(height: 16),
            _brief(context, d),
            const SizedBox(height: 12),
            _calendar(context, d.events),
            const SizedBox(height: 12),
            _tasks(context, d.tasks),
            const SizedBox(height: 12),
            _mail(context, d.unread),
            const SizedBox(height: 12),
            _notes(context, d.notes),
            const SizedBox(height: 12),
            _alarm(context, n),
          ]);
        },
      );

  Widget _unavailable(BuildContext context) => _muted(context, 'Unavailable');

  Widget _brief(BuildContext context, DashboardData d) {
    final b = d.brief;
    return GlassCard(
      title: 'Morning brief',
      icon: Icons.wb_sunny_outlined,
      accent: accentBrief,
      trailing: b == null
          ? null
          : TextButton.icon(
              onPressed: () => onPlayBrief(b),
              style: TextButton.styleFrom(foregroundColor: accentBrief),
              icon: const Icon(Icons.play_arrow, size: 18),
              label: const Text('Play')),
      child: b == null ? _unavailable(context) : _one(b, lines: 6),
    );
  }

  Widget _calendar(BuildContext context, List<EventItem>? events) => GlassCard(
        title: 'Calendar',
        icon: Icons.event,
        accent: accentCal,
        child: events == null
            ? _unavailable(context)
            : events.isEmpty
                ? _muted(context, 'No events today')
                : Column(children: [
                    for (final e in events)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          SizedBox(
                              width: 64,
                              child: Text(e.allDay || e.start == null ? 'All day' : hhmm(e.start!),
                                  style: const TextStyle(fontWeight: FontWeight.w600, color: accentCal))),
                          Expanded(
                              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            _one(e.summary),
                            if (e.location != null) _one(e.location!, style: const TextStyle(color: mutedText, fontSize: 12)),
                          ])),
                        ]),
                      ),
                  ]),
      );

  Widget _tasks(BuildContext context, List<TaskItem>? tasks) => GlassCard(
        title: 'Tasks',
        icon: Icons.check_circle_outline,
        accent: accentTasks,
        trailing: tasks == null || tasks.isEmpty ? null : Text('${tasks.length}', style: const TextStyle(color: accentTasks)),
        child: tasks == null
            ? _unavailable(context)
            : tasks.isEmpty
                ? _muted(context, 'No pending tasks')
                : Column(children: [
                    for (final t in tasks)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Row(children: [
                          Expanded(child: _one(t.title)),
                          if (t.overdue)
                            const Text('Overdue', style: TextStyle(color: Colors.redAccent, fontSize: 12))
                          else if (t.due != null)
                            Text(t.due!, style: const TextStyle(color: mutedText, fontSize: 12)),
                        ]),
                      ),
                  ]),
      );

  Widget _mail(BuildContext context, UnreadMail? m) => GlassCard(
        title: 'Unread email',
        icon: Icons.mail_outline,
        accent: accentMail,
        trailing: m == null || m.count == 0 ? null : Text('${m.count}${m.more ? '+' : ''} unread', style: const TextStyle(color: accentMail)),
        child: m == null
            ? _unavailable(context)
            : m.items.isEmpty
                ? _muted(context, 'No unread email')
                : Column(children: [
                    for (final i in m.items)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          _one(i.sender, style: const TextStyle(fontWeight: FontWeight.w600)),
                          _one(i.subject, style: const TextStyle(color: mutedText)),
                        ]),
                      ),
                  ]),
      );

  Widget _notes(BuildContext context, List<NoteItem>? notes) => GlassCard(
        title: 'Notes',
        icon: Icons.sticky_note_2_outlined,
        accent: accentNotes,
        child: notes == null
            ? _unavailable(context)
            : notes.isEmpty
                ? _muted(context, 'No notes yet')
                : Column(children: [
                    for (final n in notes)
                      Padding(padding: const EdgeInsets.symmetric(vertical: 4), child: Row(children: [Expanded(child: _one(n.title))])),
                  ]),
      );

  Widget _alarm(BuildContext context, DateTime n) {
    final at = controller.nextAlarm;
    return GlassCard(
      title: 'Next alarm',
      icon: Icons.alarm,
      accent: accentAlarm,
      child: at == null ? _muted(context, 'No alarm set') : Text('${hhmm(at)} · ${dayLabel(at, n)}', style: Theme.of(context).textTheme.titleMedium),
    );
  }
}
