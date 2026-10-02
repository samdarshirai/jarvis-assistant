import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/actions.dart';

void main() {
  test('alarm', () {
    final i = buildIntent({'type': 'set_alarm', 'hour': 6, 'minute': 5, 'label': 'Gym'}, null)!;
    expect(i.action, 'android.intent.action.SET_ALARM');
    expect(i.arguments, {
      'android.intent.extra.alarm.HOUR': 6,
      'android.intent.extra.alarm.MINUTES': 5,
      'android.intent.extra.alarm.SKIP_UI': true,
      'android.intent.extra.alarm.MESSAGE': 'Gym',
    });
  });

  test('alarm without label has no message extra', () {
    final i = buildIntent({'type': 'set_alarm', 'hour': 6, 'minute': 0}, null)!;
    expect(i.arguments!.containsKey('android.intent.extra.alarm.MESSAGE'), isFalse);
  });

  test('timer', () {
    final i = buildIntent({'type': 'set_timer', 'seconds': 300}, null)!;
    expect(i.action, 'android.intent.action.SET_TIMER');
    expect(i.arguments, {'android.intent.extra.alarm.LENGTH': 300, 'android.intent.extra.alarm.SKIP_UI': true});
  });

  test('navigation uses the maps uri and package', () {
    final i = buildIntent({'type': 'start_navigation', 'destination': 'Marienplatz München'}, null)!;
    expect(i.data, 'google.navigation:q=Marienplatz%20M%C3%BCnchen');
    expect((i.action, i.package), ('android.intent.action.VIEW', 'com.google.android.apps.maps'));
  });

  test('whatsapp compose is a prefilled wa.me link with digits only', () {
    final i = buildIntent({'type': 'compose_message', 'app': 'whatsapp', 'contact': 'Anna', 'text': '10 min late'},
        '+49 151 234-567')!;
    expect(i.data, 'https://wa.me/49151234567?text=10%20min%20late');
    expect(i.package, 'com.whatsapp');
  });

  test('sms compose is SENDTO with sms_body', () {
    final i = buildIntent({'type': 'compose_message', 'app': 'sms', 'contact': 'Anna', 'text': 'hi'}, '+49151234567')!;
    expect((i.action, i.data), ('android.intent.action.SENDTO', 'smsto:+49151234567'));
    expect(i.arguments, {'sms_body': 'hi'});
  });

  test('null label is treated as absent', () {
    for (final a in [
      {'type': 'set_alarm', 'hour': 6, 'minute': 0, 'label': null},
      {'type': 'set_timer', 'seconds': 60, 'label': null},
    ]) {
      expect(buildIntent(a, null)!.arguments!.containsKey('android.intent.extra.alarm.MESSAGE'), isFalse);
    }
  });

  test('unknown or malformed actions build nothing', () {
    expect(buildIntent({'type': 'launch_missiles'}, null), isNull);
    expect(buildIntent({'type': 'set_alarm'}, null), isNull);
    expect(buildIntent({'type': 'compose_message', 'app': 'sms', 'contact': 'A', 'text': 'x'}, null), isNull);
  });

  group('pickContact', () {
    const people = [('Anna Schmidt', '111'), ('Annabel', '222'), ('Raj', '333'), ('Joanna Lee', '444')];
    test('exact beats prefix beats substring, case-insensitive', () {
      expect(pickContact('raj', people), '333');
      expect(pickContact('anna', people), '111'); // prefix of "Anna Schmidt" (first prefix match)
      expect(pickContact('lee', people), '444');
    });
    test('exact beats earlier prefix', () => expect(pickContact('anna', [('Annabel', '1'), ('Anna', '2')]), '2'));
    test('blank numbers are skipped', () {
      expect(pickContact('anna', [('Anna', ''), ('Anna Schmidt', '111')]), '111');
      expect(pickContact('anna', [('Anna', '  ')]), isNull);
    });
    test('no match is null', () => expect(pickContact('zed', people), isNull));
  });
}
