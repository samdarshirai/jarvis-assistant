import 'package:flutter/material.dart';

import 'api.dart';
import 'learned.dart';
import 'theme.dart';

List<Map<String, dynamic>> _maps(Object? l) => l is List ? [for (final e in l) if (e is Map<String, dynamic>) e] : [];

String _clean(Object? v) => '${v ?? ''}'.trim();

const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

String _when(Object? iso) {
  final d = DateTime.tryParse('$iso')?.toLocal();
  return d == null ? '' : '${d.day} ${_months[d.month - 1]}';
}

/// One plain sentence per candidate, e.g. "Anna, your wife: works as a teacher".
String learnedSentence(Map<String, dynamic> c) {
  final name = _clean(c['subject']), rel = _clean(c['relation']), v = _clean(c['value']);
  final who = c['subject_kind'] == 'self' ? 'You' : (rel.isEmpty ? name : '$name, your $rel');
  final what = switch (c['predicate']) {
    'employer' => 'works at $v',
    'occupation' => 'works as $v',
    'city' => 'lives in $v',
    'birthday' => 'birthday is $v',
    'home' => 'home is $v',
    'office' => 'office is $v',
    'preferred_channel' => 'prefers $v',
    'friend_of' => 'is a friend of $v',
    'spouse_of' => 'is the spouse of $v',
    'partner_of' => 'is the partner of $v',
    'sibling_of' => 'is a sibling of $v',
    'colleague_of' => 'works with $v',
    'relative_of' => 'is related to $v',
    'parent_of' => 'is the parent of $v',
    'child_of' => 'is the child of $v',
    'manager_of' => 'manages $v',
    'reports_to' => 'reports to $v',
    'relation_to_user' => 'is your $v',
    _ => v,
  };
  return '$who: $what';
}

/// The "Learned" inbox: facts Jarvis noticed in conversations, grouped by conversation. Nothing enters the graph until
/// the user taps Approve (or Approve all, which states how many it applies to). Hidden when there is nothing to review.
class LearnedSection extends StatefulWidget {
  const LearnedSection({super.key, required this.api, this.learned, this.onGraphChanged});
  final ApiClient api;
  final LearnedController? learned;
  final VoidCallback? onGraphChanged; // a fact was written or retracted: the graph behind this section must reload

  @override
  State<LearnedSection> createState() => _LearnedSectionState();
}

class _LearnedSectionState extends State<LearnedSection> {
  List<Map<String, dynamic>> _groups = [];
  bool _open = true;

