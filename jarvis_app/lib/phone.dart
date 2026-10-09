import 'package:android_intent_plus/android_intent.dart';
import 'package:flutter_contacts/flutter_contacts.dart';

import 'actions.dart';
import 'session.dart';

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

  @override
  Future<String?> run(Map<String, dynamic> action) async {
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
