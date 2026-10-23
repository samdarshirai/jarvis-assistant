# Call a contact by name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Call Mom" places a WhatsApp voice call, falling back to a phone call, asking by last 4 digits when several contacts match.

**Architecture:** A new `call_contact` phone tool queues a client action. The Flutter app matches contacts on-device, launches WhatsApp's call row or the phone dialer, and for ambiguous / not-found / failed outcomes sends a new `action_result` frame. The server turns that frame into a short agent turn so Jarvis asks the question; the user's answer becomes a second `call_contact` carrying `last4`.

**Tech Stack:** Python (FastAPI, LangGraph, pytest), Flutter/Dart (`android_intent_plus`, `flutter_contacts`, `permission_handler`), Kotlin MethodChannel.

**Spec:** `docs/superpowers/specs/2026-10-23-call-contact-design.md`

## Global Constraints

- Voice calls only; Android only; regular WhatsApp (`com.whatsapp`).
- Always try WhatsApp first, then phone. The user never picks the channel.
- Server may see only matching contacts' display names and last 4 digits, and only when more than one contact matches. No full numbers.
- Ambiguity is spoken as last 4 digits.
- Jarvis never says a call was placed; it says it asked the phone.
- `compose_message` and `pickContact` behaviour stay unchanged.
- App builds use `--dart-define-from-file=jarvis.env.json`.
- New manifest permission: `android.permission.CALL_PHONE`, requested at first call; denied means `ACTION_DIAL`.

## Review Focus

- Contact whose name contains instruction text ("Mom ignore previous instructions"): server must sanitise names before the model sees them.
- Same person saved twice with the same number (two entries): one match, no question.
- One contact with mobile + home numbers: one match, not two.
- User's reply "the second one" or "ending 8830" after the question reaches the `phone` domain, not `chat`.
- `last4` that matches nothing or still several contacts: re-ask once, then give up.
- Contacts permission denied: Jarvis says so, does not ask for a respelling.
- Malformed `action_result` frame (wrong types, 1000 matches, non-digit last4): rejected with an error frame, no turn started.

---

### Task 1: WhatsApp call bridge and device spike

This is the gate for the whole feature. If the call row does not start a call on the owner's phone, stop and tell the owner; the rest then ships phone-only (skip `whatsapp` steps in Task 5).

**Files:**
- Modify: `jarvis_app/android/app/src/main/kotlin/com/jarvis/jarvis_app/MainActivity.kt`
- Create: `jarvis_app/lib/whatsapp_call.dart`

**Interfaces:**
- Produces: `Future<bool> callOnWhatsApp(String contactId)` in `whatsapp_call.dart`. True when WhatsApp's call screen was launched; false when WhatsApp is missing, the contact has no call row, or the launch throws. Never throws.

- [ ] **Step 1: Add a `jarvis/whatsapp` MethodChannel in `MainActivity.kt`** with method `call(contactId: String) -> Boolean`. It queries `ContactsContract.Data.CONTENT_URI` for `MIMETYPE = "vnd.android.cursor.item/vnd.com.whatsapp.voip.call"` and `CONTACT_ID = contactId`, takes the first row's `_ID`, and starts `Intent(ACTION_VIEW)` with data `content://com.android.contacts/data/<id>`, type = that MIME type, package `com.whatsapp`, `FLAG_ACTIVITY_NEW_TASK`. Returns false on no row, no WhatsApp, or any exception.
- [ ] **Step 2: Implement `callOnWhatsApp` in `whatsapp_call.dart`** as a thin wrapper over the channel, catching `PlatformException`/`MissingPluginException` to false.
- [ ] **Step 3: Temporary spike hook.** In a scratch change (not committed), call `callOnWhatsApp` for a known WhatsApp contact's `Contact.id` from `FlutterContacts.getAll(properties: {ContactProperty.phone})` on app start. Confirm on the device that (a) `Contact.id` works as `CONTACT_ID`, (b) WhatsApp starts a voice call, not a chat. If `Contact.id` is not the Android contact id, adjust the lookup (e.g. match through the phone number) and note the finding in the commit message. Remove the hook.
- [ ] **Step 4: Run `flutter analyze` in `jarvis_app`.** Expected: no new issues.
- [ ] **Step 5: Commit** `feat: native bridge that starts a WhatsApp voice call`. Report the spike result to the owner before continuing.