  List<Map<String, dynamic>> _items(Map<String, dynamic> g) => _maps(g['items']);
  int get _total => _groups.fold(0, (n, g) => n + _items(g).length);

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final g = _maps((await widget.api.get('/graph/candidates'))['groups']);
      if (!mounted) return;
      setState(() => _groups = g);
      widget.learned?.set(_total);
    } catch (_) {} // keep what is shown: the screen's own load reports connection problems
  }

  void _toast(String m) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  void _drop(Set<int> ids) {
    if (!mounted) return;
    setState(() {
      _groups = [
        for (final g in _groups)
          if (_items(g).any((c) => !ids.contains(c['id'])))
            {...g, 'items': [for (final c in _items(g)) if (!ids.contains(c['id'])) c]},
      ];
    });
    widget.learned?.set(_total);
  }

  Future<void> _approve(Map<String, dynamic> c, {Map<String, dynamic>? edits}) async {
    final id = c['id'] as int;
    var body = <String, dynamic>{...?edits};
    while (true) {
      try {
        final r = await widget.api.post('/graph/candidates/$id/approve', body.isEmpty ? null : body);
        _drop({id});
        widget.onGraphChanged?.call();
        _undoBar(c, r);
        return;
      } on ApiError catch (e) {
        if (e.status == 409 && (e.body?['error'] == 'ambiguous_subject' || e.body?['error'] == 'ambiguous_other')) {
          final pick = await _choose(c, _maps(e.body?['choice']), other: e.body?['error'] == 'ambiguous_other');
          if (pick == null) return; // the user backed out: nothing was written
          body = {...body, ...pick};
          continue;
        }
        if (e.status == 404) _drop({id}); // already decided elsewhere
        _toast(e.status == 404 ? 'That was already handled' : "Couldn't save that");
        return;
      } catch (_) {
        _toast("Couldn't save that");
        return;
      }
    }
  }

  /// Several people share the name: ask which one, or a new person. Never guesses. For a link candidate `other` asks
  /// about the person it names instead of its subject.
  Future<Map<String, dynamic>?> _choose(Map<String, dynamic> c, List<Map<String, dynamic>> options, {bool other = false}) {
    if (!mounted) return Future.value(null);
    return showDialog<Map<String, dynamic>>(
      context: context,
      builder: (ctx) => SimpleDialog(
        title: Text('Which ${_clean(other ? c['value'] : c['subject'])}?'),
        children: [
          for (final o in options)
            SimpleDialogOption(
              key: ValueKey('pick-${o['id']}'),
              onPressed: () => Navigator.pop(ctx, {other ? 'other_entity_id' : 'entity_id': o['id']}),
              child: Text('${o['name']} (${o['kind']}, #${o['id']})'),
            ),
          SimpleDialogOption(
            key: const Key('pick-new'),
            onPressed: () => Navigator.pop(ctx, {other ? 'new_other' : 'new': true}),
            child: const Text('A new person'),
          ),
        ],
      ),
    );
  }

  void _undoBar(Map<String, dynamic> c, Map<String, dynamic> r) {
    if (!mounted) return;
    final ids = [for (final i in (r['fact_ids'] as List? ?? const [])) if (i is int) i];
    final m = ScaffoldMessenger.of(context);
    m.hideCurrentSnackBar();
    m.showSnackBar(SnackBar(
      duration: const Duration(seconds: 5),
      persist: false, // a SnackBar with an action otherwise stays until swiped: Undo is a 5-second window
      content: Text('Saved: ${learnedSentence(c)}'),
      action: SnackBarAction(label: 'Undo', onPressed: () => _undo(ids)),
    ));
  }

  /// Retract what the approval wrote (the facts only: a person who existed before stays).
  Future<void> _undo(List<int> factIds) async {
    try {
      for (final id in factIds) {
        await widget.api.delete('/graph/facts/$id');
      }
    } catch (_) {
      _toast("Couldn't undo that");
    }
    widget.onGraphChanged?.call();
  }

  Future<void> _dismiss(Map<String, dynamic> c) async {
    final id = c['id'] as int;
    try {
      await widget.api.post('/graph/candidates/$id/dismiss');
      _drop({id});
    } on ApiError catch (e) {
      if (e.status == 404) {
        _drop({id});
      } else {
        _toast("Couldn't dismiss that");
      }
    } catch (_) {
      _toast("Couldn't dismiss that");
    }
  }

  Future<void> _edit(Map<String, dynamic> c) async {
    final edits = await showModalBottomSheet<Map<String, dynamic>>(
      context: context,
      isScrollControlled: true,
      backgroundColor: blush,
      builder: (_) => _EditSheet(candidate: c),
    );
    if (edits != null) await _approve(c, edits: edits);
  }

  Future<void> _bulk(Map<String, dynamic> g, String decision) async {
    final items = _items(g), n = items.length;
    final title = _clean((g['conversation'] as Map?)?['title']);
    final verb = decision == 'approve' ? 'Approve' : 'Dismiss';
    final sure = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('$verb all $n?'),
        content: Text(decision == 'approve'
            ? 'Saves $n ${n == 1 ? 'fact' : 'facts'}${title.isEmpty ? '' : ' from "$title"'} to your memory.'
            : "Discards $n ${n == 1 ? 'fact' : 'facts'} and won't suggest ${n == 1 ? 'it' : 'them'} again."),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(key: const Key('bulk-confirm'), onPressed: () => Navigator.pop(ctx, true), child: Text('$verb all $n')),
        ],
      ),
    );
    if (sure != true || !mounted) return;
    try {
      final r = await widget.api.post('/graph/candidates/bulk', {
        'ids': [for (final c in items) c['id']],
        'decision': decision,
      });
      await _load(); // the server's answer is the truth, whatever was skipped
      if (decision == 'approve' && (r['approved'] as List? ?? const []).isNotEmpty) widget.onGraphChanged?.call();
      final choice = (r['needs_choice'] as List? ?? const []).length;
      if (choice > 0) _toast('$choice need you to pick who they are about. Approve them one by one.');
    } catch (_) {
      _toast("Couldn't do that");
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_total == 0) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        Semantics(
          button: true,
          expanded: _open,
          label: 'Learned, $_total to review',
          child: InkWell(
            key: const Key('learned-header'),
            onTap: () => setState(() => _open = !_open),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Row(children: [
                Expanded(child: Text('Learned · $_total', style: condensed(20, weight: FontWeight.w300, color: cream))),
                Icon(_open ? Icons.expand_less : Icons.expand_more, color: cream),
              ]),
            ),
          ),
        ),
        if (_open)
          ConstrainedBox(
            constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.45),
            child: ListView(shrinkWrap: true, children: [for (final g in _groups) ..._group(g)]),
          ),
      ]),
    );
  }

  List<Widget> _group(Map<String, dynamic> g) {
    final conv = (g['conversation'] as Map?) ?? const {};
    final title = _clean(conv['title']).isEmpty ? 'Conversation' : _clean(conv['title']);
    final items = _items(g);
    return [
      Padding(
        padding: const EdgeInsets.fromLTRB(8, 8, 8, 4),
        child: Wrap(crossAxisAlignment: WrapCrossAlignment.center, spacing: 4, children: [
          Text('$title · ${_when(conv['last_at'])}', style: TextStyle(color: cream.withValues(alpha: 0.8), fontSize: 13)),
          TextButton(
            key: ValueKey('approve-all-${conv['id']}'),
            onPressed: () => _bulk(g, 'approve'),
            child: Text('Approve all ${items.length}'),
          ),
          TextButton(
            key: ValueKey('dismiss-all-${conv['id']}'),
            onPressed: () => _bulk(g, 'dismiss'),
            child: Text('Dismiss all ${items.length}'),
          ),
        ]),
      ),
      for (final c in items) _card(c),
    ];
  }

  Widget _card(Map<String, dynamic> c) {
    final replaces = _clean(c['replaces']), quote = _clean(c['quote']);
    return Container(
      key: ValueKey('candidate-${c['id']}'),
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.fromLTRB(16, 12, 8, 4),
      decoration: BoxDecoration(color: blush, borderRadius: BorderRadius.circular(24)),
      child: DefaultTextStyle.merge(
        style: const TextStyle(color: ink),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(learnedSentence(c), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
          if (replaces.isNotEmpty)
            Text('Currently: $replaces', style: TextStyle(fontSize: 13, color: ink.withValues(alpha: 0.7))),
          if (quote.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('You said: “$quote”', // plain text, never interpreted
                  style: TextStyle(fontSize: 13, fontStyle: FontStyle.italic, color: ink.withValues(alpha: 0.7))),
            ),
          Wrap(children: [
            TextButton(key: ValueKey('approve-${c['id']}'), onPressed: () => _approve(c), child: const Text('Approve')),
            TextButton(key: ValueKey('edit-${c['id']}'), onPressed: () => _edit(c), child: const Text('Edit')),
            TextButton(key: ValueKey('dismiss-${c['id']}'), onPressed: () => _dismiss(c), child: const Text('Dismiss')),
          ]),
        ]),
      ),
    );
  }
}

