import 'package:flutter/material.dart';

import 'api.dart';
import 'confirm_card.dart';
import 'theme.dart';

const _sheet = Color(0xFFFFF8F4), _userBubble = Color(0xFFFCE0D5), _jarvisBubble = Color(0xFFF2E8E3);

class _Msg {
  _Msg(this.text, {this.mine = false, this.card});
  final String text;
  final bool mine;
  final _Card? card;
}

class _Card {
  _Card(this.id, this.summary, this.untrusted);
  final String id, summary;
  final bool untrusted;
  bool? decision; // null = pending
}

/// Text chat with Jarvis: POST /chat, confirm cards inline, phone actions handed back to the caller.
class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key, required this.api, required this.onVoice, required this.runPhoneActions});
  final ApiClient api;
  final VoidCallback onVoice;
  final Future<void> Function(List<Map<String, dynamic>> actions) runPhoneActions;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _text = TextEditingController();
  final _scroll = ScrollController();
  final _msgs = <_Msg>[];
  bool _busy = false;

  @override
  void dispose() {
    _text.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _send() {
    final t = _text.text.trim();
    if (t.isEmpty || _busy) return;
    _text.clear();
    setState(() => _msgs.add(_Msg(t, mine: true)));
    _call('/chat', {'text': t});
  }

  void _decide(_Card c, bool yes) {
    if (_busy || c.decision != null) return;
    setState(() => c.decision = yes);
    _call('/chat/confirm', {'interrupt_id': c.id, 'decision': yes ? 'yes' : 'no'});
  }

  Future<void> _call(String path, Map<String, dynamic> body) async {
    setState(() => _busy = true);
    try {
      final r = await widget.api.post(path, body);
      if (!mounted) return;
      _apply(r);
      final acts = r['client_actions'];
      if (acts is List && acts.isNotEmpty) {
        await widget.runPhoneActions(acts.whereType<Map>().map((m) => Map<String, dynamic>.from(m)).toList());
      }
    } catch (_) {
      if (mounted) setState(() => _msgs.add(_Msg("Couldn't reach Jarvis.")));
    } finally {
      if (mounted) {
        setState(() => _busy = false);
        _toBottom();
      }
    }
  }

  void _apply(Map<String, dynamic> r) {
    final reply = r['reply'], card = r['card'];
    setState(() {
      if (reply is String && reply.trim().isNotEmpty) _msgs.add(_Msg(reply));
      if (card is Map) {
        _msgs.add(_Msg('', card: _Card('${card['interrupt_id']}', '${card['summary']}', card['after_untrusted'] == true)));
      }
    });
  }

  void _toBottom() => WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
      });

  @override
  Widget build(BuildContext context) => Scaffold(
        body: CocoaBackground(
          child: SafeArea(
            child: Column(children: [
              Row(children: [
                IconButton(
                    icon: const Icon(Icons.arrow_back, color: cream),
                    tooltip: 'Back',
                    onPressed: () => Navigator.of(context).maybePop()),
                const Expanded(
                  child: Text('Talk with Jarvis',
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600, color: cream)),
                ),
              ]),
              Expanded(
                child: Container(
                  width: double.infinity,
                  decoration: const BoxDecoration(
                      color: _sheet, borderRadius: BorderRadius.vertical(top: Radius.circular(32))),
                  child: Column(children: [
                    Container(
                        width: 40,
                        height: 4,
                        margin: const EdgeInsets.only(top: 10, bottom: 8),
                        decoration: BoxDecoration(color: const Color(0xFFD9C9C2), borderRadius: BorderRadius.circular(2))),
                    Expanded(child: _thread()),
                    _input(),
                  ]),
                ),
              ),
            ]),
          ),
        ),
      );

  Widget _thread() => ListView(
        controller: _scroll,
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
        children: [
          Center(
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(color: _jarvisBubble, borderRadius: BorderRadius.circular(12)),
              child: const Text('Today', style: TextStyle(fontSize: 12, color: Color(0xFF7A6560))),
            ),
          ),
          const SizedBox(height: 12),
          if (_msgs.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 24),
              child: Text('Type a request, or tap the mic.',
                  textAlign: TextAlign.center, style: TextStyle(color: Color(0xFF7A6560))),
            ),
          for (final m in _msgs) m.card != null ? _cardView(m.card!) : _bubble(m.text, m.mine),
          if (_busy) _bubble('Thinking…', false),
        ],
      );

  Widget _bubble(String text, bool mine) => Align(
        alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
        child: Container(
          margin: const EdgeInsets.only(bottom: 8),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          constraints: BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * 0.8),
          decoration: BoxDecoration(
              color: mine ? _userBubble : _jarvisBubble, borderRadius: BorderRadius.circular(18)),
          child: Text(text, style: const TextStyle(color: ink)),
        ),
      );

  Widget _cardView(_Card c) {
    if (c.decision == null) {
      return ConfirmCard(
          summary: c.summary,
          afterUntrusted: c.untrusted,
          onConfirm: () => _decide(c, true),
          onCancel: () => _decide(c, false));
    }
    final yes = c.decision!;
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(color: darkPill, borderRadius: BorderRadius.circular(18)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(yes ? Icons.check_circle : Icons.cancel, size: 18, color: yes ? amber : danger),
          const SizedBox(width: 8),
          Flexible(child: Text(yes ? 'Done' : 'Cancelled', style: const TextStyle(color: cream))),
        ]),
      ),
    );
  }

  Widget _input() => Padding(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
        child: Row(children: [
          Expanded(
            child: Container(
              padding: const EdgeInsets.only(left: 16),
              decoration: BoxDecoration(color: _jarvisBubble, borderRadius: BorderRadius.circular(28)),
              child: Row(children: [
                Expanded(
                  child: TextField(
                    controller: _text,
                    maxLength: 2000,
                    buildCounter: (_, {required currentLength, required isFocused, maxLength}) => null,
                    style: const TextStyle(color: ink),
                    textInputAction: TextInputAction.send,
                    onSubmitted: (_) => _send(),
                    decoration: const InputDecoration(
                        hintText: 'Write here…', border: InputBorder.none, isDense: true, hintStyle: TextStyle(color: Color(0xFF7A6560))),
                  ),
                ),
                IconButton(
                    icon: const Icon(Icons.send, color: ink),
                    tooltip: 'Send',
                    onPressed: _busy ? null : _send),
              ]),
            ),
          ),
          const SizedBox(width: 8),
          Tooltip(
            message: 'Talk',
            child: GestureDetector(
              onTap: widget.onVoice,
              child: Container(
                width: 48,
                height: 48,
                decoration: const BoxDecoration(shape: BoxShape.circle, gradient: amberGradient),
                child: const Icon(Icons.mic, color: amberInk),
              ),
            ),
          ),
        ]),
      );
}
