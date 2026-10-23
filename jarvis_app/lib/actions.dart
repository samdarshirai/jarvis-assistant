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

class ContactMatch {
  const ContactMatch(this.id, this.name, this.numbers);
  final String id, name;
  final List<String> numbers;
  String get last4 {
    final d = _digits(numbers.first);
    return d.length > 4 ? d.substring(d.length - 4) : d;
  }
}

String _digits(String s) => s.replaceAll(RegExp(r'\D'), '');

String normalizeNumber(String raw) {
  final d = _digits(raw);
  return d.length > 10 ? d.substring(d.length - 10) : d;
}

/// Contacts for a spoken name (same tiers as [pickContact]), narrowed by last 4 digits, duplicates collapsed.
List<ContactMatch> matchContacts(String name, List<ContactMatch> contacts, {String? last4}) {
  final n = name.trim().toLowerCase();
  if (n.isEmpty) return [];
  final withNumbers = contacts.where((c) => c.numbers.isNotEmpty).toList();
  var hits = <ContactMatch>[];
  for (final test in <bool Function(String)>[(s) => s == n, (s) => s.startsWith(n), (s) => s.contains(n)]) {
    hits = withNumbers.where((c) => test(c.name.toLowerCase())).toList();
    if (hits.isNotEmpty) break;
  }
  final seen = <String>{};
  final out = <ContactMatch>[];
  for (final c in hits) {
    if (last4 != null && !c.numbers.any((x) => _digits(x).endsWith(last4))) continue;
    final norm = c.numbers.map(normalizeNumber).toSet();
    if (seen.any(norm.contains)) continue;
    seen.addAll(norm);
    out.add(c);
  }
  return out;
}

IntentSpec? buildCallIntent(String number, {required bool canCall}) =>
    IntentSpec(action: canCall ? 'android.intent.action.CALL' : 'android.intent.action.DIAL', data: 'tel:$number');
