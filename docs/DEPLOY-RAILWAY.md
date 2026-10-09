# Deploy Jarvis backend on Railway (push to git = redeploy)

Railway builds the repo `Dockerfile` on every push to the connected branch. No SSH, no scp.

## One-time setup

1. Railway dashboard: New Project, Deploy from GitHub repo, pick this repo and branch (`main`).
2. Service, Variables. Type the values yourself, never paste secrets into Claude:
   ```
   JARVIS_OPENROUTER_API_KEY
   JARVIS_MODELS_FAST
   JARVIS_MODELS_STRONG
   JARVIS_DATABASE_URL        # Neon URL, ...?sslmode=require, direct host (no -pooler) if possible
   JARVIS_TELEGRAM_BOT_TOKEN
   JARVIS_TELEGRAM_OWNER_CHAT_ID
   JARVIS_FERNET_KEY
   JARVIS_TIMEZONE
   ```
   Fernet key: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
   Railway injects `PORT`; the Dockerfile honors it. Do not set it yourself.
3. Settings, Networking, Generate Domain.
4. Settings, Deploy, Healthcheck Path = `/health` (a broken build never replaces the working one).
5. Check `https://<domain>/health`.

## Google authorization (once, from your Mac)

The server reads the encrypted Google token from Neon, so `client_secret.json` never goes to Railway.
Run the consent locally against the same DB and key as Railway:

```
JARVIS_DATABASE_URL='<same Neon URL>' JARVIS_FERNET_KEY='<same key>' python -m jarvis.google.auth
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
