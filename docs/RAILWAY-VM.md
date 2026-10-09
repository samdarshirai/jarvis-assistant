# Run Jarvis backend on a throwaway Railway VM (Dockerfile + Railway Postgres)

For repeatable deploys (push to git = redeploy) use `docs/DEPLOY-RAILWAY.md` instead. This VM is a 60 min test sandbox.

Every block says where to run it: **MAC** = your local terminal, **VM** = the `/app ❯` shell.
The free VM expires 60 min after creation, so claim it first (step 1).

## 1. Claim the VM (browser)

Open: https://railway.com/ssh-signup?code=LAfgMC0WPP4TN_mIx0yHzQ

Sign in / sign up and confirm. Skipping this loses the VM when the 60 min end.

## 2. Copy the source to the VM

**MAC**
```
scp /private/tmp/claude-501/-Users-ronalisenapati-Ronali-jarvis/ec4beaa6-9af9-4b07-b6f3-6a65785aaa87/scratchpad/jarvis.tgz railway.new:/app/
```
If `scp` fails:
```
ssh railway.new 'cat > /app/jarvis.tgz' < /private/tmp/claude-501/-Users-ronalisenapati-Ronali-jarvis/ec4beaa6-9af9-4b07-b6f3-6a65785aaa87/scratchpad/jarvis.tgz
```

## 3. Open the VM and unpack

**MAC**
```
ssh railway.new
```
Wait for the `/app ❯` prompt, then:

**VM**
```
cd /app && tar xzf jarvis.tgz
```

## 4. Check Docker exists

**VM**
```
docker --version
```
- Prints a version: continue with step 5.
- `command not found`: skip to step 7 (no Docker, run with pip + uvicorn).

## 5. Create the env file (type secrets yourself, do not paste them into Claude)

**VM**
```
cat > /app/.env <<'EOF'
JARVIS_OPENROUTER_API_KEY=
JARVIS_MODELS_FAST=
JARVIS_MODELS_STRONG=
JARVIS_DATABASE_URL=postgresql://USER:PASS@HOST:PORT/railway   # Railway Postgres, Variables tab: DATABASE_PUBLIC_URL
JARVIS_TELEGRAM_BOT_TOKEN=
JARVIS_TELEGRAM_OWNER_CHAT_ID=
JARVIS_FERNET_KEY=
JARVIS_GOOGLE_CLIENT_SECRETS=
JARVIS_TIMEZONE=
EOF
nano /app/.env    # fill in the values, save with Ctrl+O, exit with Ctrl+X
```
Notes:
- Generate the Fernet key: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `client_secret.json` is not on the VM. Google features fail until you copy it
  (**MAC**: `scp /Users/ronalisenapati/Ronali/jarvis/client_secret.json railway.new:/app/secrets/`).

## 6. Build and run the Dockerfile

**VM**
```
cd /app
docker compose -f docker-compose.prod.yml up -d --build app
docker compose -f docker-compose.prod.yml logs -f app
```
Builds the Dockerfile and starts only `app` (no Caddy: the preview URL already does TLS). The container listens on 8000; compose maps it to `$PORT`.
Update after code change: re-run the `up` line. Restart after editing `.env`: same line.
Test: https://preview-e95bcd07ed83ffe5.up.railway.app/health

## 7. No Docker? Run directly

**VM**
```
cd /app
pip install .
set -a; . ./.env; set +a
uvicorn jarvis.main:app --host 0.0.0.0 --port $PORT
```

## Troubleshooting

- Wrong machine: prompt says `ronalisenapati@Mac` means you are on the Mac, not the VM.
- `Permission denied (publickey)`: `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519`, then reconnect.
- DB connection errors: from the VM use the Postgres service `DATABASE_PUBLIC_URL`; the `railway.internal` host only resolves inside Railway services.
- `PORT` empty in the VM shell: compose falls back to 8000; run `echo $PORT` to check which port the preview URL serves.
