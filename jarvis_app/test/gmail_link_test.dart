import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/phone.dart';

void main() {
  test('gmailUri', () {
    expect(gmailUri('abc_1-F').toString(), 'https://mail.google.com/mail/u/0/#inbox/abc_1-F');
    for (final bad in [null, '', 'a/b', 'x y', '../']) {
      expect(gmailUri(bad).toString(), 'https://mail.google.com/mail/u/0/#inbox');
    }
  });

  test('fallback order, swallows final failure', () async {
    final calls = <String>[];
    await openEmailInGmail('abc', launch: (u, {package}) async {
      calls.add('${u.fragment}|$package');
      throw StateError('no');
    });
    expect(calls, ['inbox/abc|com.google.android.gm', 'inbox|com.google.android.gm', 'inbox|null']);
  });

  test('stops at first success', () async {
    final calls = <String>[];
    await openEmailInGmail('abc', launch: (u, {package}) async {
      calls.add(u.fragment);
      if (calls.length == 1) throw StateError('no');
    });
    expect(calls, ['inbox/abc', 'inbox']);
  });
}
