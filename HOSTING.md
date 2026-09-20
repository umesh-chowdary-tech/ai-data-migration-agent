# Hosting the demo

The app is one container: a FastAPI server that serves the built UI, the agent, and the mock target system. It
keeps its data in files (SQLite + uploaded run files), so give it a small persistent disk and run **one** instance -
run state and the live activity feed live in the process.

## Fly.io (recommended: stays warm, small volume, ~$0-5/month)

```bash
fly auth login
fly launch --no-deploy --copy-config --name migration-agent   # reuses fly.toml
fly volumes create migration_data --size 1 --region sin
fly secrets set GROQ_API_KEY=... OPENROUTER_API_KEY=...       # never commit keys
fly deploy
fly open
```

## Render

Push the repo, then **New → Blueprint** and pick `render.yaml`. Set `GROQ_API_KEY` and `OPENROUTER_API_KEY` in the
dashboard. The free plan sleeps when idle (a first click waits 30-60s); `starter` stays warm.

## Any Docker host

```bash
docker build -t migration-agent .
docker run -p 8080:8080 -v migration-data:/data \
  -e GROQ_API_KEY=... -e OPENROUTER_API_KEY=... -e MAX_RUNS_PER_HOUR=20 migration-agent
```

## Settings that matter

| Variable | Why |
|---|---|
| `AGENT_DB`, `TARGET_DB`, `RUNS_DIR` | point at the mounted disk, or the demo resets on every restart |
| `PORT` | the agent calls its own mock target API, so it must know the port it runs on |
| `GROQ_API_KEY`, `OPENROUTER_API_KEY` | AI suggestions. Without them the app runs fine and says "No AI involved" |
| `MAX_RUNS_PER_HOUR` | public-demo guard: migrations startable per visitor per hour (0 = unlimited) |
| `AGENT_STEP_DELAY` | pacing so a human can watch the feed; 0 = full speed |

## Running it open, without a login

The app has **no authentication** - fine for a link shared with a few reviewers, but be aware:

- Everyone shares one demo. Anyone can start a migration or press reset while someone else is watching.
- Visitors' clicks use **your** AI keys. Groq's free tier is rate limited (the app falls back to OpenRouter, then to
  rules-only and says so), and OpenRouter is pay-as-you-go - **set a spending cap on that key**.
- Uploads are limited to CSV/Excel, 20 MB each, max 20 files per run, and file names are sanitised.

If you later want it closed, the smallest change is HTTP Basic auth in front of `/api` plus the UI, or the host's own
access control (Fly's `fly proxy`, Cloudflare Access, Render's password protection).

## After deploying, check

1. `/api/health` returns `"ok": true` and shows your AI providers.
2. The header badge reads **AI: … via Groq** (not "No AI involved").
3. Start the sample migration: it should pause with 2 questions, then push records.
4. Restart the app (`fly apps restart`), reload: your runs are still there (the volume works).
