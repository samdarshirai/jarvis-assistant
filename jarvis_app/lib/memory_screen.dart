import 'package:flutter/material.dart';

import 'api.dart';
import 'calendar_screen.dart';
import 'theme.dart';

/// What Jarvis remembers about the user; swipe-free delete via the trash icon.
class MemoryScreen extends StatefulWidget {
  const MemoryScreen({super.key, required this.api, this.onAskJarvis});
  final ApiClient api;
  final VoidCallback? onAskJarvis;

  @override
  State<MemoryScreen> createState() => _MemoryScreenState();
}

class _MemoryScreenState extends State<MemoryScreen> {
  List<({int id, String text})>? _rows;
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
      final l = (await widget.api.get('/memory'))['memories'];
      final rows = l is List
          ? [
              for (final e in l)
                if (e is Map<String, dynamic> && e['id'] is int)
                  (id: e['id'] as int, text: '${e['text']}'),
            ]
          : <({int id, String text})>[];
      if (mounted) {
        setState(() {
          _rows = rows;
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

  Future<void> _forget(int id) async {
    try {
      await widget.api.delete('/memory/$id');
      if (mounted) {
        setState(() => _rows = [for (final r in _rows ?? []) if (r.id != id) r]);
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Couldn't forget that")));
      }
    }
  }

  @override
  Widget build(BuildContext context) => ScreenShell(
    title: 'Memory',
    onMic: widget.onAskJarvis,
    child: _loading
        ? const Center(child: CircularProgressIndicator())
        : _error
        ? ErrorRetry(onRetry: _load)
        : _body(),
  );

  Widget _body() {
    final rows = _rows ?? const [];
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 110),
      children: [
        if (rows.isEmpty)
          Padding(
            padding: const EdgeInsets.all(24),
            child: Text(
              'Nothing remembered yet. Tell Jarvis "remember that…".',
              style: condensed(24, weight: FontWeight.w300, color: cream),
            ),
          ),
        for (final r in rows)
          Container(
            margin: const EdgeInsets.only(bottom: 8),
            padding: const EdgeInsets.fromLTRB(18, 8, 8, 8),
            decoration: BoxDecoration(
              color: blush,
              borderRadius: BorderRadius.circular(24),
            ),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    r.text,
                    style: const TextStyle(color: ink, fontSize: 15),
                  ),
                ),
                IconButton(
                  key: ValueKey('forget-${r.id}'),
                  tooltip: 'Forget',
                  icon: Icon(
                    Icons.delete_outline,
                    color: ink.withValues(alpha: 0.6),
                  ),
                  onPressed: () => _forget(r.id),
                ),
              ],
            ),
          ),
      ],
    );
  }
}
