import 'package:flutter/material.dart';

import 'api.dart';
import 'calendar_screen.dart' show ScreenShell;
import 'mail_screen.dart' show LoadView;
import 'theme.dart';

const defaultLlmUrl = 'https://openrouter.ai/api/v1';

const llmHowTo = 'Jarvis thinks with an AI provider you pay for (any OpenAI-compatible one). For OpenRouter: sign in at '
    'openrouter.ai, open Keys, create a key, add a little credit, and paste the key here. The base URL stays $defaultLlmUrl.';
const deepgramHowTo = 'Voice uses Deepgram to hear you. Sign up at console.deepgram.com (free credit), open API Keys, '
    'create a key and paste it here. Without it, typed chat still works.';
const tavilyHowTo = 'Web search uses Tavily. Sign up at app.tavily.com (free tier) and copy the API key shown there. '
    'Without it, Jarvis uses DuckDuckGo for web search.';

/// The backend's 422 `{error, model}` from a key test, in plain words.
String keyErrorText(Object e) {
  if (e is! ApiError) return "Couldn't reach Jarvis. Check your connection and try again.";
  final b = e.body, model = b?['model'];
  final what = switch (b?['error']) {
    'rejected_key' => 'The provider rejected this API key',
    'out_of_credit' => 'The account behind this key is out of credit',
    'model_not_found' => 'The provider does not offer this model',
    'bad_url' => 'That base URL is not allowed: it must be a public https address',
    'unreachable' => 'Could not reach the provider at that base URL',
    'no_models' => 'Enter a fast and a strong model id under Advanced',
    'bad_request' => 'Check the model ids (up to 5, comma-separated) and the key',
    _ => e.status == 422 ? 'That key was not accepted' : 'The key could not be saved (HTTP ${e.status})',
  };
  return model == null ? '$what.' : '$what (model: $model).';
}

InputDecoration _field(String label, {String? hint}) => InputDecoration(labelText: label, hintText: hint);

/// LLM base URL + key (+ model ids under "Advanced"); "Test and save" runs the backend's key test.
class LlmForm extends StatefulWidget {
  const LlmForm({super.key, required this.api, required this.onSaved, this.baseUrl, this.modelFast, this.modelStrong});
  final ApiClient api;
  final VoidCallback onSaved;
  final String? baseUrl;
  final String? modelFast, modelStrong;
  @override
  State<LlmForm> createState() => _LlmFormState();
}

class _LlmFormState extends State<LlmForm> {
  late final _url = TextEditingController(text: widget.baseUrl ?? defaultLlmUrl);
  final _key = TextEditingController();
  late final _fast = TextEditingController(text: widget.modelFast), _strong = TextEditingController(text: widget.modelStrong);
  bool _busy = false;
  String? _msg;
  bool _ok = false;

