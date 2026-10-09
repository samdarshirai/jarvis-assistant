import 'package:flutter/material.dart';

import 'api.dart';
import 'mail_screen.dart' show LoadView;
import 'theme.dart';

String agoText(DateTime t, [DateTime? now]) {
  final d = (now ?? DateTime.now()).difference(t);
  if (d.inMinutes < 1) return 'just now';
  if (d.inHours < 1) return '${d.inMinutes}m ago';
  if (d.inDays < 1) return '${d.inHours}h ago';
  return '${d.inDays}d ago';
}

class NotesScreen extends StatelessWidget {
  const NotesScreen({super.key, required this.api, required this.onAskJarvis});
  final ApiClient api;
  final VoidCallback onAskJarvis;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Notes'), actions: [
          IconButton(tooltip: 'Ask Jarvis', icon: const Icon(Icons.mic, color: amber), onPressed: onAskJarvis),
        ]),
        body: CocoaBackground(child: SafeArea(child: LoadView(load: () => api.get('/notes'), builder: _body))),
      );

  Widget _body(Map<String, dynamic> d) {
    final raw = d['notes'];
    if (raw is! List) return const Center(child: Text('Notes unavailable', style: TextStyle(color: cream)));
    final notes = [for (final e in raw) if (e is Map<String, dynamic>) e];
    if (notes.isEmpty) {
      return Center(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
        const Text('No notes yet', style: TextStyle(color: cream, fontSize: 22)),
        const SizedBox(height: 12),
        FilledButton(onPressed: onAskJarvis, child: const Text('Dictate a note')),
      ]));
    }
    return ListView(padding: const EdgeInsets.all(16), children: [
      const Text('Your notes', style: TextStyle(color: cream, fontSize: 26, fontWeight: FontWeight.w700)),
      const SizedBox(height: 12),
      for (var i = 0; i < notes.length; i++) _tile(notes[i], i % 3),
    ]);
  }

  Widget _tile(Map<String, dynamic> n, int style) {
    final u = n['updated_at'] is String ? DateTime.tryParse(n['updated_at'] as String)?.toLocal() : null;
    final fg = style == 2 ? cream : ink;
    return Builder(
      builder: (context) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: GestureDetector(
          onTap: () => _show(context, n),
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              gradient: style == 0 ? amberGradient : null,
              color: style == 1 ? blush : (style == 2 ? darkPill.withValues(alpha: 0.5) : null),
              borderRadius: BorderRadius.circular(28),
            ),
            child: DefaultTextStyle.merge(
              style: TextStyle(color: fg),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('${n['title'] ?? ''}',
                    maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
                const SizedBox(height: 4),
                Text('${n['snippet'] ?? ''}', maxLines: 2, overflow: TextOverflow.ellipsis),
                if (u != null) Padding(padding: const EdgeInsets.only(top: 6), child: Text(agoText(u), style: const TextStyle(fontSize: 12))),
              ]),
            ),
          ),
        ),
      ),
    );
  }

  void _show(BuildContext context, Map<String, dynamic> n) => showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        backgroundColor: blush,
        builder: (_) => Padding(
          padding: const EdgeInsets.all(20),
          child: DefaultTextStyle.merge(
            style: const TextStyle(color: ink),
            child: LoadView(
              load: () => api.get('/notes/${n['id']}'),
              builder: (d) => SingleChildScrollView(child: Text('${d['body'] ?? ''}')),
            ),
          ),
        ),
      );
}
