"""Entry point for a Hugging Face Space (Gradio SDK, free tier).

The Space runs `python app.py`, so this simply serves the agent's own FastAPI app - the React UI (pre-built in
`frontend/dist`), the API, and the mock target system - on the port the Space expects.

A small Gradio panel is mounted at /gradio so the Space also uses the SDK it was created with; the app itself is
at /. Locally you don't need Gradio at all: `python app.py` works without it.
"""
from __future__ import annotations

import os

import uvicorn

from backend.main import app

PORT = int(os.getenv("PORT", "7860"))

try:  # optional: only used inside a Gradio Space
    import gradio as gr

    with gr.Blocks(title="Migration Agent", analytics_enabled=False) as panel:
        gr.Markdown(
            "## Migration Agent\n"
            "The app is at the root of this Space. This panel only exists so the Space uses the Gradio SDK.\n\n"
            "- Source, evaluation and write-up: "
            "[github.com/umesh-chowdary-tech/ai-data-migration-agent](https://github.com/umesh-chowdary-tech/ai-data-migration-agent)"
        )
    app = gr.mount_gradio_app(app, panel, path="/gradio")
    # the app serves the React UI from a catch-all route, so the Gradio mount has to be matched first
    mounted = [r for r in app.router.routes if getattr(r, "path", "").startswith("/gradio")]
    for route in mounted:
        app.router.routes.remove(route)
    app.router.routes[0:0] = mounted
except ImportError:
    pass

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
