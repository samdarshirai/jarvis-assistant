import 'package:flutter/material.dart';

import 'api.dart';
import 'calendar_screen.dart';
import 'graph3d.dart';
import 'memory_graph.dart';
import 'theme.dart';

const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

String _date(Object? iso) {
  final d = DateTime.tryParse('$iso');
  return d == null ? '' : '${d.day} ${_months[d.month - 1]} ${d.year}';
}

List<Map<String, dynamic>> _maps(Object? l) => l is List ? [for (final e in l) if (e is Map<String, dynamic>) e] : [];

void _toast(BuildContext c, String m) => ScaffoldMessenger.of(c).showSnackBar(SnackBar(content: Text(m)));

/// What Jarvis knows: you first, then the people and places in your life, and what it was told to forget.
class MemoryScreen extends StatefulWidget {
  const MemoryScreen({super.key, required this.api, this.onAskJarvis, this.autoRotate = true});
  final ApiClient api;
  final VoidCallback? onAskJarvis;
  final bool autoRotate;

  @override
  State<MemoryScreen> createState() => _MemoryScreenState();
}

class _MemoryScreenState extends State<MemoryScreen> {
  List<Map<String, dynamic>> _people = [], _sups = [];
  bool _loading = true, _error = false, _showList = false;
  int? _selected;
  GraphScene? _scene;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = false;
    });
    try {
      // the user's own entity first, as "Me"; the rest keep the server's order
      final j = await widget.api.get('/graph/entities');
      final all = _maps(j['entities']);
      final p = [for (final e in all) if (e['kind'] == 'self') e, for (final e in all) if (e['kind'] != 'self') e];
      final rel = _maps(j['relations']); // older servers omit it
      final s = _maps((await widget.api.get('/graph/suppressions'))['suppressions']);
      if (mounted) {
        setState(() {
          _people = p;
          _scene = buildScene(p, rel);
          if (!p.any((e) => e['id'] == _selected)) _selected = null;
          _sups = s;
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

  Future<void> _lift(Map<String, dynamic> s) async {
    try {
      await widget.api.deleteWith('/graph/suppressions', body: {'key_type': s['key_type'], 'key_hmac': s['key_hmac']});
      if (mounted) setState(() => _sups = [for (final x in _sups) if (x != s) x]);
    } catch (_) {
      if (mounted) _toast(context, "Couldn't lift that");
    }
  }

  Future<void> _open(Map<String, dynamic> p) async {
    final me = p['kind'] == 'self';
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => _PersonDetail(api: widget.api, id: p['id'] as int, name: me ? 'Me' : '${p['name']}', self: me),
      ),
    );
    if (mounted) _load();
  }

  void _onSelect(int? id) {
    if (id != null && id == _selected) {
      _open(_people.firstWhere((e) => e['id'] == id));
    } else {
      setState(() => _selected = id);
    }
  }

  void _forgotten() => showModalBottomSheet<void>(
    context: context,
    backgroundColor: Colors.transparent,
    builder: (_) => StatefulBuilder(
      builder: (ctx, set) => ListView(
        padding: const EdgeInsets.all(16),
        shrinkWrap: true,
        children: [
          for (final s in _sups)
            _tile(
              ListTile(
                title: Text('${s['kind']}', style: const TextStyle(color: ink, fontSize: 15)),
                subtitle: Text(_date(s['created_at']), style: TextStyle(color: ink.withValues(alpha: 0.6))),
                trailing: TextButton(
                  key: ValueKey('lift-${s['key_hmac']}'),
                  onPressed: () async {
                    await _lift(s);
                    set(() {});
                  },
                  child: const Text('Lift'),
                ),
              ),
            ),
        ],
      ),
    ),
  );

  Widget _graph() {
    final sel = _selected == null ? null : _people.cast<Map<String, dynamic>?>().firstWhere((e) => e!['id'] == _selected, orElse: () => null);
    return Stack(children: [
      Positioned.fill(
        child: MemoryGraph(
          scene: _scene!,
          selectedId: _selected,
          onSelect: _onSelect,
          autoRotate: widget.autoRotate && !MediaQuery.disableAnimationsOf(context),
        ),
      ),
      if (_sups.isNotEmpty)
        Positioned(
          left: 16,
          top: 8,
          child: TextButton(key: const Key('forgotten-button'), onPressed: _forgotten, child: const Text('Forgotten')),
        ),
      if (sel != null)
        Positioned(
          left: 16,
          right: 16,
          bottom: 24,
          child: GestureDetector(
            key: const Key('open-selected'),
            onTap: () => _open(sel),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
              decoration: BoxDecoration(color: blush, borderRadius: BorderRadius.circular(24)),
              child: Text(
                '${sel['kind'] == 'self' ? 'Me' : sel['name']} · ${sel['fact_count']} facts',
                style: const TextStyle(color: ink, fontSize: 16),
              ),
            ),
          ),
        ),
    ]);
  }

  @override
  Widget build(BuildContext context) => ScreenShell(
    title: 'Memory',
    onMic: widget.onAskJarvis,
    trailing: RoundButton(
      icon: _showList ? Icons.bubble_chart_outlined : Icons.view_list_outlined,
      tooltip: _showList ? 'Graph view' : 'List view',
      onTap: () => setState(() => _showList = !_showList),
    ),
    child: _loading
        ? const Center(child: CircularProgressIndicator())
        : _error
        ? ErrorRetry(onRetry: _load)
        : _showList || _people.isEmpty
        ? _body()
        : _graph(),
  );

  Widget _body() => ListView(
    padding: const EdgeInsets.fromLTRB(16, 8, 16, 110),
    children: [
      if (_people.isEmpty)
        Padding(
          padding: const EdgeInsets.all(24),
          child: Text('Nothing remembered yet. Tell Jarvis "remember that…".', style: condensed(24, weight: FontWeight.w300, color: cream)),
        ),
      for (final p in _people)
        _tile(
          ListTile(
            title: Text(p['kind'] == 'self' ? 'Me' : '${p['name']}', style: const TextStyle(color: ink, fontSize: 16)),
            subtitle: Text('${p['kind']} · ${p['fact_count']} facts', style: TextStyle(color: ink.withValues(alpha: 0.6))),
            onTap: () async {
              await Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => _PersonDetail(
                      api: widget.api, id: p['id'] as int, name: p['kind'] == 'self' ? 'Me' : '${p['name']}', self: p['kind'] == 'self'),
                ),
              );
              if (mounted) _load();
            },
          ),
        ),
      if (_sups.isNotEmpty) ...[
        Padding(
          padding: const EdgeInsets.fromLTRB(8, 16, 8, 8),
          child: Text('Forgotten', style: condensed(20, weight: FontWeight.w300, color: cream)),
        ),
        for (final s in _sups)
          _tile(
            ListTile(
              title: Text('${s['kind']}', style: const TextStyle(color: ink, fontSize: 15)),
              subtitle: Text(_date(s['created_at']), style: TextStyle(color: ink.withValues(alpha: 0.6))),
              trailing: TextButton(
                key: ValueKey('lift-${s['key_hmac']}'),
                onPressed: () => _lift(s),
                child: const Text('Lift'),
              ),
            ),
          ),
      ],
    ],
  );
}

