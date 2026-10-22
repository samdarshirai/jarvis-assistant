# Quiet Wake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wake word listens only while charging or on Android Auto, Jarvis never makes its own sound during night hours, and the home-screen widget joins the power-button assistant as a mic-free way in.

**Architecture:** A `Prefs` object (wake on/off, night hours) is the single source of truth. `QuietPlayer` / `QuietSpeaker` wrap the two existing audio outputs and drop sound whenever `Prefs.isNight`. A small local Flutter plugin reports charging and Android Auto state; the wake foreground-service handler pauses its detector (but keeps the service alive) when the gate is closed.

**Tech Stack:** Flutter/Dart, Kotlin, `flutter_foreground_task` (existing), `androidx.car.app` `CarConnection` for Android Auto, `flutter_test`.

**Spec:** `docs/superpowers/specs/2026-10-22-quiet-wake-design.md`

## Global Constraints

- Night hours default **23:00–07:00 in the phone's local time**, active from first launch. Start inclusive, end exclusive. The range may cross midnight. Start == end means no night.
- Fail closed: if prefs have not loaded or cannot be read, treat it as night (silent).
- Night silence is enforced at the two output points only: `PcmPlayer` and `TtsSpeaker` (`lib/audio.dart`). Text keeps showing.
- Wake word runs only when `wakeEnabled && (charging || androidAuto)`. Night hours never stop it listening.
- Alarms are untouched (system clock app via `SET_ALARM`).
- Build the app with `--dart-define-from-file=jarvis.env.json`. Tests run from `jarvis_app/` with `flutter test`.
- Removed from scope by the user: shake, quick-settings tile, built-in back-tap detector, screen-off switch. Pixel back-tap is a text hint only.

## Review Focus

- Night range crossing midnight, and the exact boundary minutes (23:00 silent, 07:00 not silent). Pinned in Task 1.
- Equal start and end means voice all day. Pinned in Task 1.
- Prefs unreadable or not yet loaded means silent. Pinned in Task 1.
- Night begins while a reply is streaming: later chunks are dropped, buffered audio flushed, text still shown. Pinned in Task 2.
- The offline message and ready beep are also silent at night. Pinned in Task 2.
- Charger plugged in while a session holds the mic: wake service must not grab it. Pinned in Task 5.
- Widget tapped before sign-in or voice setup: opens the app, starts no session. Checked in Task 7.

---

### Task 1: Prefs with night-hours logic

**Files:**
- Create: `jarvis_app/lib/prefs.dart`
- Test: `jarvis_app/test/prefs_test.dart`

**Interfaces:**
- Produces:
  - `bool isNightAt(DateTime now, int startMin, int endMin)` (minutes since midnight)
  - `class Prefs extends ChangeNotifier { Prefs({ConfigStore store = const ConfigStore(), DateTime Function() clock = DateTime.now}); Future<void> load(); bool get loaded; bool get isNight; bool get wakeEnabled; int get nightStart; int get nightEnd; bool get wakeChosen; Future<void> setWakeEnabled(bool v); Future<void> setNight(int startMin, int endMin); Future<void> markWakeChosen(); }`
  - `Future<bool> storedWakeEnabled([ConfigStore store = const ConfigStore()])` (readable from the wake service isolate; default true)
- Storage keys in `ConfigStore.read/write`: `wake_enabled` ("1"/"0"), `night_start`, `night_end` (decimal minutes), `wake_chosen` ("1").

- [ ] **Step 1: Write failing tests in `test/prefs_test.dart`** (use `MemStore` from `auth_test.dart`)
  - `isNightAt` with 23:00–07:00: 22:59 false, 23:00 true, 00:00 true, 06:59 true, 07:00 false, 12:00 false.
  - `isNightAt` same-day range 01:00–05:00: 03:00 true, 05:00 false.
  - `isNightAt` with start == end: 03:00 false.
  - `Prefs` before `load()`: `isNight` is true.
  - `Prefs.load()` with an empty store: `loaded` true, `nightStart` 1380, `nightEnd` 420, `wakeEnabled` true, `wakeChosen` false; with a clock at 02:00 `isNight` true, at 12:00 false.
  - `Prefs.load()` when the store's `read` throws: `loaded` stays false, `isNight` true.
  - `setNight(0, 0)` then `isNight` false at any time; values persist (a second `Prefs` on the same store reads them).
  - `setWakeEnabled(false)` persists and `storedWakeEnabled` returns false.
