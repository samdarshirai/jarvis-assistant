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

const _muteSm = TextStyle(color: mutedText, fontSize: 12);

Widget _muted(String t) => Text(t, style: const TextStyle(color: mutedText, fontSize: 13));

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
          final d = controller.data, n = now();
          if (d == null) {
            if (!controller.offline) return const Padding(padding: EdgeInsets.all(48), child: Center(child: CircularProgressIndicator()));
            return Padding(
              padding: const EdgeInsets.all(32),
              child: Column(children: [
                const Text("Couldn't reach Jarvis", style: TextStyle(color: cream, fontSize: 18)),
                const SizedBox(height: 12),
                FilledButton.tonal(onPressed: controller.refresh, child: const Text('Retry')),
              ]),
            );
          }
          return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            _header(),
            const SizedBox(height: 18),
            _greeting(n),
            if (controller.offline)
              const Padding(padding: EdgeInsets.only(top: 8), child: Chip(avatar: Icon(Icons.cloud_off, size: 16), label: Text('Offline'))),
            if (d.reauth)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text('Google needs re-consent — some sections may be missing.', style: TextStyle(color: amber)),
              ),
            const SizedBox(height: 16),
            if (d.brief != null) ...[_brief(d.brief!), const SizedBox(height: 14)],
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Expanded(child: _schedule(d.events)),
              const SizedBox(width: 10),
              Expanded(child: _inbox(d.unread)),
            ]),
            const SizedBox(height: 10),
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Expanded(child: _notes(d.notes)),
              const SizedBox(width: 10),
              Expanded(child: _tasks(d.tasks)),
            ]),
            const SizedBox(height: 10),
            _alarm(n),
          ]);
        },
      );

  Widget _header() => Row(children: [
        Expanded(
            child: Row(crossAxisAlignment: CrossAxisAlignment.baseline, textBaseline: TextBaseline.alphabetic, children: [
          Text('JARVIS', style: logoStyle(21)),
          const SizedBox(width: 10),
          const Flexible(
              child: Text('Your personal assistant',
                  maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12, fontStyle: FontStyle.italic, color: mutedText))),
        ])),
        Container(
          width: 42,
          height: 42,
          alignment: Alignment.center,
          decoration: const BoxDecoration(shape: BoxShape.circle, color: darkPill),
          child: const Text('S', style: TextStyle(color: amber, fontWeight: FontWeight.w700, fontSize: 18)),
        ),
      ]);

  Widget _greeting(DateTime n) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('${greeting(n)},', style: condensed(48, weight: FontWeight.w200, color: cream)),
        const SizedBox(height: 2),
        Text('Sam', style: condensed(60, color: cream)),
        const SizedBox(height: 10),
        Row(children: [
          const Icon(Icons.calendar_today, size: 14, color: mutedText),
          const SizedBox(width: 6),
          Flexible(child: _one(dateLine(n), style: const TextStyle(color: mutedText, fontSize: 13))),
        ]),
      ]);

  Widget _brief(String b) => Tooltip(
        message: 'Play',
        child: Semantics(
          button: true,
          label: 'Play',
          excludeSemantics: true,
          child: GestureDetector(
            onTap: () => onPlayBrief(b),
            child: Container(
              padding: const EdgeInsets.fromLTRB(8, 8, 18, 8),
              decoration: BoxDecoration(
                color: darkPill.withValues(alpha: 0.55),
                borderRadius: BorderRadius.circular(999),
                border: Border.all(color: Colors.white.withValues(alpha: 0.06)),
              ),
              child: Row(children: [
                Container(
                    width: 42,
                    height: 42,
                    decoration: const BoxDecoration(shape: BoxShape.circle, gradient: amberGradient),
                    child: const Icon(Icons.play_arrow, color: amberInk)),
                const SizedBox(width: 12),
                Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Text('Morning brief', style: TextStyle(color: cream, fontWeight: FontWeight.w600, fontSize: 15)),
                  _one(b, style: const TextStyle(color: mutedText, fontSize: 12)),
                ])),
              ]),
            ),
          ),
        ),
      );

  Widget _tile(Widget child, {Color? color}) => Container(
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: 6),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(color: color ?? Colors.white.withValues(alpha: 0.55), borderRadius: BorderRadius.circular(22)),
        child: child,
      );

  Widget _list<T>(List<T>? items, String empty, Widget Function(T) row, {bool dark = false}) {
    final mute = dark ? _muted : (String t) => Text(t, style: TextStyle(color: ink.withValues(alpha: 0.6), fontSize: 13));
    if (items == null) return mute('Unavailable');
    if (items.isEmpty) return mute(empty);
    return Column(children: [for (final i in items) row(i)]);
  }

  Widget _schedule(List<EventItem>? events) => BlushCard(
        title: 'Schedule',
        child: _list<EventItem>(
          events,
          'Free all day',
          (e) => _tile(Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            _one(e.allDay || e.start == null ? 'All day' : hhmm(e.start!), style: TextStyle(fontSize: 11, color: ink.withValues(alpha: 0.6))),
            _one(e.summary, lines: 2, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500)),
          ])),
        ),
      );

  Widget _inbox(UnreadMail? m) => BlushCard(
        title: 'Inbox',
        dark: true,
        trailing: m == null || m.count == 0
            ? null
            : Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(color: amber, borderRadius: BorderRadius.circular(999)),
                child: Text('${m.count}${m.more ? '+' : ''}',
                    style: const TextStyle(color: amberInk, fontWeight: FontWeight.w700, fontSize: 12)),
              ),
        child: _list<MailItem>(
          m?.items,
          'Inbox zero. Nothing unread.',
          dark: true,
          (i) => _tile(
            color: Colors.white.withValues(alpha: 0.08),
            Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              _one(i.sender, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
              _one(i.subject, style: _muteSm),
            ]),
          ),
        ),
      );

  Widget _notes(List<NoteItem>? notes) => Container(
        width: double.infinity,
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(gradient: amberGradient, borderRadius: BorderRadius.circular(32)),
        child: DefaultTextStyle.merge(
          style: const TextStyle(color: amberInk),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('Notes', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            notes == null
                ? const Text('Unavailable')
                : _one(notes.isEmpty ? 'No notes yet' : notes.first.title,
                    lines: 3, style: TextStyle(fontSize: 15, fontWeight: notes.isEmpty ? FontWeight.w400 : FontWeight.w700)),
          ]),
        ),
      );

  Widget _tasks(List<TaskItem>? tasks) => BlushCard(
        title: 'Tasks',
        trailing: tasks == null || tasks.isEmpty
            ? null
            : Text('${tasks.length} left', style: TextStyle(fontSize: 12, color: ink.withValues(alpha: 0.6))),
        child: _list<TaskItem>(
          tasks,
          'Nothing pending.',
          (t) => Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Container(
                  width: 18,
                  height: 18,
                  margin: const EdgeInsets.only(top: 1),
                  decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(5), border: Border.all(color: ink.withValues(alpha: 0.4), width: 1.5))),
              const SizedBox(width: 8),
              Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                _one(t.title, lines: 2, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500)),
                if (t.overdue)
                  const Text('Overdue', style: TextStyle(color: danger, fontSize: 11))
                else if (t.due != null)
                  _one(t.due!, style: TextStyle(color: ink.withValues(alpha: 0.6), fontSize: 11)),
              ])),
            ]),
          ),
        ),
      );

  Widget _alarm(DateTime n) {
    final at = controller.nextAlarm;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
      decoration: BoxDecoration(
        color: darkPill.withValues(alpha: 0.55),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: Colors.white.withValues(alpha: 0.06)),
      ),
      child: Row(children: [
        const Icon(Icons.alarm, color: amber),
        const SizedBox(width: 12),
        const Flexible(child: Text('Next alarm', maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: cream, fontWeight: FontWeight.w600, fontSize: 15))),
        const SizedBox(width: 12),
        Expanded(
            child: Align(
                alignment: Alignment.centerRight,
                child: at == null
                    ? _muted('No alarm set')
                    : Row(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.baseline, textBaseline: TextBaseline.alphabetic, children: [
                        Text(hhmm(at), style: condensed(22, color: cream)),
                        const SizedBox(width: 6),
                        Flexible(child: _one(dayLabel(at, n), style: _muteSm)),
                      ]))),
      ]),
    );
  }
}
