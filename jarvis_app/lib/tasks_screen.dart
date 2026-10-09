import 'package:flutter/material.dart';

import 'api.dart';
import 'calendar_screen.dart';
import 'theme.dart';

class TaskRow {
  TaskRow({required this.id, required this.title, this.due, required this.overdue, this.done = false});
  final String id, title;
  final String? due;
  final bool overdue;
  bool done;

  static TaskRow from(Map<String, dynamic> j) => TaskRow(
      id: '${j['id']}',
      title: j['title'] is String ? j['title'] as String : '',
      due: j['due'] is String ? j['due'] as String : null,
      overdue: j['overdue'] == true);
}

class TasksScreen extends StatefulWidget {
  const TasksScreen({super.key, required this.api, this.onAskJarvis, this.now = DateTime.now});
  final ApiClient api;
  final VoidCallback? onAskJarvis;
  final DateTime Function() now;

  @override
  State<TasksScreen> createState() => _TasksScreenState();
}

class _TasksScreenState extends State<TasksScreen> {
  List<TaskRow>? _tasks;
  bool _loading = true, _error = false;
  final _field = TextEditingController();

  String get _today {
    final n = widget.now();
    return '${n.year}-${n.month.toString().padLeft(2, '0')}-${n.day.toString().padLeft(2, '0')}';
  }

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _field.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = false;
    });
    try {
      final l = (await widget.api.get('/tasks'))['tasks'];
      final t = l is List ? [for (final e in l) if (e is Map<String, dynamic>) TaskRow.from(e)] : null;
      if (mounted) {
        setState(() {
          _tasks = t;
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

  void _snack(String m) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));

  Future<void> _complete(TaskRow t) async {
    if (t.done) return; // no un-complete endpoint
    setState(() => t.done = true);
    try {
      await widget.api.post('/tasks/${t.id}/complete');
    } catch (_) {
      if (!mounted) return;
      setState(() => t.done = false);
      _snack("Couldn't update");
    }
  }

  Future<void> _add(String raw) async {
    var title = raw.trim();
    if (title.isEmpty) return;
    if (title.length > 200) title = title.substring(0, 200);
    _field.clear();
    try {
      final j = await widget.api.post('/tasks', {'title': title});
      final t = j['task'];
      if (!mounted) return;
      if (t is Map<String, dynamic>) setState(() => _tasks = [...?_tasks, TaskRow.from(t)]);
    } catch (_) {
      if (mounted) _snack("Couldn't add task");
    }
  }

  @override
  Widget build(BuildContext context) => ScreenShell(
        title: 'Tasks',
        onMic: widget.onAskJarvis,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error
                ? ErrorRetry(onRetry: _load)
                : _body(),
      );

  Widget _body() {
    final ts = _tasks;
    if (ts == null) return const Center(child: Text('Unavailable', style: TextStyle(color: mutedText)));
    final today = _today, open = ts.where((t) => !t.done);
    bool isOver(TaskRow t) => t.overdue || (t.due != null && t.due!.compareTo(today) < 0);
    final groups = <(String, List<TaskRow>)>[
      ('Overdue', open.where(isOver).toList()),
      ('Today', open.where((t) => !isOver(t) && t.due == today).toList()),
      ('Upcoming', open.where((t) => !isOver(t) && t.due != null && t.due!.compareTo(today) > 0).toList()),
      ('No date', open.where((t) => !isOver(t) && t.due == null).toList()),
      ('Done', ts.where((t) => t.done).toList()),
    ];
    final left = groups[0].$2.length + groups[1].$2.length;
    const orange = Color(0xFFC2410C);
    return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 110), children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(4, 6, 4, 14),
        child: Text.rich(TextSpan(children: [
          TextSpan(text: '$left', style: condensed(48, color: cream)),
          TextSpan(text: ' left today', style: condensed(40, weight: FontWeight.w200, color: cream)),
        ])),
      ),
      Container(
        width: double.infinity,
        padding: const EdgeInsets.fromLTRB(18, 18, 18, 10),
        decoration: BoxDecoration(color: blush, borderRadius: BorderRadius.circular(32)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (ts.isEmpty)
            Padding(
                padding: const EdgeInsets.only(top: 8, bottom: 12),
                child: Text('All clear. Nothing pending.', style: condensed(28, weight: FontWeight.w300, color: ink))),
          for (final g in groups)
            if (g.$2.isNotEmpty) ...[
              Padding(
                  padding: const EdgeInsets.only(top: 6, bottom: 4),
                  child: Text(g.$1.toUpperCase(),
                      style: TextStyle(
                          fontSize: 12,
                          letterSpacing: 0.72,
                          color: g.$1 == 'Overdue' ? orange : ink.withValues(alpha: g.$1 == 'Done' ? 0.4 : 0.55)))),
              for (final t in g.$2) _row(t, orange),
            ],
          _input(),
        ]),
      ),
    ]);
  }

  Widget _row(TaskRow t, Color orange) => Container(
        decoration: BoxDecoration(border: Border(bottom: BorderSide(color: ink.withValues(alpha: 0.06)))),
        padding: const EdgeInsets.symmetric(vertical: 10),
        child: Row(children: [
          GestureDetector(
            key: ValueKey('check-${t.id}'),
            behavior: HitTestBehavior.opaque,
            onTap: () => _complete(t),
            child: Container(
              width: 22,
              height: 22,
              decoration: BoxDecoration(color: t.done ? darkPill : const Color(0xFFEBCFC4), borderRadius: BorderRadius.circular(7)),
              child: t.done ? const Icon(Icons.check, size: 16, color: Colors.white) : null,
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
              child: Text(t.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                      color: ink.withValues(alpha: t.done ? 0.45 : 1),
                      fontSize: 15,
                      decoration: t.done ? TextDecoration.lineThrough : null))),
          if (t.due != null) ...[
            const SizedBox(width: 12),
            Text(t.due!, style: TextStyle(fontSize: 12, color: t.overdue && !t.done ? orange : ink.withValues(alpha: 0.5))),
          ],
        ]),
      );

  Widget _input() => Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Row(children: [
          Icon(Icons.add, size: 22, color: ink.withValues(alpha: 0.5)),
          const SizedBox(width: 10),
          Expanded(
            child: TextField(
              controller: _field,
              maxLength: 200,
              style: const TextStyle(color: ink, fontSize: 15),
              textInputAction: TextInputAction.done,
              onSubmitted: _add,
              decoration: InputDecoration(
                counterText: '',
                hintText: 'Add a task and press Enter',
                hintStyle: TextStyle(color: ink.withValues(alpha: 0.4)),
                border: InputBorder.none,
                enabledBorder: InputBorder.none,
                focusedBorder: InputBorder.none,
                filled: false,
              ),
            ),
          ),
        ]),
      );
}
