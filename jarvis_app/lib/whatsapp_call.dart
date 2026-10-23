import 'package:flutter/services.dart';

const _channel = MethodChannel('jarvis/whatsapp');

/// Starts a WhatsApp voice call to the contact with Android contact id [contactId].
/// False when WhatsApp is missing, the contact has no call row, or the launch fails; never throws.
Future<bool> callOnWhatsApp(String contactId) async {
  try {
    return await _channel.invokeMethod<bool>('call', contactId) ?? false;
  } on PlatformException {
    return false;
  } on MissingPluginException {
    return false;
  }
}