Widget _tile(Widget child) => Container(
  margin: const EdgeInsets.only(bottom: 8),
  decoration: BoxDecoration(color: blush, borderRadius: BorderRadius.circular(24)),
  child: Material(type: MaterialType.transparency, child: child),
);

class _PersonDetail extends StatefulWidget {
  const _PersonDetail({required this.api, required this.id, required this.name, this.self = false});
  final ApiClient api;
  final int id;
  final String name;
  final bool self; // the user's own entity: it cannot be forgotten

  @override
  State<_PersonDetail> createState() => _PersonDetailState();
}

class _PersonDetailState extends State<_PersonDetail> {
  List<Map<String, dynamic>> _facts = [], _history = [], _pending = [];
  bool _loading = true, _error = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = false;
    });
    try {
      final j = await widget.api.get('/graph/entities/${widget.id}');
      if (mounted) {
        setState(() {
          _facts = _maps(j['facts']);
          _history = _maps(j['history']);
          _pending = _maps(j['pending']);
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

  Future<void> _edit(Map<String, dynamic> f) async {
    final c = TextEditingController(text: '${f['value']}');
    final v = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('${f['predicate']}'),
        content: TextField(controller: c, autofocus: true),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(ctx, c.text.trim()), child: const Text('Save')),
        ],
      ),
    );
    if (v == null || v.isEmpty || v == '${f['value']}' || !mounted) return;
    try {
      final nf = await widget.api.patch('/graph/facts/${f['id']}', {'value': v});
      if (mounted) setState(() => _facts = [for (final x in _facts) x == f ? nf : x]);
    } catch (_) {
      if (mounted) _toast(context, "Couldn't save that");
    }
  }

  Future<void> _delete(Map<String, dynamic> f) async {
    try {
      await widget.api.delete('/graph/facts/${f['id']}');
      if (mounted) {
        setState(() {
          _facts = [for (final x in _facts) if (x != f) x];
          _history = [for (final x in _history) if (x != f) x];
        });
      }
    } catch (_) {
      if (mounted) _toast(context, "Couldn't delete that");
    }
  }

  Future<Map<String, dynamic>?> _preview(bool mentions) async {
    try {
      return await widget.api.get('/graph/entities/${widget.id}/forget-preview', {'mentions': '$mentions'});
    } catch (_) {
      if (mounted) _toast(context, "Couldn't load the preview");
      return null;
    }
  }

  Future<void> _forget() async {
    var pv = await _preview(true);
    if (pv == null || !mounted) return;
    var mentions = true;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, set) => AlertDialog(
          title: Text('Forget ${pv!['entity']}?'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('${pv!['facts']} facts and ${pv!['redacted_excerpts']} excerpts will be erased, '
                    '${pv!['mentions_deleted']} mentions deleted.'),
                for (final m in _maps(pv!['mention_facts']))
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text('${m['entity']} — ${m['predicate']}: ${m['value']}', style: const TextStyle(fontSize: 13)),
                  ),
                if (pv!['mention_facts_more'] is int)
                  Text('and ${pv!['mention_facts_more']} more', style: const TextStyle(fontSize: 13)),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  value: mentions,
                  onChanged: (v) async {
                    final want = v ?? true;
                    final next = await _preview(want); // the counts and the list depend on it
                    if (next != null) set(() => (mentions, pv) = (want, next));
                  },
                  title: const Text('Also delete mentions'),
                ),
                const Text('Chat history, notes and the activity log are not erased'),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
            TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Forget')),
          ],
        ),
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await widget.api.deleteWith('/graph/entities/${widget.id}', query: {'mentions': '$mentions'});
      if (mounted) Navigator.of(context).pop();
    } catch (_) {
      if (mounted) _toast(context, "Couldn't forget that");
    }
  }

  Widget _fact(Map<String, dynamic> f, {bool editable = false, bool deletable = false}) => _tile(
    ListTile(
      title: Text('${f['predicate']}: ${f['value']}', style: const TextStyle(color: ink, fontSize: 15)),
      subtitle: Text(
        '${f['source']} · ${_date(f['observed_at'])}',
        style: TextStyle(color: ink.withValues(alpha: 0.6)),
      ),
      trailing: editable
          ? Row(mainAxisSize: MainAxisSize.min, children: [
              IconButton(key: ValueKey('edit-${f['id']}'), tooltip: 'Edit', icon: const Icon(Icons.edit_outlined), onPressed: () => _edit(f)),
              IconButton(key: ValueKey('delete-${f['id']}'), tooltip: 'Delete', icon: const Icon(Icons.delete_outline), onPressed: () => _delete(f)),
            ])
          : deletable
          ? IconButton(key: ValueKey('delete-${f['id']}'), tooltip: 'Delete', icon: const Icon(Icons.delete_outline), onPressed: () => _delete(f))
          : null,
    ),
  );

  Widget _head(String t) => Padding(
    padding: const EdgeInsets.fromLTRB(8, 16, 8, 8),
    child: Text(t, style: condensed(20, weight: FontWeight.w300, color: cream)),
  );

  @override
  Widget build(BuildContext context) => ScreenShell(
    title: widget.name,
    child: _loading
        ? const Center(child: CircularProgressIndicator())
        : _error
        ? ErrorRetry(onRetry: _load)
        : ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 110),
            children: [
              for (final f in _facts) _fact(f, editable: true),
              if (_history.isNotEmpty) ...[_head('History'), for (final f in _history) _fact(f, deletable: true)],
              if (_pending.isNotEmpty) ...[_head('Pending'), for (final f in _pending) _fact(f)],
              if (!widget.self) ...[
                const SizedBox(height: 16),
                FilledButton.tonal(onPressed: _forget, child: const Text('Forget person')),
              ],
            ],
          ),
  );
}
