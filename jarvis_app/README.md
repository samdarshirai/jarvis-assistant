# Jarvis Voice App

A Flutter Android app that brings voice-first access to your calendar, email, tasks, and more via "Hey Jarvis" wake word. Integrates Picovoice, Deepgram, and Cartesia for on-device keyword spotting and cloud speech-to-text and text-to-speech.

## Build

Install dependencies:
```
flutter pub get
```

Build the APK (requires `PICOVOICE_ACCESS_KEY`):
```
flutter build apk --debug --dart-define=PICOVOICE_ACCESS_KEY=<your_key>
```

## Permissions

The app requests (runtime on Android 14+, or manifest otherwise):
- **Microphone** — to hear your voice
- **Notifications** — to show alerts and confirmations
- **Contacts** — to find and message people
- **Manifest permissions** — alarms, full-screen intents, foreground service

## Untracked Files

Two files must exist locally but are gitignored:

1. **`assets/hey_jarvis_android.ppn`** — Your trained Picovoice keyword model. Create it in the Picovoice console for Android, then save it here. A placeholder (even empty) allows `flutter build` to succeed, but the app will not wake without the real file.
2. **`android/app/google-services.json`** — Your Firebase Android config (optional). The app builds and runs without it; only push notifications and tap-to-play features are unavailable. Place it here after creating a Firebase project and adding the Android app `com.jarvis.jarvis_app`.

## SDK Requirements

- **Android SDK platform 37** — The build requires `compileSdk = 37`. If your SDK has only `android-37.0`, create a symlink:
  ```
  ln -s android-37.0 android-37
  ```
  in your SDK's `platforms/` directory.

## Checks

Run tests and lint:
```
flutter test
flutter analyze
```
