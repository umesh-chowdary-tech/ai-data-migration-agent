"""Open-weight models behind OpenAI-compatible endpoints, as an ordered fallback chain (Groq -> OpenRouter -> ...).

For every call the first healthy provider is tried; if it fails (bad key, retired model, quota, timeout,
network, garbage output) the next one is tried. A failed provider is skipped for a cool-down period so a run
doesn't wait on the same timeout over and over. If every provider fails, the call returns no data and the
agent carries on with deterministic evidence only - and the UI says plainly that no AI is involved.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .. import config


class BadOutput(Exception):
    pass


@dataclass
class Provider:
    name: str
    label: str
    base_url: str
    model: str
    api_key: str
    model_env: str | None = None
    key_env: str | None = None
    status: str = "untested"          # untested | ok | failed | not_configured
    error: str | None = None
    error_kind: str | None = None
    checked_at: float | None = None
    retry_at: float = 0.0
    calls: int = 0
    failures: int = 0
    json_mode: bool = True
    client: Any = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def usable(self) -> bool:
        return self.configured and (self.status != "failed" or time.time() >= self.retry_at)

    def public(self) -> dict:
        return {"name": self.name, "label": self.label, "model": self.model, "status": self.status,
                "configured": self.configured, "error": self.error, "error_kind": self.error_kind,
                "checked_at": self.checked_at, "calls": self.calls, "failures": self.failures,
                "retry_in": max(0, round(self.retry_at - time.time())) if self.status == "failed" else 0}


@dataclass
class AIResult:
    """Outcome of one AI call: the data (or None) and exactly who answered / who failed on the way."""
    data: dict | None
    provider: str | None = None
    model: str | None = None
    failures: list[dict] = field(default_factory=list)   # [{provider, model, error, kind}]

    @property
    def fell_back(self) -> bool:
        return self.data is not None and bool(self.failures)

    @property
    def answered_by(self) -> str | None:
        return f"{self.model} via {self.provider}" if self.provider else None

    def failure_text(self) -> str:
        return "; ".join(f"{f['provider']}: {f['error']}" for f in self.failures)


def classify(exc: Exception, p: Provider) -> tuple[str, str, bool]:
    """(kind, plain-English message, permanent?) for an exception raised while calling provider p."""
    import openai

    host = urlparse(p.base_url).hostname
    if isinstance(exc, BadOutput):
        return "bad_output", f"answered, but not with valid JSON ({exc})", False
    if isinstance(exc, openai.AuthenticationError):
        return "auth", f"API key rejected (401) - check {p.key_env} in .env", True
    if isinstance(exc, openai.PermissionDeniedError):
        return "auth", "access denied for this key (403)", True
    if isinstance(exc, openai.NotFoundError):
        return "model", f"model '{p.model}' not found or retired (404) - set {p.model_env} in .env", True
    if isinstance(exc, openai.RateLimitError):
        return "rate_limit", "rate limit or quota reached (429)", False
    if isinstance(exc, openai.APITimeoutError):
        return "timeout", f"no answer within {config.LLM_TIMEOUT:g}s", False
    if isinstance(exc, openai.APIConnectionError):
        return "network", f"can't reach {host} (network / DNS / firewall)", False
    if isinstance(exc, openai.APIStatusError):
        code = exc.status_code
        if code == 402:
            return "quota", "no credits left on this account (402)", True
        if code >= 500:
            return "server", f"provider error ({code})", False
        text = str(exc).lower()
        if code == 400 and re.search(r"valid model|model id|model .*(not found|does not exist)", text):
            return "model", f"model '{p.model}' is not available (400) - set {p.model_env} in .env", True
        if code == 400 and "json" in text:  # e.g. Groq json_validate_failed: a bad answer, not an outage
            return "bad_output", "model couldn't produce valid JSON for this request (400)", False
        detail = ""
        try:
            detail = (exc.body or {}).get("message", "") if isinstance(exc.body, dict) else ""
        except Exception:
            pass
        return "request", f"request rejected ({code}) {detail}".strip(), False
    return "unknown", f"{type(exc).__name__}: {str(exc)[:160]}", False


def dig(data: Any, key: str) -> list | None:
    """Find the list stored under `key` anywhere in a reply - some models echo the prompt and nest the answer."""
    if isinstance(data, dict):
        if isinstance(data.get(key), list):
            return data[key]
        for v in data.values():
            found = dig(v, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for v in data:
            found = dig(v, key)
            if found is not None:
                return found
    return None


def _extract_json(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                out = json.loads(match.group(0))
                return out if isinstance(out, dict) else None
            except json.JSONDecodeError:
                return None
    return None


class LLM:
    def __init__(self, chain: list[dict] | None = None, http_client: Any = None) -> None:
        from openai import OpenAI

        self._lock = threading.Lock()
        self.providers: list[Provider] = []
        for spec in (config.provider_chain() if chain is None else chain):
            p = Provider(**{k: spec[k] for k in ("name", "label", "base_url", "model", "api_key")},
                         model_env=spec.get("model_env"), key_env=spec.get("key_env"))
            if p.configured:
                p.client = OpenAI(base_url=p.base_url, api_key=p.api_key, timeout=config.LLM_TIMEOUT, max_retries=0,
                                  **({"http_client": http_client} if http_client is not None else {}))
            else:
                p.status, p.error, p.error_kind = "not_configured", f"no API key ({p.key_env} is empty)", "config"
            self.providers.append(p)

    # --- state ------------------------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return any(p.usable for p in self.providers)

    @property
    def active(self) -> Provider | None:
        return next((p for p in self.providers if p.usable), None)

    @property
    def mode(self) -> str:
        """ai: primary provider usable | fallback: a later provider is carrying the load | none: no AI at all."""
        configured = [p for p in self.providers if p.configured]
        if not configured or not self.available:
            return "none"
        return "ai" if self.active is configured[0] else "fallback"

    @property
    def model(self) -> str:
        return self.active.model if self.active else "none"

    @property
    def provider(self) -> str:
        return self.active.label if self.active else "none"

    @property
    def label(self) -> str:
        a = self.active
        if not a:
            return "No AI involved - rules-only mode"
        return f"{a.model} via {a.label}"

    def reason_unavailable(self) -> str:
        if not self.providers:
            return "AI is switched off (LLM_PROVIDERS=none)"
        down = [p for p in self.providers if p.status in ("failed", "not_configured")]
        return "; ".join(f"{p.label}: {p.error}" for p in down) or "no provider has answered yet"

    def status(self) -> dict:
        return {"mode": self.mode, "label": self.label, "available": self.available,
                "active": self.active.name if self.active else None,
                "reason": None if self.mode == "ai" else self.reason_unavailable(),
                "providers": [p.public() for p in self.providers]}

    # --- calls ------------------------------------------------------------------------------------
    def _call(self, p: Provider, messages: list[dict], max_tokens: int) -> dict:
        kwargs: dict[str, Any] = dict(model=p.model, messages=messages, temperature=0, max_tokens=max_tokens)
        if p.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = p.client.chat.completions.create(**kwargs)
        except Exception as exc:
            import openai
            if p.json_mode and isinstance(exc, openai.BadRequestError) and "response_format" in str(exc).lower():
                p.json_mode = False          # some models don't support JSON mode - retry once without it
                kwargs.pop("response_format")
                resp = p.client.chat.completions.create(**kwargs)
            else:
                raise
        text = resp.choices[0].message.content if resp.choices else None
        data = _extract_json(text or "")
        if data is None:
            raise BadOutput("empty reply" if not text else f"reply started with {text.strip()[:40]!r}")
        return data

    def _mark_ok(self, p: Provider) -> None:
        p.status, p.error, p.error_kind, p.checked_at, p.retry_at = "ok", None, None, time.time(), 0.0

    def _mark_failed(self, p: Provider, exc: Exception) -> dict:
        kind, msg, permanent = classify(exc, p)
        p.status, p.error, p.error_kind, p.checked_at = "failed", msg, kind, time.time()
        if kind == "bad_output":
            p.retry_at = time.time()  # one bad answer: fall back for this call only, try this provider again next time
        else:
            p.retry_at = time.time() + (config.LLM_RETRY_PERMANENT_AFTER if permanent else config.LLM_RETRY_TRANSIENT_AFTER)
        p.failures += 1
        return {"provider": p.label, "model": p.model, "error": msg, "kind": kind}

    def complete_json(self, system: str, user: str, max_tokens: int = 4000) -> AIResult:
        messages = [{"role": "system", "content": system + "\nRespond with a single JSON object only."},
                    {"role": "user", "content": user}]
        failures: list[dict] = []
        with self._lock:
            for p in self.providers:
                if not p.usable:
                    if p.configured:
                        failures.append({"provider": p.label, "model": p.model, "kind": p.error_kind, "skipped": True,
                                         "error": f"{p.error} (skipped, retrying in {p.public()['retry_in']}s)"})
                    continue
                try:
                    data = self._call(p, messages, max_tokens)
                except Exception as exc:
                    failures.append(self._mark_failed(p, exc))
                    continue
                self._mark_ok(p)
                p.calls += 1
                return AIResult(data, p.label, p.model, failures)
        return AIResult(None, None, None, failures)

    def json(self, system: str, user: str, max_tokens: int = 4000) -> dict | None:
        return self.complete_json(system, user, max_tokens).data

    @property
    def last_error(self) -> str | None:
        return self.reason_unavailable() if not self.available else None

    def check(self) -> dict:
        """Actively probe every configured provider (ignores cool-downs) - used at startup and by 'Re-check'."""
        messages = [{"role": "system", "content": "Health check. Respond with a single JSON object only."},
                    {"role": "user", "content": 'Reply with {"ok": true}'}]
        with self._lock:
            for p in self.providers:
                if not p.configured:
                    continue
                try:
                    self._call(p, messages, 300)
                    self._mark_ok(p)
                except Exception as exc:
                    self._mark_failed(p, exc)
        return self.status()


_instance: LLM | None = None
_instance_lock = threading.Lock()


def get() -> LLM:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = LLM()
        return _instance


def reload_from_env() -> LLM:
    """Re-read .env so a fixed key / model takes effect on 'Re-check' without restarting the server."""
    global _instance
    from dotenv import load_dotenv

    load_dotenv(config.ROOT / ".env", override=True)
    chain = config.provider_chain()
    wanted = [(c["name"], c["base_url"], c["model"], c["api_key"]) for c in chain]
    with _instance_lock:
        have = [(p.name, p.base_url, p.model, p.api_key) for p in _instance.providers] if _instance else None
        if have != wanted:
            _instance = LLM(chain)
        return _instance
