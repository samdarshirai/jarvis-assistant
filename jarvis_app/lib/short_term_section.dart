import 'package:flutter/material.dart';

import 'api.dart';
import 'theme.dart';

List<Map<String, dynamic>> _maps(Object? l) => l is List ? [for (final e in l) if (e is Map<String, dynamic>) e] : [];

const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const _days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/// "until Fri 6 Nov" for an item's end, in local time.
String untilText(Object? iso) {
  final d = DateTime.tryParse('$iso')?.toLocal();
  return d == null ? '' : 'until ${_days[d.weekday - 1]} ${d.day} ${_months[d.month - 1]}';
}

/// "Right now": the short-term notes Jarvis is keeping (a trip, a guest, a promise), each ending on its own date.
/// Jarvis saves them without asking; here the user can change one, delete it, or keep it forever (it becomes a lasting
/// fact in the graph). Hidden when there are none.
class ShortTermSection extends StatefulWidget {
  const ShortTermSection({super.key, required this.api, this.onGraphChanged});
  final ApiClient api;
  final VoidCallback? onGraphChanged; // "keep forever" wrote a fact: the graph below must reload

  @override
  State<ShortTermSection> createState() => _ShortTermSectionState();
}

class _ShortTermSectionState extends State<ShortTermSection> {
  List<Map<String, dynamic>> _items = [];
  bool _open = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final items = _maps((await widget.api.get('/memory/items'))['items']);
      if (mounted) setState(() => _items = items);
    } catch (_) {} // the screen's own load reports connection problems
  }

  void _toast(String m) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  void _drop(int id) {
    if (mounted) setState(() => _items = [for (final i in _items) if (i['id'] != id) i]);
  }

  Future<void> _delete(Map<String, dynamic> item) async {
    final id = item['id'] as int;
    try {
      await widget.api.delete('/memory/items/$id');
      _drop(id);
    } on ApiError catch (e) {
      if (e.status == 404) {
        _drop(id);
      } else {
        _toast("Couldn't delete that");
      }
    } catch (_) {
      _toast("Couldn't delete that");
    }
  }

  Future<void> _keep(Map<String, dynamic> item) async {
    final id = item['id'] as int;
    try {
      await widget.api.post('/memory/items/$id/promote');
      _drop(id);
      widget.onGraphChanged?.call();
      _toast('Kept for good: ${item['text']}');
    } catch (_) {
      _toast("Couldn't keep that");
    }
  }

  Future<void> _edit(Map<String, dynamic> item) async {
    final id = item['id'] as int;
    final edits = await showModalBottomSheet<Map<String, dynamic>>(
      context: context,
      isScrollControlled: true,
      backgroundColor: blush,
      builder: (_) => _EditSheet(item: item),
    );
    if (edits == null) return;
    try {
      final r = await widget.api.patch('/memory/items/$id', edits);
      final updated = r['item'];
      if (mounted && updated is Map<String, dynamic>) {
        setState(() => _items = [for (final i in _items) i['id'] == id ? updated : i]);
      }
    } catch (_) {
      _toast("Couldn't save that");
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 4),
      child: Container(
        decoration: BoxDecoration(color: blush, borderRadius: BorderRadius.circular(24)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          InkWell(
            key: const Key('right-now-header'),
            borderRadius: BorderRadius.circular(24),
            onTap: () => setState(() => _open = !_open),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(18, 14, 12, 10),
              child: Row(children: [
                const Icon(Icons.push_pin_outlined, size: 18, color: ink),
                const SizedBox(width: 8),
                Expanded(
                  child: Text('Right now · ${_items.length}',
                      style: const TextStyle(color: ink, fontSize: 15, fontWeight: FontWeight.w600)),
                ),
                Icon(_open ? Icons.expand_less : Icons.expand_more, color: ink),
              ]),
            ),
          ),
          if (_open)
            for (final item in _items)
              ListTile(
                key: ValueKey('right-now-${item['id']}'),
                dense: true,
                title: Text('${item['text']}', style: const TextStyle(color: ink, fontSize: 15)),
                subtitle: Text(untilText(item['expires_at']), style: TextStyle(color: ink.withValues(alpha: 0.6))),
                trailing: PopupMenuButton<String>(
                  key: ValueKey('right-now-menu-${item['id']}'),
                  tooltip: 'More',
                  onSelected: (v) => switch (v) {
                    'keep' => _keep(item),
                    'edit' => _edit(item),
                    _ => _delete(item),
                  },
                  itemBuilder: (_) => const [
                    PopupMenuItem(value: 'keep', child: Text('Keep forever')),
                    PopupMenuItem(value: 'edit', child: Text('Edit')),
                    PopupMenuItem(value: 'delete', child: Text('Delete')),
                  ],
                ),
              ),
          if (_open)
            Padding(
              padding: const EdgeInsets.fromLTRB(18, 0, 18, 12),
              child: Text('Jarvis notes these as you mention them, and forgets each on its date.',
                  style: TextStyle(color: ink.withValues(alpha: 0.55), fontSize: 12)),
            ),
        ]),
      ),
    );
  }
}

class _EditSheet extends StatefulWidget {
  const _EditSheet({required this.item});
  final Map<String, dynamic> item;

  @override
  State<_EditSheet> createState() => _EditSheetState();
}

class _EditSheetState extends State<_EditSheet> {
  late final _text = TextEditingController(text: '${widget.item['text']}');
  late DateTime? _until = DateTime.tryParse('${widget.item['expires_at']}')?.toLocal();
  bool _changedDate = false;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _pick() async {
    final now = DateTime.now();
    final d = await showDatePicker(
      context: context,
      initialDate: _until ?? now,
      firstDate: now,
      lastDate: now.add(const Duration(days: 60)),
    );
    if (d == null) return;
    setState(() {
      _until = d;
      _changedDate = true;
    });
  }

  String _day(DateTime d) =>
      '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext context) => Padding(
        padding: EdgeInsets.fromLTRB(20, 20, 20, 20 + MediaQuery.viewInsetsOf(context).bottom),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          const Text('Edit note', style: TextStyle(color: ink, fontSize: 18, fontWeight: FontWeight.w600)),
          const SizedBox(height: 12),
          TextField(
            key: const Key('right-now-text'),
            controller: _text,
            maxLength: 300,
            style: const TextStyle(color: ink),
            decoration: const InputDecoration(labelText: 'Note'),
          ),
          TextButton.icon(
            key: const Key('right-now-date'),
            onPressed: _pick,
            icon: const Icon(Icons.event),
            label: Text(_until == null ? 'Pick an end date' : untilText(_until!.toIso8601String())),
          ),
          const SizedBox(height: 8),
          FilledButton(
            key: const Key('right-now-save'),
            onPressed: () {
              final t = _text.text.trim();
              final edits = <String, dynamic>{
                if (t.isNotEmpty && t != widget.item['text']) 'text': t,
                if (_changedDate && _until != null) 'until': _day(_until!),
              };
              Navigator.pop(context, edits.isEmpty ? null : edits);
            },
            child: const Text('Save'),
          ),
        ]),
      );
}
