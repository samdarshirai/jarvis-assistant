import 'package:flutter/material.dart';

import 'api.dart';
import 'calendar_screen.dart' show RoundButton, ScreenShell;
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
  Widget build(BuildContext context) => ScreenShell(
        title: 'Notes',
        trailing: RoundButton(icon: Icons.mic, tooltip: 'Ask Jarvis', filled: true, onTap: onAskJarvis),
        child: LoadView(load: () => api.get('/notes'), builder: _body),
      );

  Widget _body(Map<String, dynamic> d) {
    final raw = d['notes'];
    if (raw is! List) return const Center(child: Text('Notes unavailable', style: TextStyle(color: cream)));
    final notes = [for (final e in raw) if (e is Map<String, dynamic>) e];
    return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 110), children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(4, 6, 4, 14),
        child: Text.rich(TextSpan(children: [
          TextSpan(text: 'Your ', style: condensed(40, weight: FontWeight.w200, color: cream)),
          TextSpan(text: 'notes', style: condensed(40, color: cream)),
        ])),
      ),
      if (notes.isEmpty)
        Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 28),
          decoration: BoxDecoration(
              color: darkPill.withValues(alpha: 0.35),
              borderRadius: BorderRadius.circular(28),
              border: Border.all(color: Colors.white.withValues(alpha: 0.06))),
          child: Column(children: [
            Text('No notes yet', style: condensed(30, weight: FontWeight.w300, color: cream)),
            const SizedBox(height: 14),
            GestureDetector(
              onTap: onAskJarvis,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                decoration: BoxDecoration(color: amber, borderRadius: BorderRadius.circular(20)),
                child: const Text('Dictate a note', style: TextStyle(color: amberInk, fontSize: 13, fontWeight: FontWeight.w500)),
              ),
            ),
          ]),
        )
      else
        for (var i = 0; i < notes.length; i += 2)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: IntrinsicHeight(
              child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                Expanded(child: _tile(notes[i], i % 3)),
                const SizedBox(width: 10),
                Expanded(child: i + 1 < notes.length ? _tile(notes[i + 1], (i + 1) % 3) : const SizedBox()),
              ]),
            ),
          ),
    ]);
  }

  Widget _tile(Map<String, dynamic> n, int style) {
    final u = n['updated_at'] is String ? DateTime.tryParse(n['updated_at'] as String)?.toLocal() : null;
    final fg = style == 2 ? cream : (style == 0 ? amberInk : ink);
    return Builder(
      builder: (context) => GestureDetector(
        onTap: () => _show(context, n),
        child: Container(
          constraints: const BoxConstraints(minHeight: 140),
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            gradient: style == 0 ? amberGradient : null,
            color: style == 1 ? blush : (style == 2 ? darkPill.withValues(alpha: 0.45) : null),
            borderRadius: BorderRadius.circular(28),
          ),
          child: DefaultTextStyle.merge(
            style: TextStyle(color: fg),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${n['title'] ?? ''}',
                  maxLines: 3, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600, height: 1.3)),
              const SizedBox(height: 6),
              Opacity(
                  opacity: 0.8,
                  child: Text('${n['snippet'] ?? ''}',
                      maxLines: 4, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13, height: 1.45))),
              if (u != null)
                Padding(
                    padding: const EdgeInsets.only(top: 10),
                    child: Opacity(opacity: 0.6, child: Text(agoText(u), style: const TextStyle(fontSize: 11.5)))),
            ]),
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