/// Edit before approving: who it is about, how they relate to you, and the fact itself.
class _EditSheet extends StatefulWidget {
  const _EditSheet({required this.candidate});
  final Map<String, dynamic> candidate;

  @override
  State<_EditSheet> createState() => _EditSheetState();
}

class _EditSheetState extends State<_EditSheet> {
  late final _subject = TextEditingController(text: _clean(widget.candidate['subject']));
  late final _relation = TextEditingController(text: _clean(widget.candidate['relation']));
  late final _value = TextEditingController(text: _clean(widget.candidate['value']));

  bool get _self => widget.candidate['subject_kind'] == 'self';

  @override
  void dispose() {
    _subject.dispose();
    _relation.dispose();
    _value.dispose();
    super.dispose();
  }

  void _save() {
    final c = widget.candidate;
    final edits = <String, dynamic>{};
    if (!_self && _subject.text.trim().isNotEmpty && _subject.text.trim() != _clean(c['subject'])) {
      edits['subject'] = _subject.text.trim();
    }
    if (!_self && _relation.text.trim() != _clean(c['relation'])) edits['relation'] = _relation.text.trim();
    if (_value.text.trim().isNotEmpty && _value.text.trim() != _clean(c['value'])) edits['value'] = _value.text.trim();
    Navigator.pop(context, edits);
  }

  @override
  Widget build(BuildContext context) => Padding(
    padding: EdgeInsets.fromLTRB(20, 20, 20, 20 + MediaQuery.viewInsetsOf(context).bottom),
    child: DefaultTextStyle.merge(
      style: const TextStyle(color: ink),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Edit before saving', style: Theme.of(context).textTheme.titleMedium?.copyWith(color: ink)),
        if (!_self) ...[
          TextField(key: const Key('edit-subject'), controller: _subject, decoration: const InputDecoration(labelText: 'Who')),
          TextField(key: const Key('edit-relation'), controller: _relation, decoration: const InputDecoration(labelText: 'Relation to you')),
        ],
        TextField(key: const Key('edit-value'), controller: _value, decoration: const InputDecoration(labelText: 'Fact')),
        const SizedBox(height: 12),
        Align(
          alignment: Alignment.centerRight,
          child: FilledButton(key: const Key('edit-save'), onPressed: _save, child: const Text('Save')),
        ),
      ]),
    ),
  );
}
