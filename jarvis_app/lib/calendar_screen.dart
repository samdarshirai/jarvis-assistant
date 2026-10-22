import 'package:flutter/material.dart';

import 'api.dart';
import 'dashboard_cards.dart';
import 'theme.dart';

const _monthNames = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];
const _dayInitials = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/// Outlined/filled 46px round icon button used in screen top bars.
class RoundButton extends StatelessWidget {
  const RoundButton({super.key, required this.icon, required this.tooltip, required this.onTap, this.filled = false});
  final IconData icon;
  final String tooltip;
  final VoidCallback onTap;
  final bool filled;

  @override
  Widget build(BuildContext context) => Tooltip(
        message: tooltip,
        child: Semantics(
          button: true,
          label: tooltip,
          excludeSemantics: true,
          child: GestureDetector(
            onTap: onTap,
            child: Container(
              width: 46,
              height: 46,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: filled ? amber : null,
                border: filled ? null : Border.all(color: Colors.white.withValues(alpha: 0.25)),
              ),
              child: Icon(icon, size: 22, color: filled ? amberInk : cream),
            ),
          ),
        ),
      );
}

/// Cocoa background + top bar (back, centred title, optional trailing) shared by the list screens.
class ScreenShell extends StatelessWidget {
  const ScreenShell({super.key, required this.title, required this.child, this.trailing, this.onMic});
  final String title;
  final Widget child;
  final Widget? trailing;
  /// Floating amber mic (the design's "ask Jarvis" button); null hides it.
  final VoidCallback? onMic;

  @override
  Widget build(BuildContext context) => Scaffold(
        body: CocoaBackground(
          child: SafeArea(
            child: Column(children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                child: Row(children: [
                  RoundButton(icon: Icons.arrow_back, tooltip: 'Back', onTap: () => Navigator.of(context).maybePop()),
                  Expanded(
                      child: Text(title,
                          textAlign: TextAlign.center,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(color: cream, fontSize: 16))),
                  SizedBox(width: 46, child: trailing),
                ]),
              ),
              Expanded(
                child: Stack(children: [
                  Positioned.fill(child: child),
                  if (onMic != null)
                    Positioned(
                      right: 20,
                      bottom: 30,
                      child: Tooltip(
                        message: 'Talk',
                        child: GestureDetector(
                          onTap: onMic,
                          child: Container(
                            width: 64,
                            height: 64,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              gradient: amberGradient,
                              boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.35), blurRadius: 30, offset: const Offset(0, 10))],
                            ),
                            child: const Icon(Icons.mic, size: 28, color: amberInk),
                          ),
                        ),
                      ),
                    ),
                ]),
              ),
            ]),
          ),
        ),
      );
}

/// "Couldn't reach Jarvis" + Retry.
class ErrorRetry extends StatelessWidget {
  const ErrorRetry({super.key, required this.onRetry});
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Text("Couldn't reach Jarvis", style: TextStyle(color: cream, fontSize: 18)),
          const SizedBox(height: 12),
          FilledButton.tonal(onPressed: onRetry, child: const Text('Retry')),
        ]),
      );
}

class CalEvent {
  const CalEvent({required this.id, required this.summary, this.start, this.end, this.location, required this.allDay});
  final String id, summary;
  final DateTime? start, end;
  final String? location;
  final bool allDay;

  static CalEvent from(Map<String, dynamic> j) {
    DateTime? p(Object? v) => v is String ? DateTime.tryParse(v)?.toLocal() : null;
    final loc = j['location'];
    return CalEvent(
        id: '${j['id']}',
        summary: j['summary'] is String ? j['summary'] as String : '(no title)',
        start: p(j['start']),
        end: p(j['end']),
        location: loc is String && loc.isNotEmpty ? loc : null,
        allDay: j['all_day'] == true);
  }

  /// All-day `end` is exclusive.
  bool onDay(DateTime d) {
    final s = start;
    if (s == null) return false;
    final day = DateTime(d.year, d.month, d.day), sd = DateTime(s.year, s.month, s.day);
    if (!allDay) return sd == day;
    final e = end == null ? sd.add(const Duration(days: 1)) : DateTime(end!.year, end!.month, end!.day);
    return !day.isBefore(sd) && day.isBefore(e);
  }

