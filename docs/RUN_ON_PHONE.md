# Run Jarvis on your phone

The app is Android only (Flutter, package `com.jarvis.jarvis_app`, Android 14+). iPhone is not supported.

**Implemented:** Telegram chat, Calendar (with reminders, invites and conflict warnings), Tasks and a Shopping list, Gmail, memory/notes/web research, Pixel voice app, morning brief, proactive alerts and email-to-calendar. All sub-projects built.

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
   - `JARVIS_DEEPGRAM_API_KEY` (covers STT and TTS)
     (turn off data retention/training in both dashboards)
   - Optional proactive settings (defaults shown): `JARVIS_BRIEF_ENABLED=true`, `JARVIS_BRIEF_TIME=07:30` (weekdays, local time),
     `JARVIS_LEAVE_LEAD_MINUTES=30`, `JARVIS_MAIL_POLL_MINUTES=5`, `JARVIS_AUTO_EVENT_CAP=5` (events added from email per 24 h)
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

10. Wake word: nothing to do. The openWakeWord `hey_jarvis` models are bundled in `jarvis_app/assets/oww/`.
11. Firebase push: only needed for the push copy of the morning brief and "time to leave" alerts (tap to play). Without it
    both still arrive on Telegram. To enable it, set `JARVIS_FCM_CREDENTIALS_PATH` to the Firebase service-account JSON and put
    `google-services.json` in `jarvis_app/android/app/` before building.
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
16. Smoke test the calendar and shopping features (Telegram or voice):
    - "Put lunch with Sam tomorrow at 12 for an hour, remind me a day before." The confirm card shows the reminder. If tomorrow
      12:00 is already busy it also shows "Warning: conflicts with ..." and up to three free slots; you can still confirm.
    - "Invite <someone you have emailed> to lunch Friday at 12." Jarvis looks the address up in your mail headers, and the
      card lists every address under "Invites emailed to". Nothing is emailed until you confirm. If the name is unknown or
      matches several people, Jarvis asks instead of guessing.
    - "Add milk and eggs to my shopping list." The items are added with no Confirm tap, to a separate "Shopping" task list.
    - To test the morning brief, set `JARVIS_BRIEF_TIME` to a couple of minutes ahead on a weekday and restart the server.

## Caveats

- Not tested on a real device yet: wake word, screen wake, assistant long-press, Kotlin services, echo cancellation. Expect some bugs.
- Reminders, invites, conflict warnings and the shopping list work by voice and chat. Invites are emailed only after you confirm
  (a tap on Telegram, or a spoken yes on voice), and the card lists every address, including existing guests who also get an
  update email when you add someone to an event.
- Adding shopping items needs no Confirm tap, except right after Jarvis read an email, a contact lookup or a web page in the
  same chat (the last 40 messages): then it asks first. Completing a shopping item always asks.
- Jarvis adds bookings it finds in new email (flights, appointments, reservations) to your calendar on its own, then sends a
  Telegram message with an Undo button. It checks for duplicates, adds at most `JARVIS_AUTO_EVENT_CAP` per 24 hours, and only
  looks at mail that arrives after the server first starts.
- Conflict and leave-now alerts go out within about 5 minutes of the moment; leave-now uses a fixed lead time, not live travel time.
- The morning brief and alerts only fire while the server is running. A restart after the brief time skips that day's brief.
- A spoken "yes" never sends an email. Say "send it", then tap Confirm on the card. This is on purpose.
- The morning brief arrives on Telegram and as a push (if Firebase is set up) at the configured time (default 07:30 on
  weekdays); tapping the push plays it aloud.
- The laptop must stay awake with the server and tunnel running whenever you use the app.
- Server-only voice check without the phone:
  `python -m jarvis.voice.client wss://<host>/voice <token> question.wav` (16 kHz mono WAV).

## Full manual test table

See `ACCEPTANCE.md` rows 1-65.
