import 'package:flutter/material.dart';

import 'api.dart';
import 'dashboard.dart' show senderName;
import 'phone.dart';
import 'theme.dart';

/// Loads once (and on Retry), showing a spinner, an error with Retry, or `builder(data)`.
class LoadView extends StatefulWidget {
  const LoadView({super.key, required this.load, required this.builder});
  final Future<Map<String, dynamic>> Function() load;
  final Widget Function(Map<String, dynamic> data) builder;

  @override
  State<LoadView> createState() => _LoadViewState();
}

class _LoadViewState extends State<LoadView> {
  late Future<Map<String, dynamic>> _f = widget.load();

  @override
  Widget build(BuildContext context) => FutureBuilder<Map<String, dynamic>>(
        future: _f,
        builder: (context, snap) {
          if (snap.hasError) {
            return Center(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Text("Couldn't load", style: TextStyle(color: cream)),
              TextButton(onPressed: () => setState(() { _f = widget.load()..ignore(); }), child: const Text('Retry')),
            ]));
          }
          if (!snap.hasData) return const Center(child: CircularProgressIndicator(color: amber));
          return widget.builder(snap.data!);
        },
      );
}

class MailScreen extends StatelessWidget {
  const MailScreen(
      {super.key, required this.api, required this.onReplyByVoice, this.openEmail = openEmailInGmail});
  final ApiClient api;
  final VoidCallback onReplyByVoice;
  final Future<void> Function(String? id) openEmail;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Inbox')),
        body: CocoaBackground(
          child: SafeArea(
            child: LoadView(load: () => api.get('/mail'), builder: _body),
          ),
        ),
      );

  Widget _body(Map<String, dynamic> d) {
    final raw = d['items'];
    if (raw is! List) {
      return const Center(child: Text('Mail unavailable', style: TextStyle(color: cream)));
    }
    final items = [for (final e in raw) if (e is Map<String, dynamic>) e];
    if (items.isEmpty) return const Center(child: Text('Inbox zero', style: TextStyle(color: cream, fontSize: 22)));
    final count = d['count'] is int ? d['count'] as int : items.length;
    return ListView(padding: const EdgeInsets.all(16), children: [
      Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
        Text('$count', style: condensed(64, color: amber)),
        const Text(' unread', style: TextStyle(color: cream, fontSize: 18)),
      ]),
      const SizedBox(height: 12),
      for (final m in items) _MailRow(api: api, item: m, onReply: onReplyByVoice, openEmail: openEmail),
      if (d['more'] == true)
        TextButton(onPressed: () => openEmail(null), child: const Text('+ more in Gmail')),
    ]);
  }
}

String _time(Object? date) {
  final t = date is String ? DateTime.tryParse(date)?.toLocal() : null;
  return t == null ? '' : '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}

class _MailRow extends StatefulWidget {
  const _MailRow({required this.api, required this.item, required this.onReply, required this.openEmail});
  final ApiClient api;
  final Map<String, dynamic> item;
  final VoidCallback onReply;
  final Future<void> Function(String? id) openEmail;

  @override
  State<_MailRow> createState() => _MailRowState();
}

class _MailRowState extends State<_MailRow> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final m = widget.item;
    final id = m['id'] is String ? m['id'] as String : null;
    final sender = senderName(m['from'] is String ? m['from'] as String : '');
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: GestureDetector(
        onTap: () => setState(() => _open = !_open),
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(color: blush, borderRadius: BorderRadius.circular(24)),
          child: DefaultTextStyle.merge(
            style: const TextStyle(color: ink),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                CircleAvatar(
                    radius: 18,
                    backgroundColor: amber,
                    child: Text(sender.characters.first.toUpperCase(), style: const TextStyle(color: amberInk))),
                const SizedBox(width: 10),
                Expanded(
                    child: Text(sender,
                        maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w700))),
                Text(_time(m['date']), style: const TextStyle(fontSize: 12)),
              ]),
              const SizedBox(height: 6),
              Text('${m['subject'] ?? ''}', maxLines: _open ? null : 2, overflow: TextOverflow.ellipsis),
              if (_open) ...[
                const SizedBox(height: 8),
                if (id != null) _Body(api: widget.api, id: id),
                const SizedBox(height: 8),
                Wrap(spacing: 8, children: [
                  FilledButton(onPressed: widget.onReply, child: const Text('Reply by voice')),
                  OutlinedButton(onPressed: () => widget.openEmail(id), child: const Text('Open in Gmail')),
                ]),
              ],
            ]),
          ),
        ),
      ),
    );
  }
}

class _Body extends StatelessWidget {
  const _Body({required this.api, required this.id});
  final ApiClient api;
  final String id;

  @override
  Widget build(BuildContext context) => LoadView(
        load: () => api.get('/mail/$id'),
        builder: (d) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${d['body'] ?? ''}'),
          if (d['truncated'] == true)
            const Padding(
                padding: EdgeInsets.only(top: 4),
                child: Text('Message truncated', style: TextStyle(fontSize: 12, fontStyle: FontStyle.italic))),
        ]),
      );
}
