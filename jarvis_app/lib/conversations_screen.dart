import 'package:flutter/material.dart';

import 'api.dart';
import 'calendar_screen.dart' show RoundButton;
import 'notes_screen.dart' show agoText;
import 'theme.dart';

/// Past chats: tap one to resume it; pops the resume payload for ChatScreen.
class ConversationsScreen extends StatefulWidget {
  const ConversationsScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<ConversationsScreen> createState() => _ConversationsScreenState();
}

class _ConversationsScreenState extends State<ConversationsScreen> {
  List<Map<String, dynamic>>? _rows;
  bool _error = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await widget.api.get('/conversations');
      if (!mounted) return;
      setState(
        () => _rows = (r['conversations'] as List? ?? const [])
            .whereType<Map>()
            .map((m) => Map<String, dynamic>.from(m))
            .toList(),
      );
    } catch (_) {
      if (mounted) setState(() => _error = true);
    }
  }

  Future<void> _open(int id) async {
    try {
      final r = await widget.api.post('/conversations/$id/resume');
      if (mounted) Navigator.of(context).pop(r);
    } on ApiError catch (e) {
      if (!mounted) return;
      final msg = e.status == 409
          ? 'Still in a voice session'
          : "Couldn't open that chat.";
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Couldn't open that chat.")));
      }
    }
  }

  IconData _icon(Object? ch) => switch (ch) {
    'voice' => Icons.mic,
    'telegram' => Icons.send,
    _ => Icons.chat_bubble_outline,
  };

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    return Scaffold(
      body: CocoaBackground(
        child: SafeArea(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                child: Row(
                  children: [
                    RoundButton(
                      icon: Icons.arrow_back,
                      tooltip: 'Back',
                      onTap: () => Navigator.of(context).maybePop(),
                    ),
                    const Expanded(
                      child: Text(
                        'Past chats',
                        textAlign: TextAlign.center,
                        style: TextStyle(fontSize: 16, color: cream),
                      ),
                    ),
                    const SizedBox(width: 46),
                  ],
                ),
              ),
              Expanded(
                child: Container(
                  width: double.infinity,
                  decoration: const BoxDecoration(
                    color: Color(0xFFFFF8F4),
                    borderRadius: BorderRadius.vertical(
                      top: Radius.circular(32),
                    ),
                  ),
                  child: Material(
                    type: MaterialType.transparency,
                    child: _error
                        ? const Center(child: Text("Couldn't load past chats."))
                        : rows == null
                        ? const Center(child: CircularProgressIndicator())
                        : rows.isEmpty
                        ? const Center(child: Text('No past chats yet.'))
                        : ListView(
                            padding: const EdgeInsets.only(top: 12),
                            children: [
                              for (final c in rows)
                                ListTile(
                                  leading: Icon(_icon(c['channel'])),
                                  title: Text(
                                    c['title'] is String &&
                                            (c['title'] as String).isNotEmpty
                                        ? c['title']
                                        : 'Untitled',
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                  subtitle: Text(_ago(c['last_at'])),
                                  onTap: () => _open(c['id'] as int),
                                ),
                            ],
                          ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  String _ago(Object? v) {
    final t = DateTime.tryParse('$v');
    return t == null ? '' : agoText(t.toUtc(), DateTime.now().toUtc());
  }
}
