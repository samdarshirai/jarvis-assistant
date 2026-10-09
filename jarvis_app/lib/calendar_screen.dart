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
  const ScreenShell({super.key, required this.title, required this.child, this.trailing});
  final String title;
  final Widget child;
  final Widget? trailing;

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
                          style: const TextStyle(color: cream, fontSize: 18, fontWeight: FontWeight.w600))),
                  SizedBox(width: 46, child: trailing),
                ]),
              ),
              Expanded(child: child),
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
  DateTime get _monday => _today.subtract(Duration(days: _today.weekday - 1));

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
      final j = await widget.api.get('/calendar', {'from': _ymd(_monday), 'days': '7'});
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
        trailing: RoundButton(icon: Icons.add, tooltip: 'Add event', filled: true, onTap: widget.onAskJarvis),
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
    return ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 24), children: [
      Text('${_monthNames[_selected.month - 1]} ${_selected.year}',
          maxLines: 1, overflow: TextOverflow.ellipsis, style: condensed(44, color: cream)),
      const SizedBox(height: 14),
      _strip(evs),
      const SizedBox(height: 16),
      if (evs == null)
        const Text('Unavailable', style: TextStyle(color: mutedText))
      else ...[
        for (final e in allDay) _banner(e),
        if (_selected == _today)
          Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text('Now · ${hhmm(widget.now())}', style: const TextStyle(color: amber, fontWeight: FontWeight.w600))),
        if (day.isEmpty) _empty() else for (final e in timed) _tile(e),
      ],
    ]);
  }

  Widget _strip(List<CalEvent>? evs) => Row(children: [
        for (var i = 0; i < 7; i++) Expanded(child: _dayCell(_monday.add(Duration(days: i)), evs)),
      ]);

  Widget _dayCell(DateTime d, List<CalEvent>? evs) {
    final sel = d == _selected, has = evs?.any((e) => e.onDay(d)) ?? false;
    return GestureDetector(
      key: ValueKey('day-${d.day}'),
      onTap: () => setState(() => _selected = d),
      child: Container(
        margin: const EdgeInsets.symmetric(horizontal: 2),
        padding: const EdgeInsets.symmetric(vertical: 8),
        decoration: BoxDecoration(
            color: sel ? amber : darkPill.withValues(alpha: 0.35), borderRadius: BorderRadius.circular(18)),
        child: Column(children: [
          Text(_dayInitials[d.weekday - 1],
              maxLines: 1, style: TextStyle(fontSize: 10, color: sel ? amberInk : mutedText)),
          const SizedBox(height: 4),
          Text('${d.day}', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700, color: sel ? amberInk : cream)),
          const SizedBox(height: 4),
          Container(
              width: 5,
              height: 5,
              decoration: BoxDecoration(shape: BoxShape.circle, color: has ? (sel ? amberInk : amber) : Colors.transparent)),
        ]),
      ),
    );
  }

  Widget _banner(CalEvent e) => Container(
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        decoration: BoxDecoration(color: amber.withValues(alpha: 0.18), borderRadius: BorderRadius.circular(18)),
        child: Row(children: [
          const Icon(Icons.wb_sunny_outlined, size: 18, color: amber),
          const SizedBox(width: 10),
          Expanded(child: Text(e.summary, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: cream, fontWeight: FontWeight.w600))),
        ]),
      );

  Widget _empty() => Padding(
        padding: const EdgeInsets.symmetric(vertical: 24),
        child: Column(children: [
          const Text('Nothing scheduled', style: TextStyle(color: cream, fontSize: 17)),
          const SizedBox(height: 12),
          FilledButton.tonal(onPressed: widget.onAskJarvis, child: const Text('Ask Jarvis to schedule something')),
        ]),
      );

  Widget _tile(CalEvent e) {
    final open = _open.contains(e.id);
    final sub = [?e.location, ?e.duration].join(' · ');
    return GestureDetector(
      key: ValueKey('event-${e.id}'),
      onTap: () => setState(() => open ? _open.remove(e.id) : _open.add(e.id)),
      child: Container(
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(color: blush, borderRadius: BorderRadius.circular(24)),
        child: DefaultTextStyle.merge(
          style: const TextStyle(color: ink),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(e.start == null ? '' : hhmm(e.start!), style: condensed(20, color: ink)),
            const SizedBox(height: 4),
            Text(e.summary, maxLines: open ? 4 : 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            if (sub.isNotEmpty)
              Text(sub, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12, color: ink.withValues(alpha: 0.6))),
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
      ),
    );
  }
}
