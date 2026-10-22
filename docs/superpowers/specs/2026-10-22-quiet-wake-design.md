# Quiet wake: gated wake word, more ways in, silent nights

## Intent

The wake word service keeps the microphone open all the time (it starts at app launch and at boot, once signed in). The owner dislikes two things:

1. It drains the battery.
2. A wrong wake-word detection makes Jarvis speak out loud at midnight or in other quiet moments.

This change keeps the wake word where it is actually needed (hands-free while driving, and across the room with the phone on a charger), gives other ways to start Jarvis that need no open mic, and guarantees Jarvis never speaks aloud during night hours.

## Goals

- The wake word listens only while the phone is charging or connected to Android Auto. Otherwise the mic is off.
- Extra ways to start a session without an open mic: home-screen widget, quick-settings tile, double-tap on the back of the phone. The existing default-assistant entry (power button / long-press, `ASSIST` intent) stays.
- The user sets night hours in settings. During night hours Jarvis replies as text on screen and makes no sound. Outside them, voice is normal.
- Each trigger (wake word, widget, tile, back-tap) can be switched on or off. This is offered as a step in onboarding and stays available on the settings page.

## Anti-goal

Jarvis speaks aloud during night hours, even by mistake. This outranks every other goal. If a design choice trades a missed reply for a possible night-time sound, choose the missed sound.

## Non-goals

- Shake is dropped: the user chose double-tap instead.
- No Do Not Disturb or bedtime-mode integration. Night is only the fixed hours the user sets.
- No "quiet voice" or low-volume mode.
- No new wake-word model or change to detection accuracy.
- No iOS work.

## Decided behaviour

**When the wake word listens.** Charging, or connected to Android Auto (the user's choice; wired or wireless). Cars without Android Auto are not covered; the user can still use the other triggers there. Stops when neither holds. Night hours do not stop the wake word from listening; they only silence replies.

**Night mode.** Night hours default to 11pm–7am in the phone's local time, active from first launch with no setup needed. The user can change the start and end time in settings (the range may cross midnight). During it, every Jarvis reply is shown as text and no audio plays. This applies to all entry points: wake word, widget, tile, back-tap, assistant, and push-started sessions.

**Pushes and reminders at night.** Text only, on screen. Alarms are the exception: they ring through the phone's clock app (Jarvis already creates them with `SET_ALARM`), so Jarvis's own voice is not involved and nothing changes there.

**Back-tap.**
- Pixel: Android's own Quick Tap can open Jarvis. Jarvis only shows a setup hint pointing the user to that system setting. No detection code.
- Other phones: an optional built-in detector that reads the accelerometer in a background service and recognises a double tap on the back. Off by default, labelled experimental in settings.

## Constraints from the existing code

- `lib/wake.dart`: `WakeService.ensureRunning()` is called from `main.dart` at launch and from `BootReceiver` at boot, and starts the foreground microphone service unconditionally once a sign-in token exists.
- Jarvis has two places that produce sound: `PcmPlayer` (streamed backend audio) and `TtsSpeaker` (Android text-to-speech fallback), both in `lib/audio.dart`. Night silence must be enforced where output happens, so any caller, present or future, is covered. Text must still be displayed.
- Sessions start from several places (`AppHost.startSession()`, assistant service, push). The mic-handoff logic between the wake service and sessions (`pause` / `resume`) must keep working when the wake service is intentionally stopped.
- The Android manifest already declares `BLUETOOTH_CONNECT`, `FOREGROUND_SERVICE_MICROPHONE`, and the `ASSIST` and voice-interaction services.

## Author's calls (settled without the user; easy to change)

- **Fail closed.** The night check runs at the moment audio would play (each reply, and each streamed chunk). If settings or the clock cannot be read, treat it as night: silent.
- **Cut off at night start.** A reply still speaking when night begins is stopped; the rest shows as text.
- **Clock changes.** Use the phone's current local time on every check, so timezone and daylight-saving changes take effect immediately.
- **Equal start and end time** means no night hours (voice allowed all day).
- **Wake-word state flips mid-session** (unplug, car disconnects): the running session finishes; `resume` then does nothing while the gate is closed.
- **Push notification sound** belongs to the system. Night mode governs only Jarvis's own voice (`PcmPlayer`, `TtsSpeaker`).
- **Widget and tile taps** open the app visibly, which Android allows. **Back-tap** (the built-in detector) listens only while the screen is on by default, which avoids a new always-on sensor drain. A separate opt-in switch, "also when the screen is off or locked", carries a battery warning. When it fires with the screen off, it starts the session the way the wake word does today (full-screen notification over the lock screen), because Android 14 limits starting the microphone from the background. It does not reveal personal content on the lock screen without an unlock; the builder decides how.
- **Trigger defaults** (not yet confirmed by the user): wake word on; widget and tile available once added by the user; back-tap off. Onboarding shows the choices once for new and existing users; the settings page can change them later.
- **Back-tap on Pixel:** setup hint only, as above.

## Left to the builder

- How to detect charging and an Android Auto connection (broadcast receivers, the Android Auto connection API, plugin, or native code).
- Widget and quick-settings tile implementation, and how a tap there starts a session when the app is not running.
- Accelerometer back-tap algorithm and thresholds.
- Where settings are stored, and the settings screen layout.
- Whether the text-only reply reuses the existing chat/transcript UI.
- Tests, in line with the project's existing style.

## Success

- On battery, away from Android Auto, the mic is off and the foreground microphone notification is gone.
- Plugging in or connecting to Android Auto turns the wake word on within a few seconds; unplugging or disconnecting turns it off.
- With night hours set and the clock inside them, no trigger and no push can produce any Jarvis audio, while replies still appear as text.
- Each listed trigger starts a session when enabled and does nothing when disabled.
