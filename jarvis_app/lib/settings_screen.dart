import 'package:flutter/material.dart';

import 'calendar_screen.dart' show ScreenShell;
import 'api.dart';
import 'keys_screen.dart';
import 'prefs.dart';
import 'theme.dart';

/// Settings: re-authorise Google on the backend. [startAuth] asks the backend for the consent URL,
/// [open] hands it to the phone's browser (Google blocks OAuth inside embedded WebViews).
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.startAuth, required this.open, this.onReturn, this.api, this.prefs, this.onWakeChanged});
  final Prefs? prefs; // null: no wake or night settings (tests)
  final Future<void> Function()? onWakeChanged; // the wake switch moved: start or stop the service
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

  Future<void> _pickNight(bool start) async {
    final p = widget.prefs!;
    final m = start ? p.nightStart : p.nightEnd;
    final t = await showTimePicker(context: context, initialTime: TimeOfDay(hour: m ~/ 60, minute: m % 60));
    if (t == null) return;
    final v = t.hour * 60 + t.minute;
    await (start ? p.setNight(v, p.nightEnd) : p.setNight(p.nightStart, v));
  }

  List<Widget> _wakeSection(BuildContext context, Prefs p) {
    String fmt(int m) => TimeOfDay(hour: m ~/ 60, minute: m % 60).format(context);
    return [
      SwitchListTile(
          contentPadding: EdgeInsets.zero,
          activeThumbColor: amber,
          title: const Text('Hey Jarvis wake word', style: TextStyle(color: cream)),
          value: p.wakeEnabled,
          onChanged: (v) async {
            await p.setWakeEnabled(v);
            await widget.onWakeChanged?.call();
          }),
      ListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Night starts', style: TextStyle(color: cream)),
          trailing: Text(fmt(p.nightStart), style: const TextStyle(color: amber)),
          onTap: () => _pickNight(true)),
      ListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Night ends', style: TextStyle(color: cream)),
          trailing: Text(fmt(p.nightEnd), style: const TextStyle(color: amber)),
          onTap: () => _pickNight(false)),
      const Text('Night hours: Jarvis shows replies as text and makes no sound.', style: TextStyle(color: mutedText, fontSize: 12)),
      const SizedBox(height: 16),
      const Text('Double-tap on Pixel', style: TextStyle(color: cream)),
      const Text('Settings > System > Gestures > Quick Tap, then choose Jarvis.', style: TextStyle(color: mutedText, fontSize: 12)),
      const SizedBox(height: 24),
    ];
  }

  @override
  Widget build(BuildContext context) => ScreenShell(
        title: 'Settings',
        child: ListView(padding: const EdgeInsets.all(16), children: [
          if (widget.api != null) ListTile(
            leading: const Icon(Icons.key, color: amber),
            title: const Text('API keys', style: TextStyle(color: cream)),
            subtitle: const Text('AI provider', style: TextStyle(color: mutedText)),
            onTap: () async {
              await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => KeysScreen(api: widget.api!)));
              widget.onReturn?.call();
            },
          ),
          if (widget.prefs != null)
            ListenableBuilder(
                listenable: widget.prefs!,
                builder: (context, _) => Column(
                    crossAxisAlignment: CrossAxisAlignment.start, children: _wakeSection(context, widget.prefs!))),
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