  @override
  void dispose() {
    for (final c in [_url, _key, _fast, _strong]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    setState(() {
      _busy = true;
      _msg = null;
    });
    try {
      // only ids the user typed: a prefilled server default must not be frozen into their settings
      final fast = _fast.text.trim(), strong = _strong.text.trim();
      await widget.api.put('/settings/llm', {
        'base_url': _url.text.trim(),
        'api_key': _key.text.trim(),
        if (fast.isNotEmpty && fast != (widget.modelFast ?? '')) 'model_fast': fast,
        if (strong.isNotEmpty && strong != (widget.modelStrong ?? '')) 'model_strong': strong,
      });
      if (!mounted) return;
      _key.clear();
      _ok = true;
      _msg = 'Key works. Saved.';
      widget.onSaved();
    } catch (e) {
      _ok = false;
      _msg = keyErrorText(e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const Text(llmHowTo, style: TextStyle(color: mutedText)),
        TextField(controller: _url, decoration: _field('Base URL'), keyboardType: TextInputType.url),
        TextField(controller: _key, decoration: _field('API key'), obscureText: true),
        ExpansionTile(
            title: const Text('Advanced', style: TextStyle(color: cream)),
            tilePadding: EdgeInsets.zero,
            children: [
              TextField(controller: _fast, decoration: _field('Fast model id', hint: 'leave empty for the default')),
              TextField(controller: _strong, decoration: _field('Strong model id', hint: 'leave empty for the default')),
            ]),
        const SizedBox(height: 8),
        FilledButton(
            style: FilledButton.styleFrom(backgroundColor: amber, foregroundColor: amberInk),
            onPressed: _busy ? null : _save,
            child: Text(_busy ? 'Testing…' : 'Test and save')),
        if (_msg != null)
          Padding(
              padding: const EdgeInsets.only(top: 8), child: Text(_msg!, style: TextStyle(color: _ok ? cream : danger))),
      ]);
}

/// One optional key (Deepgram or Tavily): paste and save.
class KeyField extends StatefulWidget {
  const KeyField({super.key, required this.api, required this.name, required this.label, required this.howTo, this.onSaved});
  final ApiClient api;
  final String name; // path segment: /settings/<name>
  final String label, howTo;
  final VoidCallback? onSaved;
  @override
  State<KeyField> createState() => _KeyFieldState();
}

class _KeyFieldState extends State<KeyField> {
  final _key = TextEditingController();
  bool _busy = false, _ok = false;
  String? _msg;

  @override
  void dispose() {
    _key.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() {
      _busy = true;
      _msg = null;
    });
    try {
      await widget.api.put('/settings/${widget.name}', {'api_key': _key.text.trim()});
      if (!mounted) return;
      _key.clear();
      _ok = true;
      _msg = 'Saved.';
      widget.onSaved?.call();
    } catch (e) {
      _ok = false;
      _msg = keyErrorText(e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(widget.howTo, style: const TextStyle(color: mutedText)),
        Row(children: [
          Expanded(child: TextField(controller: _key, decoration: _field('${widget.label} key'), obscureText: true)),
          TextButton(onPressed: _busy ? null : _save, child: Text(_busy ? 'Saving…' : 'Save')),
        ]),
        if (_msg != null) Text(_msg!, style: TextStyle(color: _ok ? cream : danger)),
      ]);
}

/// Settings > API keys: shows only "set" + last 4 from /me, never a key; replaces keys and removes optional ones.
class KeysScreen extends StatefulWidget {
  const KeysScreen({super.key, required this.api});
  final ApiClient api;
  @override
  State<KeysScreen> createState() => _KeysScreenState();
}

class _KeysScreenState extends State<KeysScreen> {
  int _gen = 0; // bump to refetch /me
  String? _error;

  void _reload() => setState(() => _gen++);

  /// The refetch rebuilds the forms (and their inline message), so confirm the save in a snackbar.
  void _saved(String text) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
    _reload();
  }

  Future<void> _delete(String name) async {
    try {
      await widget.api.delete('/settings/$name');
      _error = null;
    } catch (e) {
      _error = keyErrorText(e);
    }
    if (mounted) _reload();
  }

  // "set" comes from /me.setup: a key under 8 characters is set but has no last 4
  Widget _status(String title, bool set, String? last4, {String? remove}) => Row(children: [
        Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: const TextStyle(color: mutedText, fontSize: 12)),
          Text(!set ? 'Not set' : last4 == null ? 'Set' : 'Set, ends in $last4', style: const TextStyle(color: cream)),
        ])),
        if (remove != null && set)
          IconButton(
              tooltip: 'Remove $title key', icon: const Icon(Icons.delete_outline, color: cream), onPressed: () => _delete(remove)),
      ]);

  Widget _body(Map<String, dynamic> me) {
    final k = (me['keys'] as Map?) ?? const {}, setup = (me['setup'] as Map?) ?? const {};
    return ListView(padding: const EdgeInsets.all(16), children: [
      _status('AI provider', setup['llm'] == true, k['llm_key_last4'] as String?),
      if (k['llm_base_url'] != null) Text('Base URL: ${k['llm_base_url']}', style: const TextStyle(color: mutedText)),
      if (k['model_fast'] != null) Text('Models: ${k['model_fast']}, ${k['model_strong']}', style: const TextStyle(color: mutedText)),
      const SizedBox(height: 8),
      LlmForm(api: widget.api, baseUrl: k['llm_base_url'] as String?,
          modelFast: k['model_fast'] as String?, modelStrong: k['model_strong'] as String?, onSaved: () => _saved('AI key works. Saved.')),
      const Divider(height: 32),
      _status('Deepgram', setup['deepgram'] == true, k['deepgram_last4'] as String?, remove: 'deepgram'),
      KeyField(api: widget.api, name: 'deepgram', label: 'Deepgram', howTo: deepgramHowTo, onSaved: () => _saved('Deepgram key saved.')),
      const Divider(height: 32),
      _status('Tavily', setup['tavily'] == true, k['tavily_last4'] as String?, remove: 'tavily'),
      KeyField(api: widget.api, name: 'tavily', label: 'Tavily', howTo: tavilyHowTo, onSaved: () => _saved('Tavily key saved.')),
      if (_error != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(_error!, style: const TextStyle(color: danger))),
    ]);
  }

  @override
  Widget build(BuildContext context) =>
      ScreenShell(title: 'API keys', child: LoadView(key: ValueKey(_gen), load: () => widget.api.get('/me'), builder: _body));
}
