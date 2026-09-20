"""Runtime configuration (all overridable through environment variables / .env)."""
from __future__ import annotations

import os
import pathlib

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
SAMPLES_DIR = DATA_DIR / "samples"
SCHEMA_PATH = pathlib.Path(os.getenv("TARGET_SCHEMA", DATA_DIR / "target_schema.yaml"))
RUNS_DIR = pathlib.Path(os.getenv("RUNS_DIR", ROOT / "var" / "runs"))
DB_PATH = pathlib.Path(os.getenv("AGENT_DB", ROOT / "var" / "agent.db"))
TARGET_DB_PATH = pathlib.Path(os.getenv("TARGET_DB", ROOT / "var" / "target.db"))
FRONTEND_DIST = ROOT / "frontend" / "dist"

# Where the "push to target" step sends records. Default: the stub API mounted in this same server, on whatever
# port it was started with (hosting platforms assign one through $PORT).
PORT = os.getenv("PORT", "8000")
TARGET_API_URL = os.getenv("TARGET_API_URL", f"http://127.0.0.1:{PORT}/target-api")

# Public demo guards (0 = unlimited). Keep a shared link usable without a login.
MAX_RUNS_PER_HOUR = int(os.getenv("MAX_RUNS_PER_HOUR", "0"))

# --- LLM (open-weight models through any OpenAI-compatible endpoint) -------------------------------
# LLM_PROVIDERS is an ordered fallback chain: the first provider that answers is used for each call.
PROVIDERS = {
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b",
             "key_env": "GROQ_API_KEY", "model_env": "GROQ_MODEL"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "model": "meta-llama/llama-3.3-70b-instruct", "key_env": "OPENROUTER_API_KEY",
                   "model_env": "OPENROUTER_MODEL"},
    "ollama": {"label": "Ollama (local)", "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
               "model": "qwen2.5:7b", "key_env": None, "model_env": "OLLAMA_MODEL"},
}


def provider_chain() -> list[dict]:
    raw = os.getenv("LLM_PROVIDERS") or os.getenv("LLM_PROVIDER") or "groq,openrouter"
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    if "none" in names:
        return []
    chain = []
    for name in names:
        spec = PROVIDERS.get(name)
        if not spec:
            continue
        key = os.getenv(spec["key_env"], "") if spec["key_env"] else "ollama"
        model = os.getenv(spec["model_env"]) or (os.getenv("LLM_MODEL") if len(names) == 1 else None) or spec["model"]
        chain.append({"name": name, "label": spec["label"], "base_url": spec["base_url"], "model": model,
                      "api_key": key.strip(), "model_env": spec["model_env"], "key_env": spec["key_env"]})
    return chain


LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "30"))
LLM_RETRY_TRANSIENT_AFTER = float(os.getenv("LLM_RETRY_TRANSIENT_AFTER", "60"))    # 429 / 5xx / timeouts
LLM_RETRY_PERMANENT_AFTER = float(os.getenv("LLM_RETRY_PERMANENT_AFTER", "900"))   # bad key / model gone

# Upload limits (per file / per run)
MAX_UPLOAD_BYTES = int(float(os.getenv("MAX_UPLOAD_MB", "20")) * 2**20)
MAX_UPLOAD_FILES = int(os.getenv("MAX_UPLOAD_FILES", "20"))

# Slows the agent down slightly so a human can actually watch it work in the UI (seconds per step).
STEP_DELAY = float(os.getenv("AGENT_STEP_DELAY", "0.35"))
PUSH_DELAY = float(os.getenv("AGENT_PUSH_DELAY", "0.04"))
