import 'dart:convert';
import 'dart:typed_data';

sealed class ServerEvent {
  const ServerEvent();
}

class StateEvent extends ServerEvent {
  const StateEvent(this.state);
  final String state;
}

class TranscriptEvent extends ServerEvent {
  const TranscriptEvent(this.role, this.text, this.isFinal);
  final String role, text;
  final bool isFinal;
}

class ConfirmCardEvent extends ServerEvent {
  const ConfirmCardEvent(
      {required this.interruptId, required this.summary, required this.tapOnly, required this.afterUntrusted});
  final String interruptId, summary;
  final bool tapOnly, afterUntrusted;
}

class ClientActionsEvent extends ServerEvent {
  const ClientActionsEvent(this.actions);
  final List<Map<String, dynamic>> actions;
}

class ErrorEvent extends ServerEvent {
  const ErrorEvent(this.message, {this.needsSetup = false});
  final String message;
  final bool needsSetup; // a missing key (backend then closes with 4403): the session cannot work until it is added
}

/// Backend `{"type":"error","code":...}` before a 4403 close, in plain words.
const setupMessages = {
  'needs_deepgram': 'Add your Deepgram key in Settings to enable voice',
  'needs_llm': 'Add your AI key in Settings to talk to Jarvis',
};

class AudioEvent extends ServerEvent {
  const AudioEvent(this.pcm);
  final Uint8List pcm;
}

ServerEvent? decodeServer(Object? raw) {
  try {
    if (raw is Uint8List) return AudioEvent(raw);
    if (raw is List<int>) return AudioEvent(Uint8List.fromList(raw));
    if (raw is! String) return null;
    final j = jsonDecode(raw);
    if (j is! Map<String, dynamic>) return null;
    switch (j['type']) {
      case 'state':
        return StateEvent(j['state'] as String);
      case 'transcript':
        return TranscriptEvent(j['role'] as String, j['text'] as String, j['final'] as bool);
      case 'confirm_card':
        return ConfirmCardEvent(
            interruptId: j['interrupt_id'] as String,
            summary: j['summary'] as String,
            tapOnly: j['tap_only'] as bool,
            afterUntrusted: j['after_untrusted'] as bool);
      case 'client_actions':
        return ClientActionsEvent(List<Map<String, dynamic>>.from(j['actions'] as List));
      case 'error':
        final setup = setupMessages[j['code']];
        return setup != null ? ErrorEvent(setup, needsSetup: true) : ErrorEvent(j['message'] as String);
      default:
        return null;
    }
  } catch (_) {
    return null; // a malformed frame must never crash the session
  }
}

String encode(String type, [Map<String, dynamic> fields = const {}]) => jsonEncode({'type': type, ...fields});
