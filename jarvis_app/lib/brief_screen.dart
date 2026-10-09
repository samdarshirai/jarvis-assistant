import 'dart:async';

import 'package:flutter/material.dart';

import 'theme.dart';

// TTS gives no playback position, so timing is estimated from character count.
const _charsPerSec = 15.0;

List<String> splitSentences(String s) =>
    [for (final m in RegExp(r'[^.?!]*[.?!]+(?:\s+|$)|[^.?!]+$').allMatches(s)) m.group(0)!.trim()]
        .where((e) => e.isNotEmpty)
        .toList();

Duration _est(String s) => Duration(milliseconds: (s.length / _charsPerSec * 1000).round());

String _mmss(Duration d) =>
    '${d.inMinutes}:${(d.inSeconds % 60).toString().padLeft(2, '0')}';

class BriefScreen extends StatefulWidget {
  const BriefScreen({super.key, required this.brief, required this.speak, required this.stop});
  final String brief;
  final void Function(String text) speak;
  final VoidCallback stop;

  @override
  State<BriefScreen> createState() => _BriefScreenState();
}

class _BriefScreenState extends State<BriefScreen> {
  late final List<String> _s = splitSentences(widget.brief);
  int _i = 0;
  bool _playing = false;
  Timer? _timer;
  DateTime _sentStart = DateTime.now();

  void _playFrom(int i) {
    _timer?.cancel();
    setState(() {
      _i = i;
      _playing = true;
    });
    widget.speak(_s.skip(i).join(' '));
    _arm();
  }

  void _arm() {
    _sentStart = DateTime.now();
    _timer = Timer(_est(_s[_i]), () {
      if (_i + 1 >= _s.length) {
        setState(() => _playing = false);
      } else {
        setState(() => _i++);
        _arm();
      }
    });
  }

  // ponytail: position is estimated like the progress bar; lands on a sentence start, not mid-sentence
  void _back10() {
    if (_s.isEmpty) return;
    final inSent = _playing ? DateTime.now().difference(_sentStart) : Duration.zero;
    final target = _span(0, _i) + inSent - const Duration(seconds: 10);
    var k = 0;
    while (k < _s.length - 1 && _span(0, k + 1) <= target) {
      k++;
    }
    _playFrom(target <= Duration.zero ? 0 : k);
  }

  void _pause() {
    _timer?.cancel();
    widget.stop();
    setState(() => _playing = false);
  }

  @override
  void dispose() {
    _timer?.cancel();
    widget.stop(); // end the speak session even after playback finished, else back lands on the voice view
    super.dispose();
  }

  Duration _span(int from, int to) => _est(_s.sublist(from, to).join(' '));

  @override
  Widget build(BuildContext context) {
    final total = _est(_s.join(' '));
    final done = _span(0, _i);
    return Scaffold(
      appBar: AppBar(title: const Text('Morning brief')),
      body: CocoaBackground(
        child: SafeArea(
          child: Column(children: [
            Expanded(
              child: ListView(padding: const EdgeInsets.all(20), children: [
                for (var k = 0; k < _s.length; k++)
                  GestureDetector(
                    onTap: () {
                      widget.stop();
                      _playFrom(k);
                    },
                    child: Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(_s[k],
                          style: TextStyle(
                              fontSize: 24,
                              height: 1.3,
                              color: k == _i ? blush : (k < _i ? cream.withValues(alpha: 0.5) : cream.withValues(alpha: 0.25)))),
                    ),
                  ),
              ]),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
              child: Column(children: [
                LinearProgressIndicator(
                    value: total.inMilliseconds == 0 ? 0 : done.inMilliseconds / total.inMilliseconds,
                    color: amber,
                    backgroundColor: Colors.white12),
                Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                  Text(_mmss(done), style: const TextStyle(color: mutedText)),
                  Text(_mmss(total), style: const TextStyle(color: mutedText)),
                ]),
                const SizedBox(height: 8),
                Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                  IconButton(
                      iconSize: 36,
                      color: cream,
                      tooltip: 'Back 10 seconds',
                      onPressed: _s.isEmpty ? null : _back10,
                      icon: const Icon(Icons.replay_10)),
                  const SizedBox(width: 20),
                  GestureDetector(
                    onTap: _s.isEmpty ? null : (_playing ? _pause : () => _playFrom(_i)),
                    child: Container(
                      width: 64,
                      height: 64,
                      decoration: const BoxDecoration(shape: BoxShape.circle, gradient: amberGradient),
                      child: Icon(_playing ? Icons.pause : Icons.play_arrow, color: amberInk, size: 32),
                    ),
                  ),
                  const SizedBox(width: 56), // balances the replay button so play stays centred
                ]),
              ]),
            ),
          ]),
        ),
      ),
    );
  }
}
