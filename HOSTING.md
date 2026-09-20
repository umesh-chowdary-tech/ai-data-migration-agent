# Hosting the demo

The app is one container: a FastAPI server that serves the built UI, the agent, and the mock target system. It
keeps its data in files (SQLite + uploaded run files), so give it a small persistent disk and run **one** instance -
run state and the live activity feed live in the process.

## Render (free plan, no card)

Push the repo, then in Render: **New → Blueprint** → pick this repo → it reads `render.yaml`. Set `GROQ_API_KEY`
and `OPENROUTER_API_KEY` in the dashboard. The free plan has no persistent disk and spins the service down when it
goes unused, so the first click after a long idle waits while it wakes.

## Hugging Face Spaces (needs PRO)

**Only static Spaces are free.** Gradio and Docker Spaces on free `cpu-basic` hardware require a PRO subscription
(~$9/month), and ZeroGPU hardware refuses to run anything that isn't a GPU Gradio app
(`No @spaces.GPU function detected`). With PRO it works well: the Space runs [`app.py`](app.py), which serves this
app's UI and API with no build step, because `frontend/dist` is committed.

1. Create an account at huggingface.co, then **New Space** → name it, **SDK: Gradio**, **Blank** template, public,
   hardware **CPU basic** (needs PRO).
2. Create a **write** access token: Settings → Access Tokens.
3. Publish this repo into it (only committed files are published):

   ```bash
   powershell -ExecutionPolicy Bypass -File deploy\huggingface\publish.ps1 -Space <user>/<space>   # Windows
   bash deploy/huggingface/publish.sh <user>/<space>                                               # macOS / Linux
   ```

   Git asks for your username and the token (use the token as the password).
4. In the Space: **Settings → Variables and secrets** → add `GROQ_API_KEY` and `OPENROUTER_API_KEY` as **secrets**,
   and `MAX_RUNS_PER_HOUR = 20` as a variable. Without the keys the app still runs and says "No AI involved".
5. Watch **Logs** until it says `Uvicorn running`, then open the Space.

Re-publishing later is the same one command. The Space card lives in
[`deploy/huggingface/README.md`](deploy/huggingface/README.md); the script copies it over the project README.

If you ever upgrade to a Docker Space (paid), use [`deploy/huggingface/Dockerfile`](deploy/huggingface/Dockerfile)
instead and set the SDK to `docker`.

## Fly.io (stays warm, keeps its data, ~$0-5/month)

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
| `PORT` | the port the server binds to (platforms assign one). The agent reaches its mock target in-process, so no extra wiring |
| `TARGET_API_URL` | only if you point the push step at a real API instead of the built-in stub |
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
