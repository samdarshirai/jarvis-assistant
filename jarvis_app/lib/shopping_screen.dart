import 'package:flutter/material.dart';

import 'api.dart';
import 'calendar_screen.dart';
import 'theme.dart';

class ShoppingItem {
  ShoppingItem(this.id, this.title);
  final String id, title;
  bool done = false;
}

/// The Shopping list (a hidden Google Tasks list): add, tick off as bought.
class ShoppingScreen extends StatefulWidget {
  const ShoppingScreen({super.key, required this.api, this.onAskJarvis});
  final ApiClient api;
  final VoidCallback? onAskJarvis;

  @override
  State<ShoppingScreen> createState() => _ShoppingScreenState();
}

class _ShoppingScreenState extends State<ShoppingScreen> {
  List<ShoppingItem>? _items;
  bool _loading = true, _error = false;
  final _field = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _field.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = false;
    });
    try {
      final l = (await widget.api.get('/shopping'))['items'];
      final items = l is List
          ? [
              for (final e in l)
                if (e is Map<String, dynamic>)
                  ShoppingItem(
                    '${e['id']}',
                    e['title'] is String ? e['title'] as String : '',
                  ),
            ]
          : null;
      if (mounted) {
        setState(() {
          _items = items;
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

  void _snack(String m) =>
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));

  Future<void> _complete(ShoppingItem i) async {
    if (i.done) return;
    setState(() => i.done = true);
    try {
      await widget.api.post('/shopping/${i.id}/complete');
    } catch (_) {
      if (!mounted) return;
      setState(() => i.done = false);
      _snack("Couldn't update");
    }
  }

  Future<void> _add(String raw) async {
    var title = raw.trim();
    if (title.isEmpty) return;
    if (title.length > 100) title = title.substring(0, 100);
    _field.clear();
    try {
      final it = (await widget.api.post('/shopping', {'title': title}))['item'];
      if (mounted && it is Map<String, dynamic>) {
        setState(
          () => _items = [
            ...?_items,
            ShoppingItem('${it['id']}', '${it['title']}'),
          ],
        );
      }
    } catch (_) {
      if (mounted) _snack("Couldn't add item");
    }
  }

  @override
  Widget build(BuildContext context) => ScreenShell(
    title: 'Shopping',
    onMic: widget.onAskJarvis,
    child: _loading
        ? const Center(child: CircularProgressIndicator())
        : _error
        ? ErrorRetry(onRetry: _load)
        : _body(),
  );

  Widget _body() {
    final items = _items;
    if (items == null) {
      return const Center(child: Text('Unavailable', style: TextStyle(color: mutedText)));
    }
    final left = items.where((i) => !i.done).length;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 110),
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 6, 4, 14),
          child: Text.rich(
            TextSpan(
              children: [
                TextSpan(
                  text: '$left',
                  style: condensed(48, color: cream),
                ),
                TextSpan(
                  text: ' to buy',
                  style: condensed(40, weight: FontWeight.w200, color: cream),
                ),
              ],
            ),
          ),
        ),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.fromLTRB(18, 14, 18, 10),
          decoration: BoxDecoration(
            color: blush,
            borderRadius: BorderRadius.circular(32),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (items.isEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8, bottom: 12),
                  child: Text(
                    'List is empty.',
                    style: condensed(28, weight: FontWeight.w300, color: ink),
                  ),
                ),
              for (final i in items) _row(i),
              Row(
                children: [
                  Icon(Icons.add, size: 22, color: ink.withValues(alpha: 0.5)),
                  const SizedBox(width: 10),
                  Expanded(
                    child: TextField(
                      controller: _field,
                      maxLength: 100,
                      style: const TextStyle(color: ink, fontSize: 15),
                      textInputAction: TextInputAction.done,
                      onSubmitted: _add,
                      decoration: InputDecoration(
                        counterText: '',
                        hintText: 'Add an item and press Enter',
                        hintStyle: TextStyle(color: ink.withValues(alpha: 0.4)),
                        border: InputBorder.none,
                        enabledBorder: InputBorder.none,
                        focusedBorder: InputBorder.none,
                        filled: false,
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _row(ShoppingItem i) => Container(
    decoration: BoxDecoration(
      border: Border(bottom: BorderSide(color: ink.withValues(alpha: 0.06))),
    ),
    padding: const EdgeInsets.symmetric(vertical: 10),
    child: Row(
      children: [
        GestureDetector(
          key: ValueKey('buy-${i.id}'),
          behavior: HitTestBehavior.opaque,
          onTap: () => _complete(i),
          child: Container(
            width: 22,
            height: 22,
            decoration: BoxDecoration(
              color: i.done ? darkPill : const Color(0xFFEBCFC4),
              borderRadius: BorderRadius.circular(7),
            ),
            child: i.done
                ? const Icon(Icons.check, size: 16, color: Colors.white)
                : null,
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Text(
            i.title,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              color: ink.withValues(alpha: i.done ? 0.45 : 1),
              fontSize: 15,
              decoration: i.done ? TextDecoration.lineThrough : null,
            ),
          ),
        ),
      ],
    ),
  );
}
