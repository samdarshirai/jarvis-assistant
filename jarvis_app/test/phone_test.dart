import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/actions.dart';
import 'package:jarvis_app/phone.dart';

const _mom = ContactMatch('1', 'Mom', ['+1 555 000 4521']);
const _momWork = ContactMatch('2', 'Mom', ['+1 555 000 9911']);

void main() {
  late List<IntentSpec> launched;
  late List<Map<String, dynamic>> reports;
  late List<String> waCalls;

  Future<String?> call(
    List<ContactMatch>? contacts, {
    Map<String, dynamic> action = const {'type': 'call_contact', 'contact': 'Mom'},
    bool wa = false,
    bool canCall = true,
    bool throws = false,
    bool report = true,
  }) =>
      runCall(action,
          contacts: () async => contacts,
          whatsapp: (id) async {
            waCalls.add(id);
            return wa;
          },
          launch: (s) async {
            if (throws) throw StateError('boom');
            launched.add(s);
          },
          canCall: () async => canCall,
          report: report ? reports.add : null);

  setUp(() {
    launched = [];
    reports = [];
    waCalls = [];
  });

  test('one match on WhatsApp launches nothing else and reports nothing', () async {
    expect(await call([_mom], wa: true), isNull);
    expect(waCalls, ['1']);
    expect(launched, isEmpty);
    expect(reports, isEmpty);
  });

  test('WhatsApp failing falls back to CALL when permitted', () async {
    expect(await call([_mom]), isNull);
    expect(launched.single.action, 'android.intent.action.CALL');
    expect(launched.single.data, 'tel:+1 555 000 4521');
    expect(reports, isEmpty);
  });

  test('WhatsApp failing falls back to DIAL without permission', () async {
    await call([_mom], canCall: false);
    expect(launched.single.action, 'android.intent.action.DIAL');
  });

  test('several matches report name and last4 only and launch nothing', () async {
    expect(await call([_mom, _momWork]), isNull);
    expect(launched, isEmpty);
    expect(waCalls, isEmpty);
    expect(reports.single, {
      'action': 'call_contact',
      'status': 'ambiguous',
      'contact': 'Mom',
      'matches': [
        {'name': 'Mom', 'last4': '4521'},
        {'name': 'Mom', 'last4': '9911'},
      ],
    });
  });

  test('last4 narrows to one and calls (null last4 is unset)', () async {
    await call([_mom, _momWork], action: {'type': 'call_contact', 'contact': 'Mom', 'last4': '9911'});
    expect(waCalls, ['2']);
    waCalls.clear();
    await call([_mom], action: {'type': 'call_contact', 'contact': 'Mom', 'last4': null});
    expect(waCalls, ['1']);
  });

  test('last4 dials the number the user named', () async {
    const two = ContactMatch('3', 'Mom', ['+1 555 000 2222', '+1 555 000 3333']);
    await call([two], action: {'type': 'call_contact', 'contact': 'Mom', 'last4': '3333'});
    expect(launched.single.data, 'tel:+1 555 000 3333');
    expect(launched.single.action, 'android.intent.action.CALL');
  });

  test('a throwing permission request falls back to DIAL, not error', () async {
    final out = await runCall({'type': 'call_contact', 'contact': 'Mom'},
        contacts: () async => [_mom],
        whatsapp: (_) async => false,
        launch: (s) async => launched.add(s),
        canCall: () async => throw StateError('request running'),
        report: reports.add);
    expect(out, isNull);
    expect(reports, isEmpty);
    expect(launched.single.action, 'android.intent.action.DIAL');
  });

  test('contactMatch drops blank numbers', () {
    final c = contactMatch('9', 'Mom', ['  ', '+1 555 000 4521']);
    expect(c.numbers, ['+1 555 000 4521']);
    expect(c.last4, '4521');
    expect(contactMatch('9', 'Mom', [' ']).numbers, isEmpty);
  });

  test('no match reports not_found', () async {
    await call([_momWork], action: {'type': 'call_contact', 'contact': 'Zed'});
    expect(reports.single['status'], 'not_found');
    expect(reports.single['contact'], 'Zed');
    expect(launched, isEmpty);
  });

  test('unreadable contacts report no_permission', () async {
    await call(null);
    expect(reports.single['status'], 'no_permission');
  });

  test('a throwing launch reports error', () async {
    expect(await call([_mom], throws: true), isNull);
    expect(reports.single['status'], 'error');
  });

  test('without report, problems come back as plain strings', () async {
    expect(await call([_mom, _momWork], report: false), 'Several contacts match Mom.');
    expect(await call([], report: false), 'No contact named Mom.');
    expect(await call(null, report: false), 'Contacts permission is off.');
    expect(await call([_mom], throws: true, report: false), startsWith('Could not call Mom'));
  });
}
