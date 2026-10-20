import 'package:flutter/material.dart';

import 'api.dart';
import 'keys_screen.dart';
import 'theme.dart';
import 'calendar_screen.dart' show ScreenShell;

enum OnboardingStep { signIn, google, llm, optional }

const rejectionHint = 'Not on the list? Ask the owner to add your email.';

/// First-run wizard: sign in, connect Google, LLM key (required), Deepgram/Tavily (optional).
/// The sign-in steps run before there is a token; the app rebuilds this at [OnboardingStep.llm] once it has one.
class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen(
      {super.key, this.step = OnboardingStep.signIn, required this.signIn, this.api, this.error, required this.onDone});
  final OnboardingStep step;
  final Future<void> Function() signIn; // starts the sign-in and opens Google in the browser
  final ApiClient? api; // signed-in client, for the key steps
  final String? error; // e.g. a failed claim
  final VoidCallback onDone;
  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  late OnboardingStep _step = widget.step;
  bool _llmSaved = false, _opened = false, _busy = false;
  String? _startError;

  Future<void> _signIn() async {
    setState(() {
      _busy = true;
      _startError = null;
    });
    try {
      await widget.signIn();
      _opened = true; // back here without a token: Google refused or the user cancelled
    } catch (_) {
      _startError = "Couldn't start Google sign-in. Check your connection and try again.";
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _primary(String label, VoidCallback? onTap) => FilledButton(
      style: FilledButton.styleFrom(backgroundColor: amber, foregroundColor: amberInk), onPressed: onTap, child: Text(label));

  List<Widget> _signInProblems() => [
        for (final e in [widget.error, _startError])
          if (e != null) Text(e, style: const TextStyle(color: danger)),
        if (widget.error != null || _opened) const Text(rejectionHint, style: TextStyle(color: mutedText)),
      ];

  (String, List<Widget>) _page() => switch (_step) {
        OnboardingStep.signIn => (
            'Sign in with Google',
            [
              const Text('Jarvis is private: only people the owner has added can sign in. Use the Google account '
                  'whose calendar, tasks and email Jarvis should look after.', style: TextStyle(color: cream)),
              ..._signInProblems(),
              _primary('Sign in with Google', () => setState(() => _step = OnboardingStep.google)),
            ]
          ),
        OnboardingStep.google => (
            'Connect Google',
            [
              const Text('Google will warn that it "hasn\'t verified this app". That is expected for a private Jarvis: '
                  'tap Advanced, then Continue. Then allow calendar, tasks and Gmail access. '
                  'When Google is done you come straight back here.', style: TextStyle(color: cream)),
              ..._signInProblems(),
              _primary(_busy ? 'Opening…' : 'Continue to Google', _busy ? null : _signIn),
            ]
          ),
        OnboardingStep.llm => (
            'Your AI key',
            [
              LlmForm(api: widget.api!, onSaved: () => setState(() => _llmSaved = true)),
              _primary('Continue', _llmSaved ? () => setState(() => _step = OnboardingStep.optional) : null),
            ]
          ),
        OnboardingStep.optional => (
            'Voice and web search',
            [
              const Text('Optional. You can add these later in Settings > API keys.', style: TextStyle(color: cream)),
              KeyField(api: widget.api!, name: 'deepgram', label: 'Deepgram', howTo: deepgramHowTo),
              KeyField(api: widget.api!, name: 'tavily', label: 'Tavily', howTo: tavilyHowTo),
              Row(children: [
                Expanded(child: TextButton(onPressed: widget.onDone, child: const Text('Skip'))),
                Expanded(child: _primary('Finish', widget.onDone)),
              ]),
            ]
          ),
      };

  @override
  Widget build(BuildContext context) {
    final (title, children) = _page();
    return ScreenShell(
      title: 'Set up Jarvis',
      child: ListView(padding: const EdgeInsets.all(24), children: [
            Text('Step ${_step.index + 1} of 4', style: const TextStyle(color: mutedText, fontSize: 12)),
            const SizedBox(height: 4),
            Text(title, style: Theme.of(context).textTheme.headlineMedium?.copyWith(color: cream)),
            const SizedBox(height: 16),
            for (final c in children) Padding(padding: const EdgeInsets.only(bottom: 12), child: c),
          ]),
    );
  }
}
