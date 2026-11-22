# Deployment

HackSolve is one Python process with a SQLite file and an uploads folder. **Run exactly one instance** and put
`DATA_DIR` on persistent storage. None of the platform steps below were executed from this build environment.

## Required environment in production
```
HOST=0.0.0.0   PORT=<given by the platform>   DATA_DIR=/data   APP_ENV=production
COOKIE_SECURE=1   TRUST_PROXY=1   AI_PROVIDER=auto   (plus one provider key)
```
`COOKIE_SECURE=1` needs HTTPS (all platforms below provide it). For local Docker over plain http use `-e COOKIE_SECURE=0`.

## Docker (any host)
```bash
docker build -t hacksolve .
docker run -p 8000:8000 -v hacksolve-data:/data -e COOKIE_SECURE=0 -e GEMINI_API_KEY=... hacksolve
```
The slim image has no Node.js, so the JavaScript syntax stage reports "skipped". Add `apt-get install -y nodejs` if wanted.

## Render
Use `render.yaml` (Docker service with a 1 GB disk at `/data`). Persistent disks need a paid instance; on a free instance
the database is wiped on every restart. Add your provider key in the dashboard. Health check: `/api/health`.

## Railway
New project from the repo (it detects the Dockerfile). Add a **Volume** mounted at `/data`, set the env vars above, keep
replicas at 1.

## Fly.io
`fly launch` (accept the Dockerfile), `fly volumes create data --size 1`, add `[mounts] source="data" destination="/data"` to
`fly.toml`, `fly secrets set GEMINI_API_KEY=...`, keep one machine (`fly scale count 1`).

## Plain VPS
Run `python run.py --no-browser` under systemd with the env above and put Caddy or nginx in front for HTTPS
(`reverse_proxy 127.0.0.1:8000`). Back up `DATA_DIR` (copy the folder while the app is stopped, or use `sqlite3 .backup`).

## Before you go public
Add email verification and password reset, move rate limiting to a shared store, and decide a retention policy for old
projects. Vercel-style serverless hosting is **not** suitable: the app needs a long-lived process and a writable disk.