- [ ] **Step 2: Run** `flutter test test/prefs_test.dart` — expect FAIL (file missing).
- [ ] **Step 3: Implement** `lib/prefs.dart` per the signatures. `isNight` returns true when `!loaded`; otherwise `isNightAt(clock(), nightStart, nightEnd)`. `load()` catches read errors and leaves `loaded` false. Notify listeners on every setter.
- [ ] **Step 4: Run** the same test — expect PASS.
- [ ] **Step 5: Commit** `feat: prefs with fail-closed night hours`

---

### Task 2: Night-silent audio wrappers

**Files:**
- Modify: `jarvis_app/lib/audio.dart` (add classes at the end)
- Modify: `jarvis_app/lib/app.dart:245-249` (wrap `PcmPlayer()` and `TtsSpeaker()` inside `_bind`), and `JarvisApp` / its state to own one `Prefs` (new optional constructor parameter `Prefs? prefs`, default `Prefs()`; call `prefs.load()` in `initState`).
- Test: `jarvis_app/test/quiet_audio_test.dart`

**Interfaces:**
- Consumes: `Prefs.isNight` (Task 1); `Player`, `Speaker` from `lib/session.dart`.
- Produces: `class QuietPlayer implements Player { QuietPlayer(Player inner, bool Function() isNight); }` and `class QuietSpeaker implements Speaker { QuietSpeaker(Speaker inner, bool Function() isNight); }`.

- [ ] **Step 1: Write failing tests** with `FakePlayer` / `FakeSpeaker` from `test/fakes.dart`:
  - Day: `play` forwards the bytes; `say` forwards the text.
  - Night: `play` forwards nothing; `say` forwards nothing and completes normally.
  - Night begins mid-reply: first `play` in day is forwarded, then the flag flips to night; the next `play` is dropped and `inner.flush()` is called exactly once, even across several dropped chunks.
  - A `SessionController` built with the wrappers at night: `start()` plays no ready beep (`FakePlayer.played` empty) and the offline message (`FakeSpeaker.said` empty) is silent, while `jarvisText` / `userText` still update from server events (use `test/session_rig.dart` as the other session tests do).
- [ ] **Step 2: Run** `flutter test test/quiet_audio_test.dart` — expect FAIL.
- [ ] **Step 3: Implement** the two classes. `QuietPlayer` keeps a private `_cut` bool: on first night `play`, flush inner once and set it; clear it on the next day `play`. `flush()` always forwards.
- [ ] **Step 4: Wire into `_bind`** in `app.dart` using `widget.prefs`-owned instance: `QuietPlayer(PcmPlayer(), () => prefs.isNight)` and `QuietSpeaker(TtsSpeaker(), () => prefs.isNight)`.
- [ ] **Step 5: Run** `flutter test` (whole suite) — expect PASS. Existing tests that build `JarvisApp` keep working because `prefs` is optional.
- [ ] **Step 6: Commit** `feat: drop Jarvis sound during night hours`

---

### Task 3: Wake gate rule

**Files:**
- Create: `jarvis_app/lib/wake_gate.dart`
- Test: `jarvis_app/test/wake_gate_test.dart`

**Interfaces:**
- Produces: `bool wakeGateOpen({required bool enabled, required bool charging, required bool androidAuto})`.

