import 'package:flutter/material.dart';

import 'api.dart';
import 'calendar_screen.dart' show ScreenShell;
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
  Widget build(BuildContext context) =>
      ScreenShell(title: 'Inbox', onMic: onReplyByVoice, child: LoadView(load: () => api.get('/mail'), builder: _body));

  Widget _body(Map<String, dynamic> d) {
    final raw = d['items'];
    if (raw is! List) {
      return const Center(child: Text('Mail unavailable', style: TextStyle(color: cream)));
    }
    final items = [for (final e in raw) if (e is Map<String, dynamic>) e];
    if (items.isEmpty) {
      return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 24), children: [
        Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 30),
          decoration: BoxDecoration(color: blush, borderRadius: BorderRadius.circular(28)),
          child: Column(children: [
            const Icon(Icons.mark_email_read_outlined, size: 34, color: ink),
            const SizedBox(height: 6),
            Text('Inbox zero', style: condensed(30, weight: FontWeight.w300, color: ink)),
          ]),
        ),
      ]);
    }
    final count = d['count'] is int ? d['count'] as int : items.length;
    return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 110), children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(4, 6, 4, 14),
        child: Row(crossAxisAlignment: CrossAxisAlignment.baseline, textBaseline: TextBaseline.alphabetic, children: [
          Text('$count', style: condensed(48, color: cream)),
          Flexible(child: Text(' unread', maxLines: 1, overflow: TextOverflow.ellipsis, style: condensed(40, weight: FontWeight.w200, color: cream))),
        ]),
      ),
      for (final m in items) _MailRow(api: api, item: m, onReply: onReplyByVoice, openEmail: openEmail),
      if (d['more'] == true)
        TextButton(
            onPressed: () => openEmail(null),
            child: const Text('+ more in Gmail', style: TextStyle(color: mutedText, fontSize: 13))),
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
    const soft = Color(0xFFE5D8D2);
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: GestureDetector(
        onTap: () => setState(() => _open = !_open),
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          decoration: BoxDecoration(
              color: darkPill.withValues(alpha: 0.35),
              borderRadius: BorderRadius.circular(28),
              border: Border.all(color: Colors.white.withValues(alpha: 0.06))),
          child: DefaultTextStyle.merge(
            style: const TextStyle(color: soft),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Container(
                    width: 40,
                    height: 40,
                    alignment: Alignment.center,
                    decoration: const BoxDecoration(shape: BoxShape.circle, color: Color(0xFFC99585)),
                    child: Text(sender.characters.first.toUpperCase(),
                        style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600))),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Row(children: [
                      Expanded(
                          child: Text(sender,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(color: cream, fontSize: 15, fontWeight: FontWeight.w600))),
                      Text(_time(m['date']), style: const TextStyle(fontSize: 12, color: mutedText)),
                    ]),
                    const SizedBox(height: 2),
                    Text('${m['subject'] ?? ''}',
                        maxLines: _open ? null : 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13.5)),
                  ]),
                ),
              ]),
              if (_open)
                Padding(
                  padding: const EdgeInsets.only(left: 52, top: 12),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    if (id != null) _Body(api: widget.api, id: id),
                    const SizedBox(height: 12),
                    Wrap(spacing: 8, runSpacing: 8, children: [
                      GestureDetector(
                        onTap: widget.onReply,
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
                          decoration: BoxDecoration(color: amber, borderRadius: BorderRadius.circular(18)),
                          child: const Row(mainAxisSize: MainAxisSize.min, children: [
                            Icon(Icons.mic, size: 17, color: amberInk),
                            SizedBox(width: 6),
                            Flexible(child: Text('Reply by voice', style: TextStyle(color: amberInk, fontSize: 13, fontWeight: FontWeight.w500))),
                          ]),
                        ),
                      ),
                      GestureDetector(
                        onTap: () => widget.openEmail(id),
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
                          decoration: BoxDecoration(
                              borderRadius: BorderRadius.circular(18), border: Border.all(color: Colors.white.withValues(alpha: 0.15))),
                          child: const Text('Open in Gmail', style: TextStyle(color: cream, fontSize: 13, fontWeight: FontWeight.w500)),
                        ),
                      ),
                    ]),
                  ]),
                ),
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
          Text('${d['body'] ?? ''}', style: const TextStyle(fontSize: 14, height: 1.5)),
          if (d['truncated'] == true)
            const Padding(
                padding: EdgeInsets.only(top: 4),
                child: Text('Message truncated', style: TextStyle(fontSize: 12, fontStyle: FontStyle.italic, color: mutedText))),
        ]),
      );
}