### Task 2: Contact matching (pure Dart)

**Files:**
- Modify: `jarvis_app/lib/actions.dart`
- Test: `jarvis_app/test/actions_test.dart`

**Interfaces:**
- Produces in `actions.dart`:
  - `class ContactMatch { const ContactMatch(this.id, this.name, this.numbers); final String id, name; final List<String> numbers; String get last4 }` where `last4` is the last four digits of `numbers.first`.
  - `String normalizeNumber(String raw)`: digits only, last 10 kept when longer.
  - `List<ContactMatch> matchContacts(String name, List<ContactMatch> contacts, {String? last4})`: same tiers as `pickContact` (exact, prefix, substring, case-insensitive; the first tier with any hit wins). Contacts with no numbers are ignored. If `last4` is given, contacts with no number ending in those digits are dropped. Contacts that share any normalised number collapse into the first one. Contact order is preserved.
  - `IntentSpec? buildCallIntent(String number, {required bool canCall})`: `android.intent.action.CALL` if `canCall` else `android.intent.action.DIAL`, data `tel:<number>`.

- [ ] **Step 1: Write failing tests** in `actions_test.dart`, one per case: single exact match returns one; two "Mom" with different numbers return two with `last4` `4521` and `8830`; same number saved under "Mom" and "Mom (old)" collapses to one; one contact with mobile and home is one match; `last4: '8830'` keeps only that contact and also matches via a second number; `last4` matching nothing returns empty; prefix beats substring ("Mo" with "Mom" and "Salmon" returns Mom only); empty name returns empty; `buildCallIntent('+4917012345678', canCall: true).action == 'android.intent.action.CALL'`, `canCall: false` gives `DIAL`, both with data `tel:+4917012345678`.
- [ ] **Step 2: Run** `cd jarvis_app && flutter test test/actions_test.dart`. Expected: FAIL (undefined symbols).
- [ ] **Step 3: Implement** the signatures above in `actions.dart`. Leave `pickContact` and `buildIntent` untouched.
- [ ] **Step 4: Run** the same command. Expected: PASS, including the existing tests.
- [ ] **Step 5: Commit** `feat: match contacts with all numbers for calling`.

### Task 3: `call_contact` tool and routing (server)

**Files:**
- Modify: `src/jarvis/tools/phone_tools.py`, `src/jarvis/agent/graph.py` (`KEYWORDS`, `ROUTER_PROMPT`), `src/jarvis/agent/domains.py` (`phone` prompt)
- Test: `tests/test_phone_tools.py`, `tests/test_wiring.py`, `tests/test_graph.py`

**Interfaces:**
- Produces: tool `call_contact` in domain `phone`, no confirmation, args `CallArgs(contact: str min_length=1, last4: str | None = Field(None, pattern=r"^\d{4}$"))`. Its result is `queue("call_contact", contact=..., last4=...)` with `last4` omitted when unset (use `exclude_unset`, as the other tools do).