  String? get duration {
    if (allDay || start == null || end == null) return null;
    final m = end!.difference(start!).inMinutes;
    if (m <= 0) return null;
    return m < 60 ? '${m}m' : (m % 60 == 0 ? '${m ~/ 60}h' : '${m ~/ 60}h ${m % 60}m');
  }
}

String _ymd(DateTime d) => '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

class CalendarScreen extends StatefulWidget {
  const CalendarScreen({super.key, required this.api, required this.onAskJarvis, this.now = DateTime.now});
  final ApiClient api;
  final VoidCallback onAskJarvis;
  final DateTime Function() now;

  @override
  State<CalendarScreen> createState() => _CalendarScreenState();
}

class _CalendarScreenState extends State<CalendarScreen> {
  List<CalEvent>? _events;
  bool _loading = true, _error = false;
  late DateTime _selected;
  final _open = <String>{};

  DateTime get _today => DateTime(widget.now().year, widget.now().month, widget.now().day);
  bool _month = false;

  static DateTime _mondayOf(DateTime d) => DateTime(d.year, d.month, d.day - (d.weekday - 1));
  DateTime get _monday => _mondayOf(_selected);
  // month view fetches the whole six-week grid so leading/trailing days show their dots
  DateTime get _gridStart => _mondayOf(DateTime(_selected.year, _selected.month));

  void _go(DateTime to, {bool month = false}) {
    _selected = to;
    _month = month || _month;
    _load();
  }

  void _step(int dir) => _go(_month
      ? DateTime(_selected.year, _selected.month + dir, 1)
      : _selected.add(Duration(days: 7 * dir)));

