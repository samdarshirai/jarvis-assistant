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
        if (c.phones.isNotEmpty) (c.displayName ?? '', c.phones.first.number)
    ]);
  }

  @override
  Future<String?> run(Map<String, dynamic> action) async {
    String? phone;
    if (action['type'] == 'compose_message') {
      phone = await _phoneFor('${action['contact']}');
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
