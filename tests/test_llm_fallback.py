"""AI provider chain: Groq -> OpenRouter fallback, and a clear 'no AI involved' state when both fail.

Uses a fake HTTP transport - no network, no real keys."""
import json

import httpx

from backend import db
from backend.agent import llm as llm_module
from backend.agent.llm import LLM
from tests.test_end_to_end import new_run, open_by_type

OK_BODY = {"mappings": []}


def completion(content: str) -> dict:
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def make_llm(behaviour: dict[str, tuple[int, object]], calls: list | None = None) -> LLM:
    """behaviour: host -> (status, body). body may be a dict (JSON) or str (raw assistant text)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.host)
        status, body = behaviour[request.url.host]
        if status == 200:
            text = body if isinstance(body, str) else json.dumps(body)
            return httpx.Response(200, json=completion(text))
        return httpx.Response(status, json={"error": {"message": str(body), "code": str(status)}})

    chain = [
        {"name": "groq", "label": "Groq", "base_url": "https://groq.test/v1", "model": "gone-model", "api_key": "k1",
         "model_env": "GROQ_MODEL", "key_env": "GROQ_API_KEY"},
        {"name": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.test/v1", "model": "llama",
         "api_key": "k2", "model_env": "OPENROUTER_MODEL", "key_env": "OPENROUTER_API_KEY"},
    ]
    return LLM(chain=chain, http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_primary_down_secondary_answers():
    llm = make_llm({"groq.test": (404, "model not found"), "openrouter.test": (200, OK_BODY)})
    res = llm.complete_json("s", "u")
    assert res.data == OK_BODY and res.provider == "OpenRouter" and res.fell_back
    assert res.failures[0]["kind"] == "model" and "GROQ_MODEL" in res.failures[0]["error"]
    assert llm.mode == "fallback" and llm.label == "llama via OpenRouter"


def test_failed_provider_is_skipped_during_cooldown():
    calls: list = []
    llm = make_llm({"groq.test": (401, "bad key"), "openrouter.test": (200, OK_BODY)}, calls)
    llm.complete_json("s", "u")
    llm.complete_json("s", "u")
    assert calls.count("groq.test") == 1  # not hammered again after a bad key
    assert calls.count("openrouter.test") == 2


def test_garbage_output_falls_back():
    llm = make_llm({"groq.test": (200, "Sure! Here is the mapping you asked for"), "openrouter.test": (200, OK_BODY)})
    res = llm.complete_json("s", "u")
    assert res.provider == "OpenRouter" and res.failures[0]["kind"] == "bad_output"


def test_json_generation_failure_is_one_off_not_an_outage():
    calls: list = []
    llm = make_llm({"groq.test": (400, "Failed to generate JSON. Please adjust your prompt."),
                    "openrouter.test": (200, OK_BODY)}, calls)
    res = llm.complete_json("s", "u")
    assert res.provider == "OpenRouter" and res.failures[0]["kind"] == "bad_output"
    llm.complete_json("s", "u")
    assert calls.count("groq.test") == 2  # tried again on the next call - no cool-down for one bad answer


def test_invalid_model_400_is_treated_as_model_problem():
    llm = make_llm({"groq.test": (200, OK_BODY),
                    "openrouter.test": (400, "also-not-a-real-model is not a valid model ID")})
    llm.providers = llm.providers[1:]
    res = llm.complete_json("s", "u")
    assert res.failures[0]["kind"] == "model" and "OPENROUTER_MODEL" in res.failures[0]["error"]


def test_answer_nested_in_prompt_echo_is_found():
    from backend.agent.llm import dig
    echoed = {"items": [{"value": "x"}], "output_format": {"suggestions": [{"value": "x", "suggestion": "Operations"}]}}
    assert dig(echoed, "suggestions") == [{"value": "x", "suggestion": "Operations"}]
    assert dig({"suggestions": []}, "suggestions") == []
    assert dig({"nothing": 1}, "suggestions") is None


def test_both_down_means_no_ai():
    llm = make_llm({"groq.test": (401, "bad key"), "openrouter.test": (402, "no credits")})
    res = llm.complete_json("s", "u")
    assert res.data is None and len(res.failures) == 2
    status = llm.status()
    assert status["mode"] == "none" and not status["available"]
    assert status["label"].startswith("No AI involved")
    assert "API key rejected" in status["reason"] and "no credits" in status["reason"]


def test_recheck_recovers():
    behaviour = {"groq.test": (503, "down"), "openrouter.test": (503, "down")}
    llm = make_llm(behaviour)
    assert llm.check()["mode"] == "none"
    behaviour["groq.test"] = (200, {"ok": True})
    assert llm.check()["mode"] == "ai"


def test_run_without_any_ai_still_completes_and_says_so(isolated, monkeypatch):
    monkeypatch.setattr(llm_module, "_instance",
                        make_llm({"groq.test": (401, "bad key"), "openrouter.test": (500, "boom")}))
    run = new_run("v1")
    ai = db.loads(db.one("SELECT ai_json FROM runs WHERE id=?", (run,))["ai_json"])
    assert ai["mode_at_start"] == "none" and ai["calls"] == 0
    events = [e["message"] for e in db.query("SELECT message FROM events WHERE run_id=?", (run,))]
    assert any(m.startswith("No AI involved in this run") for m in events)
    # rules-only behaviour is unchanged: the same two column-level questions
    assert {e["context"]["column"] for es in open_by_type(run).values() for e in es} == {"Contact Number", "Joining Date"}


def test_run_with_fallback_records_who_answered(isolated, monkeypatch):
    mapping = {"mappings": [{"column": "Contact Number", "target": "phone", "confidence": 0.9, "reason": "phone"}]}
    monkeypatch.setattr(llm_module, "_instance",
                        make_llm({"groq.test": (404, "model not found"), "openrouter.test": (200, mapping)}))
    run = new_run("v1")
    ai = db.loads(db.one("SELECT ai_json FROM runs WHERE id=?", (run,))["ai_json"])
    assert ai["mode_at_start"] == "fallback"
    assert ai["used"] == {"llama via OpenRouter": 3}  # one mapping call per file
