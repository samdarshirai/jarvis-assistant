# Quiet wake: gated wake word, more ways in, silent nights

## Intent

The wake word service keeps the microphone open all the time (it starts at app launch and at boot, once signed in). The owner dislikes two things:

1. It drains the battery.
2. A wrong wake-word detection makes Jarvis speak out loud at midnight or in other quiet moments.

This change keeps the wake word where it is actually needed (hands-free while driving, and across the room with the phone on a charger), gives other ways to start Jarvis that need no open mic, and guarantees Jarvis never speaks aloud during night hours.

## Goals

- The wake word listens only while the phone is charging or connected to Android Auto. Otherwise the mic is off.
- Extra ways to start a session without an open mic: home-screen widget, and double-tap on the back of the phone on Pixel (through Android's own Quick Tap). The existing default-assistant entry (power button / long-press, `ASSIST` intent) stays.
- The user sets night hours in settings. During night hours Jarvis replies as text on screen and makes no sound. Outside them, voice is normal.
- The wake word can be switched on or off. This is offered as a step in onboarding and stays available on the settings page. The other entry points need no switch.

## Anti-goal

Jarvis speaks aloud during night hours, even by mistake. This outranks every other goal. If a design choice trades a missed reply for a possible night-time sound, choose the missed sound.

## Non-goals

- Shake, a quick-settings tile, a built-in back-tap detector for non-Pixel phones, and a screen-off listening switch. The user cut these to keep the options few.
- No Do Not Disturb or bedtime-mode integration. Night is only the fixed hours the user sets.
- No "quiet voice" or low-volume mode.
- No new wake-word model or change to detection accuracy.
- No iOS work.

## Decided behaviour

**When the wake word listens.** Charging, or connected to Android Auto (the user's choice; wired or wireless). Cars without Android Auto are not covered; the user can still use the other triggers there. Stops when neither holds. Night hours do not stop the wake word from listening; they only silence replies.

**Night mode.** Night hours default to 11pm–7am in the phone's local time, active from first launch with no setup needed. The user can change the start and end time in settings (the range may cross midnight). During it, every Jarvis reply is shown as text and no audio plays. This applies to all entry points: wake word, widget, assistant, and push-started sessions.

**Pushes and reminders at night.** Text only, on screen. Alarms are the exception: they ring through the phone's clock app (Jarvis already creates them with `SET_ALARM`), so Jarvis's own voice is not involved and nothing changes there.

**Back-tap (Pixel only).** Android's own Quick Tap can open Jarvis. Jarvis shows a setup hint pointing the user to that system setting. No detection code.

## Constraints from the existing code

- `lib/wake.dart`: `WakeService.ensureRunning()` is called from `main.dart` at launch and from `BootReceiver` at boot, and starts the foreground microphone service unconditionally once a sign-in token exists.
- Jarvis has two places that produce sound: `PcmPlayer` (streamed backend audio) and `TtsSpeaker` (Android text-to-speech fallback), both in `lib/audio.dart`. Night silence must be enforced where output happens, so any caller, present or future, is covered. Text must still be displayed.
- Sessions start from several places (`AppHost.startSession()`, assistant service, push). The mic-handoff logic between the wake service and sessions (`pause` / `resume`) must keep working when the wake service is intentionally stopped.
- The Android manifest already declares `BLUETOOTH_CONNECT`, `FOREGROUND_SERVICE_MICROPHONE`, and the `ASSIST` and voice-interaction services.

## Author's calls (settled without the user; easy to change)

- **Fail closed.** The night check runs at the moment audio would play (each reply, and each streamed chunk). If settings or the clock cannot be read, treat it as night: silent.
- **Cut off at night start.** Streamed reply audio still playing when night begins is stopped within about a second (even if no further chunk arrives); the rest shows as text. A short Jarvis TTS acknowledgement (or the offline message) already speaking is not interrupted.
- **Clock changes.** Use the phone's current local time on every check, so timezone and daylight-saving changes take effect immediately.
- **Equal start and end time** means no night hours (voice allowed all day).
- **Wake-word state flips mid-session** (unplug, car disconnects): the running session finishes; `resume` then does nothing while the gate is closed.
- **Push notification sound** belongs to the system. Night mode governs only Jarvis's own voice (`PcmPlayer`, `TtsSpeaker`).
- **Widget taps** open the app visibly, which Android allows.
- **Trigger defaults** (not yet confirmed by the user): wake word on. Onboarding shows the wake-word choice once for new and existing users; the settings page can change it later.

## Left to the builder

- How to detect charging and an Android Auto connection (broadcast receivers, the Android Auto connection API, plugin, or native code).
- Widget implementation, and how a tap there starts a session when the app is not running.

- Where settings are stored, and the settings screen layout.
- Whether the text-only reply reuses the existing chat/transcript UI.
- Tests, in line with the project's existing style.

## Success

- On battery, away from Android Auto, the mic is not recording. The wake service stays alive (Android 14 blocks restarting a microphone service from the background) and its notification reads that the wake word is paused.
- Plugging in or connecting to Android Auto turns the wake word on within a few seconds; unplugging or disconnecting turns it off.
- With night hours set and the clock inside them, no trigger and no push can produce any Jarvis audio, while replies still appear as text.
- The widget and the power-button assistant start a session. The wake word starts one when enabled and not when disabled.
