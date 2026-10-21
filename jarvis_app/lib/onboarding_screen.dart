import 'package:flutter/material.dart';

import 'api.dart';
import 'keys_screen.dart';
import 'theme.dart';

enum OnboardingStep { google, name, llm, deepgram, tavily, ready }

const rejectionHint = 'Not on the list? Ask the owner to add your email.';

OnboardingStep firstMissingStep(Map setup, {String? displayName}) {
  if (setup['google'] != true) return OnboardingStep.google;
  if (displayName == null || displayName.trim().isEmpty) {
    return OnboardingStep.name;
  }
  if (setup['llm'] != true) return OnboardingStep.llm;
  if (setup['deepgram'] != true) return OnboardingStep.deepgram;
  if (setup['tavily'] != true) return OnboardingStep.tavily;
  return OnboardingStep.ready;
}

/// Guided setup. Only /me.setup determines which credentials are saved.
class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({
    super.key,
    required this.signIn,
    this.api,
    this.me = const {},
    this.error,
    required this.onDone,
  });
  final Future<void> Function() signIn;
  final ApiClient? api;
  final Map<String, dynamic> me;
  final String? error;
  final Future<void> Function() onDone;
  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  late Map<String, dynamic> _me = widget.me;
  late OnboardingStep _step = firstMissingStep(
    _setup,
    displayName: _me['display_name'] as String?,
  );
  Map get _setup => (_me['setup'] as Map?) ?? const {};
  Map get _keys => (_me['keys'] as Map?) ?? const {};
  bool _opened = false, _busy = false;
  String? _error;
  final _name = TextEditingController();

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _saveName() async {
    final name = _name.text.trim();
    if (name.isEmpty ||
        name.runes.length > 100 ||
        RegExp(r'[\n\r\v\f\x1c-\x1e\x85\u2028\u2029\x00]')
            .hasMatch(_name.text)) {
      setState(
        () => _error = 'Enter a name of 1–100 characters without line breaks.',
      );
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final me = await widget.api!.put('/me', {'display_name': name});
      if (me['display_name'] is! String ||
          (me['display_name'] as String).trim().isEmpty ||
          me['setup'] is! Map) {
        throw const FormatException('Missing profile');
      }
      if (!mounted) return;
      setState(() => _me = me);
      final next = firstMissingStep(
        _setup,
        displayName: me['display_name'] as String,
      );
      if (next == OnboardingStep.ready) {
        await widget.onDone();
      } else {
        setState(() => _step = next);
      }
    } catch (_) {
      if (mounted) {
        setState(
          () => _error = 'Your name could not be saved. Check your connection and try Continue again.',
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _signIn() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.signIn();
      if (mounted) setState(() => _opened = true);
    } catch (_) {
      if (mounted) {
        setState(
          () => _error = "Couldn't start Google sign-in. Check your connection and try again.",
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _refresh() async {
    try {
      final me = await widget.api!.get('/me');
      if (me['setup'] is! Map) throw const FormatException('Missing setup');
      if (mounted) {
        setState(() {
          _me = me;
          _error = null;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(
          () => _error = 'Could not check your setup. Check your connection and try again.',
        );
      }
    }
  }

  Future<void> _continue({bool finish = false}) async {
    setState(() => _busy = true);
    await _refresh();
    if (!mounted) return;
    if (_error == null) {
      final next = firstMissingStep(
        _setup,
        displayName: _me['display_name'] as String?,
      );
      if (finish && next == OnboardingStep.ready) {
        await widget.onDone();
      } else {
        setState(() => _step = next);
      }
    }
    if (mounted) setState(() => _busy = false);
  }

  String? get _keyName => switch (_step) {
    OnboardingStep.llm => 'llm',
    OnboardingStep.deepgram => 'deepgram',
    OnboardingStep.tavily => 'tavily',
    _ => null,
  };

  Widget _primary(String label, VoidCallback? onTap) => FilledButton(
    style: FilledButton.styleFrom(
      backgroundColor: amber,
      foregroundColor: amberInk,
      minimumSize: const Size.fromHeight(52),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
      textStyle: const TextStyle(
        fontFamily: 'InstrumentSans',
        fontSize: 16,
        fontWeight: FontWeight.w600,
      ),
    ),
    onPressed: _busy ? null : onTap,
    child: Text(_busy ? 'Please wait…' : label),
  );

  Widget _form() {
    void busy(bool value) {
      if (mounted) setState(() => _busy = value);
    }

    if (_step == OnboardingStep.llm) {
      return LlmForm(
        api: widget.api!,
        baseUrl: _keys['llm_base_url'] as String?,
        modelFast: _keys['model_fast'] as String?,
        modelStrong: _keys['model_strong'] as String?,
        onSaved: _refresh,
        onBusyChanged: busy,
      );
    }
    final deepgram = _step == OnboardingStep.deepgram;
    return KeyField(
      api: widget.api!,
      name: deepgram ? 'deepgram' : 'tavily',
      label: deepgram ? 'Deepgram' : 'Tavily',
      howTo: deepgram ? deepgramHowTo : tavilyHowTo,
      onSaved: _refresh,
      onBusyChanged: busy,
    );
  }

  static const _labels = [
    'Connect Google',
    'Your name',
    'AI key',
    'Deepgram key',
    'Tavily key',
    'Ready',
  ];

  List<bool> get _completed => [
    _setup['google'] == true,
    (_me['display_name'] as String?)?.trim().isNotEmpty == true,
    _setup['llm'] == true,
    _setup['deepgram'] == true,
    _setup['tavily'] == true,
  ];

  Widget _progress() => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        'Step ${_step.index + 1} of ${_labels.length} • ${_labels[_step.index]}',
        style: const TextStyle(
          color: cream,
          fontSize: 13,
          fontWeight: FontWeight.w600,
        ),
      ),
      const SizedBox(height: 12),
      Row(
        children: [
          for (var i = 0; i < _labels.length; i++) ...[
            if (i > 0) const SizedBox(width: 6),
            Expanded(
              child: Container(
                height: 4,
                decoration: BoxDecoration(
                  color: i <= _step.index
                      ? amber
                      : cream.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(4),
                ),
              ),
            ),
          ],
        ],
      ),
      const SizedBox(height: 12),
      Wrap(
        spacing: 14,
        runSpacing: 8,
        children: [
          for (var i = 0; i < _completed.length; i++)
            if (_completed[i])
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(
                    Icons.check_circle_rounded,
                    color: amber,
                    size: 14,
                  ),
                  const SizedBox(width: 5),
                  Flexible(
                    child: Text(
                      _labels[i],
                      style: const TextStyle(color: mutedText, fontSize: 12),
                    ),
                  ),
                ],
              ),
        ],
      ),
    ],
  );

  Widget _panel({required Widget child, bool light = false}) => Material(
    color: light ? blush : darkPill.withValues(alpha: 0.35),
    borderRadius: BorderRadius.circular(28),
    child: Padding(padding: const EdgeInsets.all(20), child: child),
  );

  Widget _googleIntro() => _panel(
    light: true,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'YOUR DAY, CONNECTED',
          style: const TextStyle(
            color: ink,
            fontSize: 11,
            fontWeight: FontWeight.w700,
            letterSpacing: 1.5,
          ),
        ),
        const SizedBox(height: 16),
        for (final (icon, title, detail) in [
          (Icons.calendar_today_outlined, 'Calendar', 'Stay ahead of your day'),
          (Icons.mail_outline_rounded, 'Gmail', 'Keep up with what matters'),
          (
            Icons.check_circle_outline_rounded,
            'Tasks',
            'Make room for getting things done',
          ),
        ])
          Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: ink.withValues(alpha: 0.06),
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Icon(icon, color: ink, size: 20),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        title,
                        style: const TextStyle(
                          color: ink,
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      Text(
                        detail,
                        style: TextStyle(
                          color: ink.withValues(alpha: 0.7),
                          fontSize: 13,
                          height: 1.5,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        const Text(
          'Then paste three required API keys: OpenRouter (or another AI provider), '
          'Deepgram for voice, and Tavily for web search.',
          style: TextStyle(color: ink, height: 1.5, fontSize: 13),
        ),
      ],
    ),
  );

  Widget _readySummary() => _panel(
    light: true,
    child: Column(
      children: [
        for (final status in [
          'Google connected',
          'AI key saved',
          'Deepgram key saved',
          'Tavily key saved',
        ])
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 10),
            child: Row(
              children: [
                const Icon(Icons.check_circle_rounded, color: ink, size: 22),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    status,
                    style: const TextStyle(
                      color: ink,
                      fontSize: 15,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ],
            ),
          ),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) {
    final name = _keyName;
    final (eyebrow, title, subtitle, icon) = switch (_step) {
      OnboardingStep.google => (
        'A LITTLE SETUP. A LIGHTER DAY.',
        'Meet your Jarvis.',
        'Your calendar, inbox and to-dos. One assistant to bring them together.',
        Icons.auto_awesome_rounded,
      ),
      OnboardingStep.name => (
        'A LITTLE INTRODUCTION',
        'What should Jarvis call you?',
        'Choose the name you’d like to see in Jarvis.',
        Icons.person_outline_rounded,
      ),
      OnboardingStep.llm => (
        'THE MIND',
        'Make it thoughtful.',
        'Connect an AI provider so Jarvis can think, plan and help you get things done.',
        Icons.auto_awesome_outlined,
      ),
      OnboardingStep.deepgram => (
        'THE VOICE',
        'Let’s talk.',
        'Add Deepgram so Jarvis can hear you and answer out loud.',
        Icons.graphic_eq_rounded,
      ),
      OnboardingStep.tavily => (
        'THE CURIOSITY',
        'A little more world.',
        'Give Jarvis web search with Tavily, for answers beyond your day-to-day.',
        Icons.travel_explore_rounded,
      ),
      OnboardingStep.ready => (
        'ALL CONNECTED',
        'Ready for your day.',
        'Your connections are in place. Jarvis is ready when you are.',
        Icons.check_rounded,
      ),
    };
    final localTheme = Theme.of(context).copyWith(
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: darkPill.withValues(alpha: 0.28),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 16,
          vertical: 18,
        ),
        labelStyle: const TextStyle(color: mutedText),
        floatingLabelStyle: const TextStyle(color: amber),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(color: cream.withValues(alpha: 0.16)),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(color: cream.withValues(alpha: 0.16)),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: amber, width: 1.5),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size.fromHeight(52),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(18),
          ),
          textStyle: const TextStyle(
            fontFamily: 'InstrumentSans',
            fontSize: 15,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
      expansionTileTheme: const ExpansionTileThemeData(
        shape: Border(),
        collapsedShape: Border(),
        iconColor: mutedText,
        collapsedIconColor: mutedText,
      ),
    );
    return PopScope(
      canPop: !_busy,
      child: Scaffold(
        bottomNavigationBar: ColoredBox(
          color: cocoaBottom,
          child: SafeArea(
            top: false,
            child: Align(
              heightFactor: 1,
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 560),
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(24, 16, 24, 16),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      if (_step == OnboardingStep.google)
                        _primary('Continue to Google', _signIn)
                      else if (_step == OnboardingStep.name)
                        _primary('Continue', _saveName)
                      else if (_step == OnboardingStep.ready)
                        _primary(
                          'Start using Jarvis',
                          () => _continue(finish: true),
                        )
                      else
                        _primary(
                          'Continue',
                          _setup[name] == true ? () => _continue() : null,
                        ),
                      if (_step.index > OnboardingStep.llm.index)
                        TextButton(
                          onPressed: _busy
                              ? null
                              : () => setState(
                                  () => _step =
                                      OnboardingStep.values[_step.index - 1],
                                ),
                          child: const Text('Back'),
                        ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
        body: CocoaBackground(
          child: SafeArea(
            child: Theme(
              data: localTheme,
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 560),
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.fromLTRB(24, 20, 24, 32),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Wrap(
                          alignment: WrapAlignment.spaceBetween,
                          spacing: 16,
                          runSpacing: 12,
                          children: [
                            Text('JARVIS', style: logoStyle(21)),
                            const Text(
                              'Set up Jarvis',
                              style: TextStyle(color: mutedText, fontSize: 12),
                            ),
                          ],
                        ),
                        const SizedBox(height: 32),
                        _progress(),
                        if (widget.error != null)
                          Padding(
                            padding: const EdgeInsets.only(top: 16),
                            child: Text(
                              widget.error!,
                              style: const TextStyle(color: danger),
                            ),
                          ),
                        if (_error != null)
                          Padding(
                            padding: const EdgeInsets.only(top: 16),
                            child: Semantics(
                              liveRegion: true,
                              child: Text(
                                _error!,
                                style: const TextStyle(color: danger),
                              ),
                            ),
                          ),
                        if (_error != null &&
                            widget.api != null &&
                            _step != OnboardingStep.name)
                          TextButton(
                            onPressed: _busy ? null : () => _continue(),
                            child: const Text('Retry setup check'),
                          ),
                        if (widget.error != null || _opened)
                          Padding(
                            padding: const EdgeInsets.only(top: 12),
                            child: const Text(
                              rejectionHint,
                              style: TextStyle(color: mutedText),
                            ),
                          ),

                        const SizedBox(height: 28),
                        Align(
                          alignment: Alignment.centerLeft,
                          child: Container(
                            padding: const EdgeInsets.all(14),
                            decoration: BoxDecoration(
                              color: amber.withValues(alpha: 0.12),
                              borderRadius: BorderRadius.circular(20),
                            ),
                            child: Icon(icon, color: amber, size: 28),
                          ),
                        ),
                        const SizedBox(height: 20),
                        Text(
                          eyebrow,
                          style: const TextStyle(
                            color: mutedText,
                            fontSize: 10,
                            letterSpacing: 1.7,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text(title, style: condensed(48, color: cream)),
                        const SizedBox(height: 12),
                        Text(
                          subtitle,
                          style: const TextStyle(
                            color: cream,
                            fontSize: 15,
                            height: 1.6,
                          ),
                        ),
                        const SizedBox(height: 24),
                        if (_step == OnboardingStep.google) ...[
                          _googleIntro(),
                          const SizedBox(height: 20),
                          const Text(
                            'Use the Google account Jarvis should look after. Only people the owner has added can sign in.',
                            style: TextStyle(
                              color: mutedText,
                              fontSize: 13,
                              height: 1.5,
                            ),
                          ),
                          const SizedBox(height: 12),
                          ExpansionTile(
                            tilePadding: EdgeInsets.zero,
                            title: const Text(
                              'Before you connect',
                              style: TextStyle(color: cream, fontSize: 14),
                            ),
                            children: const [
                              Padding(
                                padding: EdgeInsets.only(bottom: 16),
                                child: Text(
                                  'Google will warn that it "hasn\'t verified this app". That is expected for a private Jarvis: '
                                  'tap Advanced, then Continue. Then allow Calendar, Tasks and Gmail access. '
                                  'When Google is done you come straight back here.',
                                  style: TextStyle(
                                    color: mutedText,
                                    height: 1.6,
                                  ),
                                ),
                              ),
                            ],
                          ),
                          if (_opened)
                            const Text(
                              'If you cancelled or Google refused access, try signing in again.',
                              style: TextStyle(color: mutedText, height: 1.5),
                            ),
                          const SizedBox(height: 16),
                        ] else if (_step == OnboardingStep.name) ...[
                          TextField(
                            controller: _name,
                            enabled: !_busy,
                            decoration: const InputDecoration(
                              labelText: 'Your name',
                            ),
                            textCapitalization: TextCapitalization.words,
                            autofillHints: const [AutofillHints.name],
                            textInputAction: TextInputAction.done,
                            onSubmitted: (_) {
                              if (!_busy) _saveName();
                            },
                          ),
                        ] else if (_step == OnboardingStep.ready) ...[
                          _readySummary(),
                          const SizedBox(height: 24),
                        ] else ...[
                          _panel(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: [
                                Text(
                                  _labels[_step.index],
                                  style: const TextStyle(
                                    color: cream,
                                    fontSize: 18,
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                                const SizedBox(height: 16),
                                if (_setup[name] == true)
                                  Padding(
                                    padding: const EdgeInsets.only(bottom: 16),
                                    child: const Text(
                                      '•••• Saved. You can replace this key below.',
                                      style: TextStyle(color: cream),
                                    ),
                                  ),
                                KeyedSubtree(
                                  key: ValueKey(_step),
                                  child: _form(),
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(height: 20),
                        ],
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
