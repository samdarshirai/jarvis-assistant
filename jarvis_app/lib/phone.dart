import 'package:android_intent_plus/android_intent.dart';
import 'package:flutter_contacts/flutter_contacts.dart';
import 'package:permission_handler/permission_handler.dart' hide PermissionStatus;

import 'actions.dart';
import 'session.dart';
import 'whatsapp_call.dart';

class AndroidPhoneActions implements PhoneActions {
  /// Contact names are resolved here, on the phone, so the server never sees the address book.
  Future<String?> _phoneFor(String name) async {
    final status = await FlutterContacts.permissions.request(PermissionType.read);
    if (status != PermissionStatus.granted && status != PermissionStatus.limited) return null;
    final all = await FlutterContacts.getAll(properties: {ContactProperty.phone});
    return pickContact(name, [
      for (final c in all)
        if (c.phones.isNotEmpty && c.phones.first.number.trim().isNotEmpty) (c.displayName ?? '', c.phones.first.number)
    ]);
  }

  /// Every contact with all its numbers; null when contacts permission is not granted.
  Future<List<ContactMatch>?> _allContacts() async {
    final status = await FlutterContacts.permissions.request(PermissionType.read);
    if (status != PermissionStatus.granted && status != PermissionStatus.limited) return null;
    final all = await FlutterContacts.getAll(properties: {ContactProperty.phone});
    return [
      for (final c in all)
        if (c.id != null) contactMatch(c.id!, c.displayName ?? '', [for (final p in c.phones) p.number])
    ];
  }

  Future<void> _launch(IntentSpec spec) => AndroidIntent(
        action: spec.action,
        data: spec.data,
        package: spec.package,
        arguments: spec.arguments,
        flags: const [0x10000000], // FLAG_ACTIVITY_NEW_TASK
      ).launch();

  @override
  Future<String?> run(Map<String, dynamic> action, {void Function(Map<String, dynamic> result)? report}) async {
    if (action['type'] == 'call_contact') {
      return runCall(action,
          contacts: _allContacts,
          whatsapp: callOnWhatsApp,
          launch: _launch,
          canCall: () async => (await Permission.phone.request()).isGranted,
          report: report);
    }
    String? phone;
    if (action['type'] == 'compose_message') {
      if (action['contact'] is! String ||
          action['text'] is! String ||
          (action['app'] != 'whatsapp' && action['app'] != 'sms')) {
        return 'Unsupported phone action: compose_message.';
      }
      try {
        phone = await _phoneFor(action['contact'] as String);
      } catch (e) {
        return 'Could not read contacts: $e';
      }
      if (phone == null) return 'No contact named ${action['contact']}.';
    }
    final spec = buildIntent(action, phone);
    if (spec == null) return 'Unsupported phone action: ${action['type']}.';
    try {
      await AndroidIntent(
        action: spec.action,
        data: spec.data,
        package: spec.package,
        arguments: spec.arguments,
        flags: const [0x10000000], // FLAG_ACTIVITY_NEW_TASK
      ).launch();
      return null;
    } catch (e) {
      return 'Could not run ${action['type']}: $e';
    }
  }
}

/// A [ContactMatch] without blank numbers, so a blank first number never becomes an empty `tel:` or last4.
ContactMatch contactMatch(String id, String name, Iterable<String> numbers) =>
    ContactMatch(id, name, [for (final n in numbers) if (n.trim().isNotEmpty) n]);

/// Calls the contact: WhatsApp first, then the phone. Success reports nothing; every other outcome goes to
/// [report] (only name and last 4 digits leave the phone), or comes back as an error string without one.
Future<String?> runCall(
  Map<String, dynamic> action, {
  required Future<List<ContactMatch>?> Function() contacts,
  required Future<bool> Function(String contactId) whatsapp,
  required Future<void> Function(IntentSpec) launch,
  required Future<bool> Function() canCall,
  void Function(Map<String, dynamic>)? report,
}) async {
  final name = action['contact'];
  if (name is! String) return 'Unsupported phone action: call_contact.';
  String? outcome(String status, String message, [List<Map<String, String>> matches = const []]) {
    if (report == null) return message;
    report({'action': 'call_contact', 'status': status, 'contact': name, 'matches': matches});
    return null;
  }

  try {
    final all = await contacts();
    if (all == null) return outcome('no_permission', 'Contacts permission is off.');
    final last4 = action['last4'];
    final hits = matchContacts(name, all, last4: last4 is String ? last4 : null);
    if (hits.isEmpty) return outcome('not_found', 'No contact named $name.');
    if (hits.length > 1) {
      return outcome('ambiguous', 'Several contacts match $name.', [
        for (final h in hits) {'name': h.name, 'last4': h.last4}
      ]);
    }
    final hit = hits.single;
    if (await whatsapp(hit.id)) return null;
    final number = last4 is String
        ? hit.numbers.firstWhere((n) => n.replaceAll(RegExp(r'\D'), '').endsWith(last4), orElse: () => hit.numbers.first)
        : hit.numbers.first;
    var allowed = false;
    try {
      allowed = await canCall();
    } catch (_) {} // a permission prompt that fails just means the dialer
    await launch(buildCallIntent(number, canCall: allowed)!);
    return null;
  } catch (e) {
    return outcome('error', 'Could not call $name: $e');
  }
}

/// Gmail web/app deep link for a message; falls back to the inbox for a missing or unsafe id.
Uri gmailUri(String? messageId) {
  final ok = messageId != null && RegExp(r'^[A-Za-z0-9_-]+$').hasMatch(messageId);
  return Uri.parse('https://mail.google.com/mail/u/0/#inbox${ok ? '/$messageId' : ''}');
}

Future<void> _launchIntent(Uri uri, {String? package}) => AndroidIntent(
      action: 'action_view',
      data: uri.toString(),
      package: package,
      flags: const [0x10000000], // FLAG_ACTIVITY_NEW_TASK
    ).launch();

/// Opens [uri] in the default browser.
Future<void> openInBrowser(Uri uri) => _launchIntent(uri);

const _gmailPackage = 'com.google.android.gm';

/// Tries Gmail with the message, then Gmail inbox, then any app on the inbox URL; never throws.
Future<void> openEmailInGmail(String? messageId,
    {Future<void> Function(Uri uri, {String? package}) launch = _launchIntent}) async {
  final attempts = <(Uri, String?)>[
    (gmailUri(messageId), _gmailPackage),
    (gmailUri(null), _gmailPackage),
    (gmailUri(null), null),
  ];
  for (final (uri, pkg) in attempts) {
    try {
      await launch(uri, package: pkg);
      return;
    } catch (_) {}
  }
}