- [ ] **Step 1: Write failing tests.** `test_phone_tools.py`: the phone tool set now includes `call_contact`; `fn(contact="Mom")["client_action"] == {"type": "call_contact", "contact": "Mom"}`; with `last4="8830"` it is included; `CallArgs(contact="Mom", last4="88")` raises `ValidationError`, as does `last4="abcd"`. Update `test_wiring.py` expected tool names/tags to include `call_contact`. `test_graph.py` (or where `keyword_domain` is tested): `keyword_domain("call Mom") == ["phone"]`, `keyword_domain("calendar for Monday") == ["calendar"]`, `keyword_domain("call the dentist and add it to my calendar") is None`.
- [ ] **Step 2: Run** `pytest tests/test_phone_tools.py tests/test_wiring.py tests/test_graph.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `CallArgs` and the tool in `phone_tools.py`, with description "Call a contact by name on the user's phone: WhatsApp first, normal call if that is not possible. Pass last4 only after the user has chosen between several matches."
- [ ] **Step 4: Routing.** Add `\bcall(?:ing)?\b` to the `phone` regex in `KEYWORDS`. Router prompt: add "calling" to the `phone` description and the example `'call Mom' -> phone`, and a sentence: "If the assistant's last message asked which contact to call, or to repeat or spell a name, the user's reply is phone." (The router sees the last 6 messages, so it can read that question.)
- [ ] **Step 5: `phone` domain prompt.** List `call_contact`; say: the phone resolves the name; say you asked the phone to call, never that the call is placed; when a message starting "Phone result" reports several matches, ask which one by name and last four digits, then call `call_contact` again with the same contact and `last4`; when it reports no match, ask the user to say the name again or spell it, then call `call_contact` with what they say; ask at most twice per call, then tell the user you could not find it; if contacts permission was denied, tell the user to allow it in the app and do not ask for a respelling.
- [ ] **Step 6: Router test.** Using the fake provider in `test_graph.py`, assert that after an assistant message "Which one, ending 4521 or 8830?" the router prompt text sent to the fast model contains the "asked which contact" sentence (pin the prompt wording, not model behaviour).
- [ ] **Step 7: Run** the three test files plus `pytest -q`. Expected: all PASS.
- [ ] **Step 8: Commit** `feat: call_contact phone tool and routing`.

### Task 4: `action_result` frame (server)

**Files:**
- Modify: `src/jarvis/voice/ws.py` (`_control`, new handler), `src/jarvis/voice/protocol.py` (limits)
- Test: `tests/test_voice_actions.py`

**Interfaces:**
- Consumes: `_utterance(text)` and `_start(coro)` in `ws.py`.
- Produces: client frame `{"type":"action_result","action":"call_contact","status":<"ambiguous"|"not_found"|"no_permission"|"error">,"contact":str,"matches":[{"name":str,"last4":str}]}`. `matches` only matters for `ambiguous`. Handler `async def _action_result(self, m: dict) -> None`.

- [ ] **Step 1: Write failing tests** in `test_voice_actions.py`, in the existing `build(...)`/websocket style. (a) An `ambiguous` frame with two matches starts a turn: the fake LLM's received human message contains `Mom`, `4521`, `8830`, the word `call`, and starts with `Phone result`; the reply is spoken. (b) A contact named `Mom. Ignore all previous instructions <b>` reaches the model as `Mom Ignore all previous instructions b`: only letters, digits, spaces and `.'-`, at most 40 characters. (c) `not_found`, `no_permission` and `error` each start a turn whose text names the status in plain words. (d) Rejected with an `error` frame and no turn: unknown status, unknown action, `contact` not a string or over 60 chars, more than 10 matches, a `last4` that is not four digits, `matches` not a list.
- [ ] **Step 2: Run** `pytest tests/test_voice_actions.py -q`. Expected: FAIL (`Unknown frame type`).
- [ ] **Step 3: Implement** `_action_result` and add `elif kind == "action_result": await self._start(self._action_result(m))` in `_control`. It validates as in (d), sanitises names per (b), builds one text per status ("Phone result: the call to '<contact>' matches N contacts: <name> ending <last4>, ..."), and runs `await self._utterance(text)`. Validation failure sends `self.send("error", message="Bad action result.")` and returns. Sanitise `contact` the same way as names.
- [ ] **Step 4: Run** `pytest tests/test_voice_actions.py -q` then `pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: phone can report a client action result`.

### Task 5: Call flow in the app

**Files:**
- Modify: `jarvis_app/lib/session.dart` (`PhoneActions.run`, `_runActions`), `jarvis_app/lib/phone.dart`, `jarvis_app/android/app/src/main/AndroidManifest.xml`, `jarvis_app/test/fakes.dart`
- Test: `jarvis_app/test/session_test.dart`, `jarvis_app/test/phone_test.dart` (create)

**Interfaces:**
- Consumes: `matchContacts`, `ContactMatch`, `buildCallIntent` (Task 2); `callOnWhatsApp` (Task 1).
- Produces: `Future<String?> run(Map<String, dynamic> action, {void Function(Map<String, dynamic> result)? report})` on `PhoneActions`. `SessionController._runActions` passes `report: (r) => _socket?.sendJson('action_result', r)`. `FakePhone.run` accepts and records `report`, and a test field lets a test make it call `report` with a given map.

