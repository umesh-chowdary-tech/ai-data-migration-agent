"""Entry point for a Hugging Face Space (Gradio SDK, free tier).

The Space runs `python app.py`, so this serves the agent's own FastAPI app - the React UI (pre-built in
`frontend/dist`), the API, and the mock target system - on the port the Space expects.

Note: don't create a `gr.Blocks` here. Inside a Space, Gradio starts its own server on the same port, and this one
then can't bind it.
"""
from __future__ import annotations

import os

import uvicorn

from backend.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "7860")))
