# Run Jarvis on your phone

The app is Android only (Flutter, package `com.jarvis.jarvis_app`, Android 14+). iPhone is not supported.

**Implemented:** Telegram chat, Calendar, Tasks, Gmail, Pixel voice app.
**Not built yet:** memory/notes/web research (sub-project 4), morning brief and proactive alerts (sub-project 5).

Sources: `ACCEPTANCE.md`, `docs/HANDOFF.md`.

## A. Backend on laptop

1. Install dependencies:
   ```
   cd /Users/ronalisenapati/Ronali/jarvis
   python3 -m venv .venv && . .venv/bin/activate
   pip install -e '.[dev]'
   ```
2. Start the database: `docker compose up -d db`
3. `cp .env.example .env` and fill in:
   - `JARVIS_OPENROUTER_API_KEY`, `JARVIS_MODELS_FAST`, `JARVIS_MODELS_STRONG` (comma-separated OpenRouter model ids)
   - `JARVIS_TELEGRAM_BOT_TOKEN` (from @BotFather), `JARVIS_TELEGRAM_OWNER_CHAT_ID`
   - `JARVIS_FERNET_KEY`:
     ```
     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```
   - `JARVIS_DEEPGRAM_API_KEY`, `JARVIS_CARTESIA_API_KEY`, `JARVIS_CARTESIA_VOICE_ID`
     (turn off data retention/training in both dashboards)
4. Google OAuth:
   - In Google Cloud, enable the Calendar, Tasks and Gmail APIs.
   - Create a Desktop OAuth client and save it as `client_secret.json` in the repo root.
   - Set the consent screen to "In production".
   - Run `python -m jarvis.google.auth`.
5. Start the server: `uvicorn jarvis.main:app`
6. Check: message your Telegram bot "What's on my calendar today?". It should answer.

## B. Reach the laptop from the phone

7. `brew install cloudflared`
8. In a second terminal: `cloudflared tunnel --url http://localhost:8000`.
   Copy the `https://….trycloudflare.com` URL. A quick tunnel gets a new URL on every restart, so re-pair the app each time.
9. Create a device token: `python -m jarvis.voice.token`. It prints once, so save it.
   Revoke with `python -m jarvis.voice.token --revoke <id>`.

## C. Build and install the app

10. Wake word: nothing to do. The model and "Hey Jarvis" keyword are bundled in `jarvis_app/assets/kws/`.
11. Firebase push: skip for now. It is only needed for tap-to-play push.
12. Phone: enable Developer options and USB debugging, then plug it in.
13. Build and install:
    ```
    cd jarvis_app
    flutter build apk --debug
    adb install -r build/app/outputs/flutter-apk/app-debug.apk
    ```
    The build needs Android SDK platform 37. On this Mac the `android-37` symlink already exists.
14. Open the app and paste the tunnel URL and device token. Grant microphone, notification and contacts permissions. Grant "Display over other apps / full-screen intents" if offered.
15. Smoke test:
    - Tap Talk: "What's my day look like?"
    - Say "Hey Jarvis, put gym at 7 tomorrow in my calendar", then say "yes" to confirm.

## Caveats

- Not tested on a real device yet: wake word, screen wake, assistant long-press, Kotlin services, echo cancellation. Expect some bugs.
- A spoken "yes" never sends an email. Say "send it", then tap Confirm on the card. This is on purpose.
- Web research and the morning brief don't exist yet.
- The laptop must stay awake with the server and tunnel running whenever you use the app.
- Server-only voice check without the phone:
  `python -m jarvis.voice.client wss://<host>/voice <token> question.wav` (16 kHz mono WAV).

## Full manual test table

See `ACCEPTANCE.md` rows 1-38.
