# Deploy Jarvis backend on Railway (push to git = redeploy)

Railway builds the repo `Dockerfile` on every push to the connected branch. No SSH, no scp.

## One-time setup

1. Railway dashboard: New Project, Deploy from GitHub repo, pick this repo and branch (`main`).
2. Same project: New, Database, Add PostgreSQL. Tables are created on first start.
3. App service, Variables. Type the values yourself, never paste secrets into Claude:
   ```
   JARVIS_OPENROUTER_API_KEY
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

## Every deploy

```
git push origin main
```
Watch the build in Railway, Deployments. Roll back from the same tab.
Env var changes in the dashboard trigger a redeploy automatically.

## FCM push (optional)

`JARVIS_FCM_CREDENTIALS_PATH` is a file path. On Railway add a Volume, upload the service-account JSON
to it, and set the variable to the mounted path. Skip it if you only use Telegram.

## Other options

- Throwaway test on a Railway VM: `docs/RAILWAY-VM.md`.
- Own server with TLS: `docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build`.
