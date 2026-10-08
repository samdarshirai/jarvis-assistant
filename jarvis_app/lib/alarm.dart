import 'package:flutter/services.dart';

const _channel = MethodChannel('jarvis/alarm');

/// The phone's next alarm (any app's), or null when none is set or it cannot be read.
Future<DateTime?> nextAlarm() async {
  try {
    final ms = await _channel.invokeMethod<int>('next');
    return ms == null ? null : DateTime.fromMillisecondsSinceEpoch(ms);
  } catch (_) {
    return null;
  }
}
