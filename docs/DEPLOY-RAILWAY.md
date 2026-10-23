# Deploy Jarvis backend on Railway (push to git = redeploy)

Railway builds the repo `Dockerfile` on every push to the connected branch. No SSH, no scp.

## One-time setup

1. Railway dashboard: New Project, Deploy from GitHub repo, pick this repo and branch (`main`).
2. Same project: New, Database, Add PostgreSQL. Tables are created on first start.
3. App service, Variables. Type the values yourself, never paste secrets into Claude:
   ```
   JARVIS_OWNER_EMAIL         # your Google email: seeds user #1 on first start
   JARVIS_PUBLIC_BASE_URL     # https://<domain> from step 4
   JARVIS_GOOGLE_WEB_CLIENT_SECRETS  # web OAuth client JSON
   JARVIS_OPENROUTER_API_KEY  # operator keys: seeded into the owner once, never used for friends
   JARVIS_MODELS_FAST
   JARVIS_MODELS_STRONG
   JARVIS_DATABASE_URL        # reference variable: ${{Postgres.DATABASE_URL}} (use your DB service name)
   JARVIS_TELEGRAM_BOT_TOKEN
   JARVIS_TELEGRAM_OWNER_CHAT_ID
   JARVIS_FERNET_KEY
   JARVIS_TIMEZONE
   ```
   Fernet key: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
   Railway injects `PORT`; the Dockerfile honors it. Do not set it yourself.
4. Settings, Networking, Generate Domain.
5. Settings, Deploy, Healthcheck Path = `/health` (a broken build never replaces the working one).
6. Check `https://<domain>/health`.

## Google authorization (once, from your Mac)

The server reads the encrypted Google token from the Railway Postgres DB, so `client_secret.json` never goes to Railway.
`DATABASE_URL` uses the private `railway.internal` host, which only works inside Railway, so locally use the Postgres service's `DATABASE_PUBLIC_URL`.
Run the consent locally against the same DB and key as Railway:

```
JARVIS_DATABASE_URL='<Postgres DATABASE_PUBLIC_URL>' JARVIS_FERNET_KEY='<same key>' python -m jarvis.google.auth
```
Needs `client_secret.json` in the repo root. Redo only if the token is revoked or the Fernet key changes.

## Multi-user prerequisites (by hand, in this order)

Friends bring their own LLM key (and optionally Deepgram and Tavily); they sign in with Google from the app.

1. Google Cloud console, Credentials, the **web** OAuth client: add `<JARVIS_PUBLIC_BASE_URL>/auth/callback` as an authorized redirect URI.
2. OAuth consent screen, Scopes: `openid`, `https://www.googleapis.com/auth/userinfo.email`, plus the existing Calendar, Tasks, `gmail.readonly` and `gmail.compose`.
3. Publishing status: **In production**, without verification. Friends see "Google hasn't verified this app" once (Advanced, then Continue).
   Google caps unverified apps at 100 new users; the users CLI enforces the same cap (100 rows, owner included).
   Checked 2026-10-20 at support.google.com/cloud/answer/7454865: the 100-user cap and the unverified screen are confirmed there; that page
   says nothing on refresh-token lifetime (the 7-day expiry is Testing-mode behaviour, not re-confirmed). Verify the token survives a week (ACCEPTANCE row 96).
4. Set `JARVIS_OWNER_EMAIL`, deploy. First start creates the owner, adopts existing rows, copies the operator keys into the owner's secrets and renames the `owner` thread to `user:<id>`.
   Without the variable, start fails if pre-multi-user data exists.
5. Add each friend (run against the Railway DB as in "Google authorization"): `python -m jarvis.users add friend@gmail.com [--tz Europe/Berlin]`.
   Others: `list`, `disable <email>` (revokes devices, 401 on the next call), `enable <email>`, `delete <email> [--yes]` (without `--yes` it only lists what would go).
6. Build and send the APK with the backend URL baked in: `flutter build apk --release --dart-define-from-file=jarvis.env.json` (no token inside).

Sign-in no longer needs `python -m jarvis.google.auth` or `python -m jarvis.voice.token`; the owner can still use them.

Hardening (not done): `/auth/start` has no rate limit, so add a per-IP limit at the proxy (Caddy rate-limit module); `/auth/callback?code=` puts Google's one-time code in access logs, so filter the query string there.

## Every deploy

`pg_dump` before deploy; the schema upgrade is one-way (NOT NULL user_id, composite keys); the old image cannot write after it. So a rollback across the multi-user upgrade means restoring that dump, not just redeploying the old image.

```
git push origin main
```
Watch the build in Railway, Deployments. Roll back from the same tab.
Env var changes in the dashboard trigger a redeploy automatically.

## FCM push (optional)

Firebase console, Project settings, Service accounts, Generate new private key. Open the downloaded JSON,
copy its whole content, and set it as the Railway variable `JARVIS_FCM_CREDENTIALS_PATH` (a value starting
with `{` is read as the JSON itself, so no file or volume is needed). Skip it if you only use Telegram.
The phone app still needs its own Firebase config (`google-services.json`) built in; that part is separate.

## Other options

- Own server with TLS: `docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build`.
