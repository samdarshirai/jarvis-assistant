import 'package:flutter/material.dart';

import 'calendar_screen.dart' show ScreenShell;
import 'api.dart';
import 'keys_screen.dart';
import 'theme.dart';

/// Settings: re-authorise Google on the backend. [startAuth] asks the backend for the consent URL,
/// [open] hands it to the phone's browser (Google blocks OAuth inside embedded WebViews).
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.startAuth, required this.open, this.onReturn, this.api});
  final ApiClient? api;
  final Future<String> Function() startAuth;
  final Future<void> Function(Uri url) open;
  final VoidCallback? onReturn; // after the browser step, so the dashboard can refetch

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> with WidgetsBindingObserver {
  bool _busy = false, _opened = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && _opened) {
      _opened = false;
      widget.onReturn?.call();
    }
  }

  Future<void> _reconnect() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final url = await widget.startAuth();
      _opened = true;
      await widget.open(Uri.parse(url));
    } catch (_) {
      _opened = false;
      if (mounted) setState(() => _error = "Couldn't start Google sign-in. Check the backend and try again.");
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => ScreenShell(
        title: 'Settings',
        child: ListView(padding: const EdgeInsets.all(16), children: [
          if (widget.api != null) ListTile(
            leading: const Icon(Icons.key, color: amber),
            title: const Text('API keys', style: TextStyle(color: cream)),
            subtitle: const Text('AI provider, voice and web search', style: TextStyle(color: mutedText)),
            onTap: () async {
              await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => KeysScreen(api: widget.api!)));
              widget.onReturn?.call();
            },
          ),
          const Text('Google', style: TextStyle(color: mutedText, fontSize: 12)),
          const SizedBox(height: 8),
          const Text('Calendar, tasks and email access can expire. Reconnect to sign in to Google again.',
              style: TextStyle(color: cream)),
          const SizedBox(height: 12),
          FilledButton.icon(
              onPressed: _busy ? null : _reconnect,
              icon: const Icon(Icons.link),
              label: Text(_busy ? 'Opening…' : 'Reconnect Google')),
          if (_error != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(_error!, style: const TextStyle(color: amber))),
        ]),
      );
}