  @override
  void initState() {
    super.initState();
    _selected = _today;
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = false;
    });
    try {
      final j = await widget.api.get(
          '/calendar', {'from': _ymd(_month ? _gridStart : _monday), 'days': _month ? '42' : '7'});
      final l = j['events'];
      final ev = l is List ? [for (final e in l) if (e is Map<String, dynamic>) CalEvent.from(e)] : null;
      if (mounted) {
        setState(() {
          _events = ev;
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _error = true;
          _loading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => ScreenShell(
        title: 'Calendar',
        onMic: widget.onAskJarvis,
        trailing: RoundButton(icon: Icons.add, tooltip: 'Add event', onTap: _addEvent),
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error
                ? ErrorRetry(onRetry: _load)
                : _body(),
      );

  Widget _body() {
    final evs = _events;
    final day = [if (evs != null) for (final e in evs) if (e.onDay(_selected)) e];
    final allDay = day.where((e) => e.allDay).toList();
    final timed = day.where((e) => !e.allDay).toList()
      ..sort((a, b) => (a.start ?? _selected).compareTo(b.start ?? _selected));
    final now = widget.now();
    final next = _selected == _today
        ? timed.cast<CalEvent?>().firstWhere((e) => !e!.start!.isBefore(now), orElse: () => null)
        : null;
    return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 110), children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(4, 6, 4, 14),
        child: Text.rich(
            TextSpan(children: [
              TextSpan(text: _monthNames[_selected.month - 1], style: condensed(40, weight: FontWeight.w200, color: cream)),
              TextSpan(text: ' ${_selected.year}', style: condensed(40, color: cream)),
            ]),
            maxLines: 1,
            overflow: TextOverflow.ellipsis),
      ),
      _nav(),
      const SizedBox(height: 10),
      _month ? _grid(evs) : _strip(evs),
      const SizedBox(height: 18),
      if (evs == null)
        const Text('Unavailable', style: TextStyle(color: mutedText))
      else ...[
        for (final e in allDay) _banner(e),
        if (_selected == _today && day.isNotEmpty)
          Padding(
              padding: const EdgeInsets.only(top: 4, bottom: 10),
              child: Row(children: [
                Container(width: 8, height: 8, decoration: const BoxDecoration(shape: BoxShape.circle, color: amber)),
                const SizedBox(width: 8),
                Text('Now · ${hhmm(widget.now())}', style: const TextStyle(color: amber, fontSize: 12)),
                const SizedBox(width: 8),
                Expanded(child: Container(height: 1, color: amber.withValues(alpha: 0.4))),
              ])),
        if (day.isEmpty) _empty() else for (final e in timed) _tile(e, e == next),
      ],
    ]);
  }

  Widget _nav() => Wrap(crossAxisAlignment: WrapCrossAlignment.center, spacing: 4, children: [
        IconButton(key: const ValueKey('prev'), tooltip: _month ? 'Previous month' : 'Previous week', icon: const Icon(Icons.chevron_left, color: cream), onPressed: () => _step(-1)),
        IconButton(key: const ValueKey('next'), tooltip: _month ? 'Next month' : 'Next week', icon: const Icon(Icons.chevron_right, color: cream), onPressed: () => _step(1)),
        TextButton(onPressed: () => _go(_today), child: const Text('Today', style: TextStyle(color: amber))),
        SegmentedButton<bool>(
          showSelectedIcon: false,
          segments: const [ButtonSegment(value: false, label: Text('Week')), ButtonSegment(value: true, label: Text('Month'))],
          selected: {_month},
          onSelectionChanged: (v) {
            _month = v.first;
            _load();
          },
        ),
      ]);

  Widget _grid(List<CalEvent>? evs) => Column(children: [
        Row(children: [
          for (final n in _dayInitials)
            Expanded(child: Center(child: Text(n, style: TextStyle(fontSize: 11, color: cream.withValues(alpha: 0.6))))),
        ]),
        const SizedBox(height: 6),
        for (var w = 0; w < 6; w++)
          Row(children: [
            for (var i = 0; i < 7; i++) Expanded(child: _gridCell(_gridStart.add(Duration(days: w * 7 + i)), evs)),
          ]),
      ]);

  Widget _gridCell(DateTime d, List<CalEvent>? evs) {
    final sel = d == _selected, has = evs?.any((e) => e.onDay(d)) ?? false, inMonth = d.month == _selected.month;
    final fg = sel ? ink : cream.withValues(alpha: inMonth ? 1 : 0.35);
    return GestureDetector(
      key: ValueKey('cell-${d.month}-${d.day}'),
      onTap: () => setState(() => _selected = d),
      child: Container(
        height: 46,
        margin: const EdgeInsets.all(2),
        decoration: BoxDecoration(
            color: sel ? blush : (d == _today ? amber.withValues(alpha: 0.18) : null), borderRadius: BorderRadius.circular(14)),
        child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
          Text('${d.day}', style: TextStyle(fontSize: 15, color: fg)),
          const SizedBox(height: 3),
          Container(width: 4, height: 4, decoration: BoxDecoration(shape: BoxShape.circle, color: has ? (sel ? ink : amber) : Colors.transparent)),
        ]),
      ),
    );
  }

  static String _iso(DateTime d) {
    final o = d.timeZoneOffset, a = o.abs();
    String two(int n) => n.toString().padLeft(2, '0');
    return '${d.toIso8601String().split('.').first}${o.isNegative ? '-' : '+'}${two(a.inHours)}:${two(a.inMinutes % 60)}';
  }

  Future<void> _addEvent() async {
    final title = TextEditingController();
    var start = DateTime(_selected.year, _selected.month, _selected.day, 9);
    var mins = 60;
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => StatefulBuilder(
            builder: (ctx, set) => AlertDialog(
                  title: Text('New event · ${_ymd(_selected)}'),
                  content: Column(mainAxisSize: MainAxisSize.min, children: [
                    TextField(controller: title, autofocus: true, maxLength: 200, decoration: const InputDecoration(labelText: 'Title', counterText: '')),
                    ListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text('Starts ${hhmm(start)}'),
                        trailing: const Icon(Icons.schedule),
                        onTap: () async {
                          final t = await showTimePicker(context: ctx, initialTime: TimeOfDay.fromDateTime(start));
                          if (t != null) set(() => start = DateTime(start.year, start.month, start.day, t.hour, t.minute));
                        }),
                    DropdownButton<int>(
                        value: mins,
                        items: [for (final m in [15, 30, 60, 90, 120]) DropdownMenuItem(value: m, child: Text('$m min'))],
                        onChanged: (v) => set(() => mins = v ?? mins)),
                  ]),
                  actions: [
                    TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
                    FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Add')),
                  ],
                )));
    final name = title.text.trim();
    title.dispose();
    if (ok != true || name.isEmpty || !mounted) return;
    try {
      await widget.api.post('/calendar/events', {
        'summary': name,
        'start': _iso(start),
        'end': _iso(start.add(Duration(minutes: mins))),
      });
      await _load();
    } catch (_) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Couldn't add event")));
    }
  }

  Future<void> _delete(CalEvent e) async {
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
              title: Text('Delete "${e.summary}"?'),
              actions: [
                TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
                FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Delete')),
              ],
            ));
    if (ok != true || !mounted) return;
    try {
      await widget.api.delete('/calendar/events/${e.id}');
      await _load();
    } catch (_) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Couldn't delete event")));
    }
  }

  Widget _strip(List<CalEvent>? evs) => Row(children: [
        for (var i = 0; i < 7; i++) Expanded(child: _dayCell(_monday.add(Duration(days: i)), evs)),
      ]);

  Widget _dayCell(DateTime d, List<CalEvent>? evs) {
    final sel = d == _selected, has = evs?.any((e) => e.onDay(d)) ?? false;
    final fg = sel ? ink : cream;
    return GestureDetector(
      key: ValueKey('day-${d.day}'),
      onTap: () => setState(() => _selected = d),
      child: Container(
        height: 68,
        margin: const EdgeInsets.symmetric(horizontal: 3),
        decoration: BoxDecoration(color: sel ? blush : darkPill.withValues(alpha: 0.35), borderRadius: BorderRadius.circular(24)),
        child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
          Text(_dayInitials[d.weekday - 1], maxLines: 1, style: TextStyle(fontSize: 11, color: fg.withValues(alpha: 0.75))),
          const SizedBox(height: 3),
          Text('${d.day}', style: condensed(20, color: fg)),
          const SizedBox(height: 3),
          Container(
              width: 4,
              height: 4,
              decoration: BoxDecoration(shape: BoxShape.circle, color: has ? (sel ? ink : amber) : Colors.transparent)),
        ]),
      ),
    );
  }

  Widget _banner(CalEvent e) => Container(
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(color: amber.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(20)),
        child: Row(children: [
          const Icon(Icons.cake_outlined, size: 18, color: amberLight),
          const SizedBox(width: 10),
          Expanded(
              child: Text('All day · ${e.summary}',
                  maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: amberLight, fontSize: 14))),
        ]),
      );

  Widget _empty() => Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 26),
        decoration: BoxDecoration(
            color: darkPill.withValues(alpha: 0.35),
            borderRadius: BorderRadius.circular(28),
            border: Border.all(color: Colors.white.withValues(alpha: 0.06))),
        child: Column(children: [
          Text('Nothing scheduled', style: condensed(30, weight: FontWeight.w300, color: cream)),
          const SizedBox(height: 14),
          GestureDetector(
            onTap: widget.onAskJarvis,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              decoration: BoxDecoration(color: blush, borderRadius: BorderRadius.circular(20)),
              child: const Text('Ask Jarvis to schedule something', style: TextStyle(color: ink, fontSize: 13, fontWeight: FontWeight.w500)),
            ),
          ),
        ]),
      );

  Widget _tile(CalEvent e, bool next) {
    final open = _open.contains(e.id);
    final fg = next ? Colors.white : ink, sc = next ? Colors.white.withValues(alpha: 0.6) : ink.withValues(alpha: 0.6);
    final sub = [if (next) 'Next', ?e.location, ?e.duration].join(' · ');
    return GestureDetector(
      key: ValueKey('event-${e.id}'),
      onTap: () => setState(() => open ? _open.remove(e.id) : _open.add(e.id)),
      onLongPress: () => _delete(e),
      child: Container(
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
        decoration: BoxDecoration(color: next ? darkPill : blush, borderRadius: BorderRadius.circular(28)),
        child: DefaultTextStyle.merge(
          style: TextStyle(color: fg),
          child: Row(crossAxisAlignment: CrossAxisAlignment.baseline, textBaseline: TextBaseline.alphabetic, children: [
            SizedBox(width: 62, child: Text(e.start == null ? '' : hhmm(e.start!), style: condensed(22, color: fg))),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(e.summary,
                    maxLines: open ? 4 : 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w500, height: 1.3)),
                if (sub.isNotEmpty)
                  Padding(
                      padding: const EdgeInsets.only(top: 3),
                      child: Text(sub, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 13, color: sc))),
                if (open && e.location != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      const Icon(Icons.place_outlined, size: 16),
                      const SizedBox(width: 6),
                      Expanded(child: Text(e.location!, key: const ValueKey('location-row'), style: const TextStyle(fontSize: 13))),
                    ]),
                  ),
              ]),
            ),
          ]),
        ),
      ),
    );
  }
}
