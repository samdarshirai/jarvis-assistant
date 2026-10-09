# Jarvis Voice App

A Flutter Android app for voice-first access to your calendar, email, tasks, and more. Detects "Hey Jarvis" on device with openWakeWord keyword spotting, streams audio to the Jarvis backend via WebSocket (`/voice`), plays the spoken reply, and runs phone actions (alarms, timers, navigation, message compose) as Android intents. The backend handles speech-to-text (Deepgram), text-to-speech (Deepgram Aura), and runs the agent graph.

## Setup

On first launch, enter your backend hostname (the tunnel URL from ACCEPTANCE.md step 1) and a device token from `python -m jarvis.voice.token`. See ACCEPTANCE.md (sub-project 3 section) for tunnel and account setup.

## Build

Install dependencies:
```
flutter pub get
```

Build the APK:
```
flutter build apk --debug
```

## Permissions

**Runtime permissions** asked at start: microphone, notifications, contacts.
**Manifest-only permissions**: alarms, full-screen intent, foreground service (microphone), boot completed, internet, audio settings, wake lock.

## Untracked Files

1. **`android/app/google-services.json`** — Firebase Android config (optional). The app builds without it; only push tap-to-play is unavailable.

## SDK Requirements

**Android SDK platform 37** — The build requires `compileSdk = 37`. If only `android-37.0` exists, create a symlink: `ln -s android-37.0 android-37` in your SDK `platforms/` directory.

## Checks

```
flutter test
flutter analyze
```
