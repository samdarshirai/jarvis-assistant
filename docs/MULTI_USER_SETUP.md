# Multi-user setup: prerequisites, dependencies, steps

For the owner (you). Gets the multi-tenant, bring-your-own-keys build running on Railway, with the Android app signing in through Google. Details of the design: `docs/superpowers/specs/2026-10-20-multi-tenant-byok-design.md`. Deploy basics: `docs/DEPLOY-RAILWAY.md`. Manual test checklist: `ACCEPTANCE.md` (rows 84-98).

## 1. What you need before starting

| Need | Why | Notes |
|---|---|---|
| Google Cloud project with a **Web** OAuth client | Sign in with Google + Calendar/Gmail/Tasks consent | You already have this for "Reconnect Google". Same client, new redirect URI. |
| Railway project with the app service and a Postgres service | Hosts the backend and data | Public domain with https (Settings, Networking, Generate Domain). |
| `JARVIS_FERNET_KEY` already set | Encrypts Google tokens and users' API keys | **Do not change it.** Changing it makes every stored secret unreadable. |
| Your Google email | `JARVIS_OWNER_EMAIL` seeds you as user #1 | Must be the Google account you will sign in with. |
| Flutter SDK + Android SDK on your Mac | Build the APK | `flutter doctor` clean. |
| `jarvis_app/jarvis.env.json` (or the repo-root copy used so far) with `{"JARVIS_URL": "https://<railway-domain>"}` | Backend URL baked into the APK | Never put a token in it. Always pass `--dart-define-from-file=jarvis.env.json` when building. |
| A phone with sideloading allowed | Install the APK | Friends need the same APK and a Google account on the allowlist. |
| Python 3.11+ and the repo venv on your Mac | Run the `users` CLI against the Railway DB | Use the Postgres service's **public** URL, not the `railway.internal` one. |
| Docker/psql or Railway backup | `pg_dump` before the upgrade | The schema upgrade is one-way (see section 3). |

### Dependencies that matter

- **Operator-owned (stay yours):** the Google web OAuth client, Firebase/FCM credentials for push (`JARVIS_FCM_CREDENTIALS_PATH`, optional until you want push), the Fernet key, the Postgres database, optional default model ids (`JARVIS_MODELS_FAST`, `JARVIS_MODELS_STRONG`).
- **Per user (brought by each person in the app wizard):** an OpenAI-compatible LLM base URL + key (required; OpenRouter is the default), a Deepgram key (optional, voice), a Tavily key (optional, web search). The server never falls back to your keys for friends.
- **Seeded into you once, then unused:** `JARVIS_OPENROUTER_API_KEY`, `JARVIS_DEEPGRAM_API_KEY`, `JARVIS_TAVILY_API_KEY` are read a single time on first start and copied into your own account.
- **Telegram:** stays your own channel (`JARVIS_TELEGRAM_BOT_TOKEN`, `JARVIS_TELEGRAM_OWNER_CHAT_ID`). Friends get push notifications only.
- **App packages:** `app_links` (deep link), `flutter_secure_storage` (token), Firebase messaging (already present). `flutter pub get` handles them.

## 2. Google Cloud console (by hand)

1. APIs & Services, Credentials, open the **Web** OAuth client. Under **Authorized redirect URIs** add `https://<railway-domain>/auth/callback`. Exact match: https, no trailing slash. Keep the old URI until everything works.
2. OAuth consent screen, **Scopes**, make sure these are present:
   - `openid`
   - `https://www.googleapis.com/auth/userinfo.email` (the full URL, not the bare `email`)
   - your existing Calendar, Tasks, `gmail.readonly`, `gmail.compose`
3. OAuth consent screen, **Publishing status**: click **Publish app** so it is **In production**. Do not submit for verification.
   - Friends see "Google hasn't verified this app" once: Advanced, then Continue.
   - Unverified apps are capped at 100 users; the `users` CLI enforces the same cap.
   - In Testing mode, only listed test users can sign in and refresh tokens expire after 7 days, so use production.
   - Google's help page does not confirm refresh-token lifetime for unverified production apps. Check that a token still works after a week (ACCEPTANCE row 96).

## 3. Railway

1. **Back up first:** `pg_dump` the Railway database. The upgrade adds `user_id` to every data table with NOT NULL and composite keys. The old image cannot write to the upgraded schema, so rolling back needs the dump.
2. Set variables (type them yourself, never paste secrets into chat):
   ```
   JARVIS_OWNER_EMAIL=<your google email>
   JARVIS_PUBLIC_BASE_URL=https://<railway-domain>
   JARVIS_GOOGLE_WEB_CLIENT_SECRETS=<web OAuth client JSON>
   ```
   plus the variables you already have (database URL, Fernet key, Telegram, timezone, operator keys, models).
   Without `JARVIS_OWNER_EMAIL`, start fails when pre-multi-user data exists.
3. Deploy (push to the connected branch). First start creates you as user #1, adopts existing memories, notes, devices, your Google token, and renames the `owner` conversation thread to `user:<id>`. Your existing app install keeps working with its current token.
4. Check `https://<railway-domain>/health`.
5. Caddy must proxy the new public paths: `/auth/start`, `/auth/callback`, `/auth/claim`, `/me`, `/settings/*` (already in the repo `Caddyfile`).

## 4. Add people

Run from your Mac against the Railway DB (public URL):

```
JARVIS_DATABASE_URL='<Postgres DATABASE_PUBLIC_URL>' JARVIS_FERNET_KEY='<same key>' \
  python -m jarvis.users add friend@gmail.com --tz Europe/Berlin
python -m jarvis.users list
python -m jarvis.users disable friend@gmail.com     # revokes devices; 401 on next call
python -m jarvis.users enable friend@gmail.com
python -m jarvis.users delete friend@gmail.com      # dry run; add --yes to delete
```

Emails are stored lowercase. The owner cannot be disabled or deleted.

## 5. Build and install the app

```
cd jarvis_app
flutter pub get
flutter build apk --release --dart-define-from-file=jarvis.env.json
```

Install the APK on your phone and on the second phone. First launch with no stored token shows the wizard:
1. Sign in with Google
2. Connect Google (unverified warning: Advanced, Continue)
3. LLM base URL + key, tested before saving
4. Optional Deepgram and Tavily keys (skippable)

Chat stays locked until step 3 passes. Settings has the same inputs to change or remove a key.

Notes:
- Your existing install keeps its current token. Signing in again adds a new device and leaves the old token valid.
- Debug build for quick trials: `flutter build apk --debug --dart-define-from-file=jarvis.env.json`.

## 6. Known limits and risks

- No rate limit on `/auth/start`. Add a per-IP limit at the proxy before opening this up. Over 1000 live sign-in attempts it returns 503.
- `/auth/callback?code=` puts Google's one-time code in access logs. Filter the query string at the proxy.
- Friends do not get mail-to-calendar auto-events (owner only until the app has an Undo).
- Retired per-user runtimes are only released on restart (bounded by the 100-user cap).
- Weather city and brief time per user have no in-app setting yet; friends without a stored location get no weather line.
- Friends see the Google "unverified app" screen; you cannot remove it without paid Google verification.

## 7. Quick verification

1. You: sign in on the phone, run the wizard, chat. Your old data and history are there.
2. Friend: add their email with the CLI, sign in on the second phone, finish the wizard, chat about their own calendar.
3. Disable the friend: their next request returns 401.
4. A friend without a Deepgram key sees "Add your Deepgram key in Settings to enable voice".
5. Optional: Telegram still works for you only.
