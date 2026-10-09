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
  const TasksScreen({super.key, required this.api, this.now = DateTime.now});
  final ApiClient api;
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
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error
                ? ErrorRetry(onRetry: _load)
                : Column(children: [Expanded(child: _body()), _input()]),
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
    return ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 8), children: [
      Text('$left left today', style: condensed(36, color: cream)),
      const SizedBox(height: 12),
      Container(
        width: double.infinity,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(color: blush, borderRadius: BorderRadius.circular(32)),
        child: ts.isEmpty
            ? Text('Nothing pending.', style: TextStyle(color: ink.withValues(alpha: 0.6)))
            : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                for (final g in groups)
                  if (g.$2.isNotEmpty) ...[
                    Padding(
                        padding: const EdgeInsets.fromLTRB(4, 8, 4, 4),
                        child: Text(g.$1,
                            style: TextStyle(
                                fontSize: 12,
                                fontWeight: FontWeight.w700,
                                color: g.$1 == 'Overdue' ? danger : ink.withValues(alpha: 0.6)))),
                    for (final t in g.$2) _row(t),
                  ],
              ]),
      ),
    ]);
  }

  Widget _row(TaskRow t) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(children: [
          GestureDetector(
            key: ValueKey('check-${t.id}'),
            behavior: HitTestBehavior.opaque,
            onTap: () => _complete(t),
            child: Padding(
              padding: const EdgeInsets.all(6),
              child: Container(
                width: 20,
                height: 20,
                decoration: BoxDecoration(
                    color: t.done ? amber : null,
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: t.done ? amber : ink.withValues(alpha: 0.4), width: 1.5)),
                child: t.done ? const Icon(Icons.check, size: 14, color: amberInk) : null,
              ),
            ),
          ),
          const SizedBox(width: 6),
          Expanded(
              child: Text(t.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                      color: ink.withValues(alpha: t.done ? 0.45 : 1),
                      fontSize: 15,
                      fontWeight: FontWeight.w500,
                      decoration: t.done ? TextDecoration.lineThrough : null))),
        ]),
      );

  Widget _input() => Padding(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
        child: TextField(
          controller: _field,
          maxLength: 200,
          style: const TextStyle(color: cream),
          textInputAction: TextInputAction.done,
          onSubmitted: _add,
          decoration: InputDecoration(
            counterText: '',
            hintText: 'Add a task and press Enter',
            hintStyle: const TextStyle(color: mutedText),
            filled: true,
            fillColor: darkPill.withValues(alpha: 0.45),
            border: OutlineInputBorder(borderRadius: BorderRadius.circular(999), borderSide: BorderSide.none),
          ),
        ),
      );
}
