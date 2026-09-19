"""The LLM may propose, but it can't push a decision over the escalation boundary on its own."""
import json

from backend.agent import escalations as esc
from backend.agent import llm as llm_module
from backend.agent.llm import AIResult
from tests.test_end_to_end import new_run, open_by_type


class OverconfidentLLM:
    """Always sure of itself: maps 'Contact Number' to phone at 0.99 and suggests a department."""
    available = True
    model = "fake-llm"
    provider = "fake"
    label = "fake-llm via test"
    providers: list = []

    def status(self):
        return {"mode": "ai", "label": self.label, "reason": None, "providers": []}

    def reason_unavailable(self):
        return ""

    def complete_json(self, system, user, max_tokens=4000):
        payload = json.loads(user)
        data = None
        if "source_columns" in payload:
            data = {"mappings": [{"column": c["column"], "target": "phone" if c["column"] == "Contact Number" else None,
                                  "confidence": 0.99, "reason": "looks like a phone"} for c in payload["source_columns"]]}
        if "items" in payload:
            data = {"suggestions": [{"field": i["field"], "value": i["value"], "suggestion": "Operations",
                                     "confidence": 0.95, "reason": "projects are usually ops"} for i in payload["items"]]}
        return AIResult(data, "Fake", "fake-llm")


def test_llm_cannot_override_boundary(isolated, monkeypatch):
    monkeypatch.setattr(llm_module, "_instance", OverconfidentLLM())
    run = new_run("v1")
    blocking = open_by_type(run)
    # Even a 99%-confident AI doesn't make 'Contact Number' unambiguous: header + value evidence still split.
    assert [e["context"]["column"] for e in blocking["mapping"]] == ["Contact Number"]
    top = blocking["mapping"][0]["context"]["candidates"][0]
    assert top["llm_score"] is not None
    for e in esc.list_for_run(run, "open"):
        from backend.agent import resolve
        resolve.resolve(e["id"], "approve", None, "", False, "Tester")
    enum = open_by_type(run)["unknown_value"][0]
    # The AI's department guess is offered as a suggestion, never applied automatically.
    assert enum["proposal"]["value"] == "Operations"
    assert enum["status"] == "open"
