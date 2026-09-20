#!/usr/bin/env bash
# Start the Migration Agent on macOS / Linux: creates the virtual environment, installs dependencies, starts the app.
#   bash run.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip -q
  ./.venv/bin/python -m pip install -r requirements.txt
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env - add a free Groq key for AI suggestions (the app also runs without one)."
fi
echo
echo "Migration Agent is starting on http://localhost:8000  (Ctrl+C to stop)"
echo
exec ./.venv/bin/python -m uvicorn backend.main:app --port 8000