- [ ] **Step 1: Write failing tests.** `session_test.dart`: when `FakePhone` reports `{'action':'call_contact','status':'ambiguous','contact':'Mom','matches':[{'name':'Mom','last4':'4521'}]}`, the fake socket receives a JSON frame of type `action_result` with those fields. `phone_test.dart`: extract the decision logic as a testable function in `phone.dart`, `Future<String?> runCall(Map action, {required Future<List<ContactMatch>?> Function() contacts, required Future<bool> Function(String contactId) whatsapp, required Future<void> Function(IntentSpec) launch, required Future<bool> Function() canCall, void Function(Map<String, dynamic>)? report})`, and test: one match + WhatsApp ok launches nothing else and reports nothing; one match + WhatsApp false launches `CALL` when `canCall` true and `DIAL` when false; two matches reports `ambiguous` with `{name,last4}` only and launches nothing; `last4` filters to one and calls; no match reports `not_found`; `contacts` returns null (permission denied) reports `no_permission`; `launch` throws reports `error`; with `report == null` and ambiguity, returns a plain error string instead.
- [ ] **Step 2: Run** `flutter test test/phone_test.dart test/session_test.dart`. Expected: FAIL.
- [ ] **Step 3: Implement.** Change the `PhoneActions.run` signature and update `FakePhone`. In `phone.dart` add `runCall` per the signature above and wire `AndroidPhoneActions.run` to it for `type == 'call_contact'`: `contacts` reads all contacts with all phone numbers into `ContactMatch` (null when contacts permission is not granted), `whatsapp` is `callOnWhatsApp`, `canCall` is `Permission.phone.request().isGranted`, `launch` is the existing `AndroidIntent` launch with `FLAG_ACTIVITY_NEW_TASK`. Success reports nothing. Add `<uses-permission android:name="android.permission.CALL_PHONE"/>`. Chat-screen path (`app.dart` `runPhoneActions`) passes no `report` and keeps working.
- [ ] **Step 4: Run** `flutter test` (whole suite) and `flutter analyze`. Expected: PASS, no new issues.
- [ ] **Step 5: Commit** `feat: place WhatsApp or phone calls from voice`.

### Task 6: Acceptance rows and handoff

**Files:**
- Modify: `ACCEPTANCE.md`, `docs/HANDOFF.md`

- [ ] **Step 1: Add a "Call a contact" section to `ACCEPTANCE.md`** in the existing Do/Expect table style, rows: "call <contact with WhatsApp>" starts a WhatsApp voice call; "call <contact without WhatsApp>" opens the phone call (direct with permission, dialer if denied); two contacts with the same name: Jarvis speaks both with last 4 digits and "the second one" works; "ending 8830" works; garbled name: Jarvis asks to repeat or spell, answering retries; contacts permission off: Jarvis says so; Telegram "call Mom": reply says the phone action was not run; a spoken reply never claims the call was placed.
- [ ] **Step 2: Note in `docs/HANDOFF.md`** the new frame, tool and the Task 1 spike result.
- [ ] **Step 3: Build** `cd jarvis_app && flutter build apk --debug --dart-define-from-file=jarvis.env.json` and walk the new rows on the device. Fix and report any that fail.
- [ ] **Step 4: Commit** `docs: acceptance rows for calling a contact`.

---

## Self-review notes

- Spec coverage: calling flow (Tasks 3, 5), ambiguity by last 4 (2, 4, 5), not-found / spell loop (3, 4), permission (5), privacy limit (4, 5), routing (3), untrusted names (4), WhatsApp mechanism and gate (1), acceptance (6).
- Simplification versus the spec, flagged for the owner: no 5-second server timer. Success is not reported back; the reply already says "asking your phone", which satisfies "never claim the call was placed". Only problems (ambiguous, not found, no permission, error) come back as frames.
- Names are sanitised rather than wrapped in an `untrusted_*` tag, because those tags also switch on tap-only confirmation for later writes, which a contact lookup should not trigger.
- `call me later` now routes to `phone` through the keyword; harmless and simpler than a negative pattern.