- [ ] **Step 1: Write failing test** covering all eight input combinations: open only when `enabled && (charging || androidAuto)`.
- [ ] **Step 2: Run** `flutter test test/wake_gate_test.dart` — expect FAIL.
- [ ] **Step 3: Implement** the one-line function.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: wake gate rule`

---

### Task 4: Charging and Android Auto plugin

**Files:**
- Create: local plugin `jarvis_app/packages/jarvis_gate/` (`pubspec.yaml`, `lib/jarvis_gate.dart`, `android/build.gradle`, `android/src/main/kotlin/.../JarvisGatePlugin.kt`, `android/src/main/AndroidManifest.xml`)
- Modify: `jarvis_app/pubspec.yaml` (path dependency `jarvis_gate`)
- Test: `jarvis_app/test/jarvis_gate_test.dart`

A local plugin is used because the wake handler runs in `flutter_foreground_task`'s own isolate, which only has plugins from `GeneratedPluginRegistrant`; a `MainActivity` channel would not reach it.

**Interfaces:**
- Produces:
  - `class GateState { const GateState({required this.charging, required this.androidAuto}); final bool charging, androidAuto; }`
  - `class JarvisGate { static Future<GateState> state(); static Stream<GateState> get changes; }`
  - Channels: `MethodChannel('jarvis_gate')` method `state` returning `{charging: bool, androidAuto: bool}`; `EventChannel('jarvis_gate/changes')` emitting the same map on every change.
- Kotlin behaviour: charging from the sticky `ACTION_BATTERY_CHANGED` (`BATTERY_PLUGGED_*` non-zero) plus a runtime-registered receiver for `ACTION_POWER_CONNECTED/DISCONNECTED`; Android Auto from `androidx.car.app.connection.CarConnection(context).type.observeForever`, true when `CarConnection.CONNECTION_TYPE_PROJECTION`. Add dependency `androidx.car.app:app` (latest stable). Unregister receiver and observer when the stream is cancelled.

- [ ] **Step 1: Write failing Dart test** that mocks both channels (`TestDefaultBinaryMessengerBinding`) and asserts `state()` maps the reply and `changes` emits a `GateState` per event.
- [ ] **Step 2: Run** `flutter test test/jarvis_gate_test.dart` — expect FAIL.
- [ ] **Step 3: Implement** the Dart wrapper and the Kotlin plugin; add the path dependency; `flutter pub get`.
- [ ] **Step 4: Run** the test — expect PASS; then `flutter build apk --debug --dart-define-from-file=jarvis.env.json` — expect a successful build (confirms the car-app dependency resolves).
- [ ] **Step 5: Manual check on the phone** (note result in the commit message): plug in and unplug the charger and watch `adb logcat` for the plugin's change events; connect Android Auto and see `androidAuto: true`.
- [ ] **Step 6: Commit** `feat: jarvis_gate plugin for charging and Android Auto`

---

### Task 5: Wake service obeys the gate

**Files:**
- Modify: `jarvis_app/lib/wake.dart` (`WakeTaskHandler`, `WakeService`)
- Test: `jarvis_app/test/wake_test.dart` (extend)

**Interfaces:**
- Consumes: `wakeGateOpen` (Task 3), `JarvisGate` (Task 4), `storedWakeEnabled` (Task 1).
- Produces: `WakeService.refreshGate()` (main isolate sends data `'gate'` to the task); `WakeService.ensureRunning()` now returns without starting when `storedWakeEnabled()` is false.
- Handler rules (exact):
  - Keep two flags: `_held` (a session holds the mic; set by data `'pause'`, cleared by `'resume'`) and the latest `GateState`.
  - `_listen()` returns without recording unless `wakeGateOpen(...)` is true **and** `!_held`.
  - On every `JarvisGate.changes` event and on data `'gate'`: re-read `storedWakeEnabled()`, then `_listen()` if the gate is open, else `_pause()`.
  - When paused by the gate, `FlutterForegroundTask.updateService(notificationText: 'Wake word paused - plug in or connect Android Auto')`; when listening, the existing `Listening for "Hey Jarvis"` text. The service stays alive (Android 14 blocks starting a microphone service from the background).
  - Subscribe in `onStart`, cancel in `onDestroy`.

- [ ] **Step 1: Write failing tests** for a small pure helper `bool shouldListen({required bool gateOpen, required bool held})` exported from `wake.dart`: listens only when gate open and not held — including the case "charger plugged in during a session" (`gateOpen: true, held: true` → false).
- [ ] **Step 2: Run** `flutter test test/wake_test.dart` — expect FAIL.
- [ ] **Step 3: Implement** the helper and the handler changes above.
- [ ] **Step 4: Run** `flutter test` — expect PASS.
- [ ] **Step 5: Manual check:** unplugged, away from Android Auto — the detector is silent (`adb logcat | grep wake:` shows no `chunks=` lines) and the notification reads "Wake word paused…"; plug in — chunks resume within seconds; say "Hey Jarvis" and it starts a session; unplug mid-listening — chunks stop.
- [ ] **Step 6: Commit** `feat: wake word listens only while charging or on Android Auto`

---

### Task 6: Settings and the one-time wake choice

**Files:**
- Create: `jarvis_app/lib/wake_choice_screen.dart`
- Modify: `jarvis_app/lib/settings_screen.dart`, `jarvis_app/lib/ui.dart:88-100` (pass prefs through), `jarvis_app/lib/app.dart` (`_home()` and `_bind`)
- Test: `jarvis_app/test/wake_choice_test.dart`, extend `jarvis_app/test/settings_screen_test.dart`

**Interfaces:**
- Consumes: `Prefs` (Task 1), `WakeService.stop/ensureRunning/refreshGate` (Task 5, passed in as callbacks so tests stay free of platform code).
- Produces:
  - `WakeChoiceScreen({required Prefs prefs, required VoidCallback onDone})`: explains "Hey Jarvis" works only while charging or on Android Auto; a switch (default on); a Continue button that calls `prefs.setWakeEnabled(value)`, `prefs.markWakeChosen()`, then `onDone`.
  - `SettingsScreen` gains optional `Prefs? prefs` and `Future<void> Function()? onWakeChanged`; when `prefs != null` it shows: a "Hey Jarvis wake word" switch; "Night starts" and "Night ends" rows opening `showTimePicker` (calls `prefs.setNight`); one line of text "Night hours: Jarvis shows replies as text and makes no sound."; and a "Double-tap on Pixel" hint: "Settings > System > Gestures > Quick Tap, then choose Jarvis."
- `_home()` in `app.dart`: when `_ready && prefs.loaded && !prefs.wakeChosen`, show `WakeChoiceScreen` instead of `SessionScreen` (new and existing users alike); `onDone` calls `WakeService.refreshGate()` / `ensureRunning()` or `stop()` according to the choice, then rebuilds.

- [ ] **Step 1: Write failing widget tests:** the choice screen shows the switch on by default and Continue persists `wakeEnabled` false after turning it off and sets `wakeChosen`; settings shows the switch, both night rows (default labels "11:00 PM" and "7:00 AM"), and the Quick Tap hint; toggling the switch calls `onWakeChanged` once.
- [ ] **Step 2: Run** the two test files — expect FAIL.
- [ ] **Step 3: Implement** the screen and the settings additions, following the existing screens' theme (`ScreenShell`, `amber`, `cream`, `mutedText`).
- [ ] **Step 4: Wire** `_home()` and the settings route as described.
- [ ] **Step 5: Run** `flutter test` — expect PASS.
- [ ] **Step 6: Commit** `feat: wake word choice and night-hours settings`

---

### Task 7: Home-screen widget

**Files:**
- Create: `android/app/src/main/kotlin/com/jarvis/jarvis_app/JarvisWidget.kt`, `android/app/src/main/res/xml/jarvis_widget_info.xml`, `android/app/src/main/res/layout/jarvis_widget.xml`
- Modify: `android/app/src/main/AndroidManifest.xml` (add the `<receiver>` with `APPWIDGET_UPDATE` and the `appwidget.provider` meta-data)

**Interfaces:**
- Produces: `class JarvisWidget : AppWidgetProvider` whose single button fires a `PendingIntent.getActivity` (immutable) to `MainActivity` with `putExtra(MainActivity.EXTRA_START, true)` and `FLAG_ACTIVITY_NEW_TASK`. No new Dart code: `MainActivity` already starts a session from `EXTRA_START` when `voiceReady` is true, and just opens the app otherwise.

- [ ] **Step 1: Implement** the provider, a 2x1 widget with the app icon and the label "Talk to Jarvis", and register it.
- [ ] **Step 2: Build** `flutter build apk --debug --dart-define-from-file=jarvis.env.json` — expect success.
- [ ] **Step 3: Manual check on the phone** (record results in the commit message): add the widget; tap it with the app closed (session starts); tap it with the app open (session starts); tap it while signed out (app opens, no session); at night, the reply shows as text and plays no sound.
- [ ] **Step 4: Commit** `feat: home-screen widget starts a session`

---

### Task 8: End-to-end acceptance on the phone

- [ ] **Step 1:** Install the build. Clear app data to see first-run: onboarding ends with the wake choice screen; existing install shows it once after update.
- [ ] **Step 2:** Walk the spec's success list: battery power and no Android Auto means no `wake: chunks=` logs; charger or Android Auto turns the wake word on; set the phone clock inside 23:00–07:00 (or set night hours around the current time) and try wake word, widget, assistant and a pushed brief — no Jarvis sound from any, text still shown, and an alarm still rings via the clock app.
- [ ] **Step 3:** Update `docs/HANDOFF.md` with the new behaviour and the acceptance results. Commit `docs: quiet wake acceptance`.

---

## Self-review notes

- **Spec coverage:** gated wake word (Tasks 3-5), Android Auto (4), widget (7), assistant unchanged, night hours default/custom and fail-closed silence for every entry point (1-2), onboarding choice and settings page (6), Pixel Quick Tap hint (6), alarms untouched (no task). Dropped items stay dropped.
- **One deviation from the spec:** the spec's success line says the foreground microphone notification disappears on battery. Android 14 blocks restarting a microphone foreground service from the background, so the service stays and only recording stops, with the notification reading "Wake word paused". The spec's success line is updated to match.
- **Settled by the author, not the user:** the wake choice is a standalone screen shown right after setup (instead of a step inside the 721-line onboarding file); prefs live in the existing secure store.
