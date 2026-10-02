class IntentSpec {
  const IntentSpec({required this.action, this.data, this.package, this.arguments});
  final String action;
  final String? data, package;
  final Map<String, dynamic>? arguments;
}

IntentSpec? buildIntent(Map<String, dynamic> a, String? phone) {
  try {
    switch (a['type']) {
      case 'set_alarm':
        return IntentSpec(action: 'android.intent.action.SET_ALARM', arguments: {
          'android.intent.extra.alarm.HOUR': a['hour'] as int,
          'android.intent.extra.alarm.MINUTES': a['minute'] as int,
          'android.intent.extra.alarm.SKIP_UI': true,
          if (a['label'] != null) 'android.intent.extra.alarm.MESSAGE': a['label'] as String,
        });
      case 'set_timer':
        return IntentSpec(action: 'android.intent.action.SET_TIMER', arguments: {
          'android.intent.extra.alarm.LENGTH': a['seconds'] as int,
          'android.intent.extra.alarm.SKIP_UI': true,
          if (a['label'] != null) 'android.intent.extra.alarm.MESSAGE': a['label'] as String,
        });
      case 'start_navigation':
        return IntentSpec(
            action: 'android.intent.action.VIEW',
            data: 'google.navigation:q=${Uri.encodeComponent(a['destination'] as String)}',
            package: 'com.google.android.apps.maps');
      case 'compose_message':
        if (phone == null) return null;
        final text = a['text'] as String;
        if (a['app'] == 'whatsapp') {
          final digits = phone.replaceAll(RegExp(r'\D'), '');
          return IntentSpec(
              action: 'android.intent.action.VIEW',
              data: 'https://wa.me/$digits?text=${Uri.encodeComponent(text)}',
              package: 'com.whatsapp');
        }
        return IntentSpec(
            action: 'android.intent.action.SENDTO', data: 'smsto:$phone', arguments: {'sms_body': text});
      default:
        return null;
    }
  } catch (_) {
    return null; // missing or wrongly typed fields
  }
}

/// Best contact for a spoken name: exact, then prefix, then substring (case-insensitive). Returns the phone number.
String? pickContact(String name, List<(String, String)> contacts) {
  final n = name.trim().toLowerCase();
  if (n.isEmpty) return null;
  for (final test in <bool Function(String)>[(s) => s == n, (s) => s.startsWith(n), (s) => s.contains(n)]) {
    for (final (display, phone) in contacts) {
      if (phone.trim().isNotEmpty && test(display.toLowerCase())) return phone;
    }
  }
  return null;
}
