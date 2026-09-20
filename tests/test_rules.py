"""Hand-written rules: validated like decisions, impact measured on real data, AI-reviewed with citations,
saved only exactly as reviewed, and fully traceable. Plus the conflict card's "a different value" option."""
import pytest

from backend import db
from backend.agent import escalations as esc
from backend.agent import llm as llm_module
from backend.agent import resolve, rule_review, rules
from backend.agent.llm import AIResult
from backend.agent.records import Record
from tests.test_end_to_end import new_run, open_by_type


def facts_text(rec) -> str:
    return " | ".join(f["text"] for f in rec["facts"])


def answer_column_questions(run, remember=False):
    blocking = open_by_type(run)
    resolve.resolve(blocking["mapping"][0]["id"], "correct", "phone", "", remember, "Tester")
    resolve.resolve(blocking["date_format"][0]["id"], "approve", None, "", remember, "Tester")


# --- what may be written by hand ---------------------------------------------------------------

def test_rules_about_one_employee_cannot_be_added_by_hand(isolated):
    for kind in ("skip_record", "record_value", "duplicate"):
        with pytest.raises(rule_review.RuleError):
            rule_review.review({"kind": kind, "value": "x"})


def test_invalid_rules_are_rejected(isolated):
    bad = [{"kind": "column_map", "header": "Dept", "target": "salary_hack"},
           {"kind": "value_map", "field": "department", "raw": "Special Projects", "value": "Space Force"},
           {"kind": "value_map", "field": "phone", "raw": "98765", "value": "12345"},
           {"kind": "date_format", "header": "DOJ", "format": "YMD"},
           {"kind": "source_priority", "field": "not_a_field", "file": "x.csv"}]
    for p in bad:
        with pytest.raises(rule_review.RuleError):
            rule_review.review(p)


# --- impact measured on real data --------------------------------------------------------------

def test_a_rule_the_data_already_decides_is_flagged_as_not_needed(isolated):
    new_run("v1")
    rec = rule_review.review({"kind": "column_map", "header": "Emp ID", "target": "employee_id"})
    assert rec["needed"] == "no" and rec["needs_acknowledgement"]
    assert "already reaches the same answer" in facts_text(rec)


def test_a_rule_overriding_the_evidence_must_be_acknowledged(isolated):
    new_run("v1")
    rec = rule_review.review({"kind": "column_map", "header": "Emp ID", "target": "annual_salary"})
    assert rec["needed"] == "overrides"
    assert any(f["level"] == "warn" and "0% of the values fit" in f["text"] for f in rec["facts"])
    with pytest.raises(rule_review.RuleError):
        rule_review.save(rec["id"], "client says so", "Tester", acknowledge=False)
    saved = rule_review.save(rec["id"], "client says so", "Tester", acknowledge=True)
    assert saved["origin"] == "manual" and saved["reason"] == "client says so"
    event = rules.history(saved["id"])[-1]
    assert event["action"] == "created" and event["review"]["acknowledged_warnings"] is True


def test_a_useful_rule_is_recognised_with_its_real_impact(isolated):
    run = new_run("v1")
    rec = rule_review.review({"kind": "column_map", "header": "Contact Number", "target": "phone"})
    assert rec["needed"] == "yes"
    answer_column_questions(run)
    rec = rule_review.review({"kind": "value_map", "field": "department", "raw": "Special Projects",
                              "value": "Operations"})
    assert rec["needed"] == "yes"
    assert "2 employee(s)" in facts_text(rec) and "EMP0023" in facts_text(rec)


def test_a_date_rule_on_a_proven_column_is_flagged(isolated):
    new_run("v1")
    rec = rule_review.review({"kind": "date_format", "header": "DOJ", "format": "MDY"})
    assert rec["needed"] == "no" and "contradicts the data" in facts_text(rec)


def test_a_manual_rule_takes_effect_on_the_next_run(isolated):
    run = new_run("v1")
    answer_column_questions(run)
    rec = rule_review.review({"kind": "value_map", "field": "department", "raw": "Special Projects",
                              "value": "Operations"})
    rule_review.save(rec["id"], "confirmed with client HR", "Tester", acknowledge=rec["needs_acknowledgement"])
    db.execute("DELETE FROM escalations")  # start clean, keep the rule
    run2 = new_run("v1")
    answer_column_questions(run2)
    assert "unknown_value" not in open_by_type(run2)
    assert Record.load(run2, "EMP0023").data["department"] == "Operations"


# --- the AI review: advisory and grounded ------------------------------------------------------

class ReviewerLLM:
    available, model, provider, label, providers = True, "reviewer", "fake", "reviewer via test", []

    def status(self):
        return {"mode": "ai", "label": self.label, "reason": None, "providers": []}

    def reason_unavailable(self):
        return ""

    def complete_json(self, system, user, max_tokens=4000):
        return AIResult({"verdict": "maybe", "needed": "yes", "summary": "Looks risky.",
                         "effects": [{"text": "Maps IDs to salary", "cites": ["F1", "F99"]}],
                         "risks": [{"text": "Invented claim", "cites": ["F42"]}, "plain string risk"]}, "Fake", "reviewer")


def test_ai_review_citations_are_checked_and_verdicts_sanitised(isolated, monkeypatch):
    new_run("v1")
    monkeypatch.setattr(llm_module, "_instance", ReviewerLLM())
    rec = rule_review.review({"kind": "column_map", "header": "Emp ID", "target": "annual_salary"})
    ai = rec["ai"]
    assert ai["available"] and ai["verdict"] == "caution"          # unknown verdict -> caution
    assert ai["effects"][0]["cites"] == ["F1"]                     # F99 doesn't exist -> stripped
    assert ai["risks"][0]["cites"] == [] and ai["risks"][1]["cites"] == []


def test_without_ai_the_review_is_the_data_checks_alone(isolated):
    new_run("v1")
    rec = rule_review.review({"kind": "column_map", "header": "Contact Number", "target": "phone"})
    assert rec["ai"]["available"] is False and rec["facts"]
    rule_review.save(rec["id"], "client confirmed", "Tester", acknowledge=rec["needs_acknowledgement"])


# --- saving exactly what was reviewed ----------------------------------------------------------

def test_only_a_reviewed_proposal_can_be_saved(isolated):
    with pytest.raises(rule_review.RuleError):
        rule_review.save("made-up-review-id", "because", "Attacker", acknowledge=True)


def test_editing_a_rule_that_changed_since_review_is_refused(isolated):
    run = new_run("v1")
    answer_column_questions(run, remember=True)                  # creates rules from decisions
    rule = rules.find("column_map", "contact number")
    assert rule["origin"] == "decision"
    rec = rule_review.review({"rule_id": rule["id"], "target": "emergency_contact_phone"})
    rules.save("column_map", "contact number", "personal_email", "someone else's edit", "Other", None)
    with pytest.raises(rule_review.RuleError):
        rule_review.save(rec["id"], "switching to emergency contact", "Tester", acknowledge=True)


def test_rule_history_records_create_edit_delete(isolated):
    run = new_run("v1")
    answer_column_questions(run, remember=True)
    rule = rules.find("date_format", "joining date")
    rec = rule_review.review({"rule_id": rule["id"], "format": "DMY"})
    rule_review.save(rec["id"], "client clarified the format", "Tester", acknowledge=True)
    rules.delete(rule["id"], "Tester", "no longer needed")
    assert [e["action"] for e in rules.history(rule["id"])] == ["created", "edited", "deleted"]


# --- the scope of "remember": this employee vs this kind of problem ----------------------------

def phone_card(run):
    return open_by_type(run)["invalid_value"][0]


def test_remembering_a_typed_fix_is_specific_to_that_employee(isolated):
    run = new_run("v1")
    answer_column_questions(run)
    resolve.resolve(phone_card(run)["id"], "correct", {"phone": "9876501234"}, "", True, "Tester")
    assert rules.find("record_value", "EMP0017|phone")["value"] == "+919876501234"
    assert not rules.get_all("value_map")   # never "anyone whose phone is 98765 gets EMP0017's number"


def test_leave_empty_can_be_remembered_as_a_problem_rule_for_everyone(isolated):
    from tests.test_security import hrms_with, run_with_files, sample_files
    run = new_run("v1")
    answer_column_questions(run, remember=True)
    card = phone_card(run)
    labels = [o["label"] for o in resolve.remember_options(card)]
    assert labels[0].startswith("Only for EMP0017") and "isn't a valid mobile number" in labels[1]
    resolve.resolve(card["id"], "approve", None, "", True, "Tester", scope="problem")   # proposal: leave it empty
    assert rules.find("issue_policy", "phone|bad_phone")["description"].startswith("Whenever mobile number")

    def other_bad_phone(df):  # a different employee, a different bad number
        df.loc[df["Emp ID"] == "EMP0020", "Mobile No"] = "12345"
    db.execute("DELETE FROM escalations")
    run2 = run_with_files(sample_files(**{"hrms_legacy_export.csv": hrms_with(other_bad_phone)}))
    assert "invalid_value" not in open_by_type(run2)                  # nobody is asked about either phone
    rec = Record.load(run2, "EMP0020")
    assert rec.data.get("phone") is None and any("cleared by your rule" in c["reason"] for c in rec.changes)


def test_problem_scope_is_refused_when_a_real_value_was_typed(isolated):
    run = new_run("v1")
    answer_column_questions(run)
    with pytest.raises(resolve.InputError):
        resolve.resolve(phone_card(run)["id"], "correct", {"phone": "9876501234"}, "", True, "Tester", scope="problem")


def test_required_fields_never_get_a_problem_rule(isolated):
    run = new_run("v1")
    answer_column_questions(run)
    card = next(e for e in open_by_type(run)["validation"] if e["record_keys"] == ["EMP0029"])
    assert [o["value"] for o in resolve.remember_options(card)] == ["record"]   # work email can't be left empty
    with pytest.raises(rule_review.RuleError):
        rule_review.review({"kind": "issue_policy", "field": "email", "problem": "email_format"})


def test_unknown_manager_rule_clears_the_manager_at_push_time(isolated):
    run = new_run("v1")
    answer_column_questions(run, remember=True)
    card = next(e for e in open_by_type(run)["push_failure"] if e["record_keys"] == ["EMP0042"])
    resolve.resolve(card["id"], "approve", None, "", True, "Tester", scope="problem")
    assert rules.find("issue_policy", "manager_email|manager_not_found")
    isolated.post("/admin/reset")                                     # empty target, same rules
    db.execute("DELETE FROM escalations")
    run2 = new_run("v1")
    assert not any(e["record_keys"] == ["EMP0042"] for e in esc.list_for_run(run2))
    rec = Record.load(run2, "EMP0042")
    assert rec.status == "pushed" and rec.data.get("manager_email") is None


def test_an_old_exact_value_rule_can_be_made_generic(isolated):
    old = rules.save("value_map", "phone|98765", None, "Mobile number '98765' -> (leave empty)", "Tester", 1)
    g = rule_review.generalisation(old)
    assert g["problem"] == "bad_phone" and g["replaces"] == old
    rec = rule_review.review({"kind": "issue_policy", "field": g["field"], "problem": g["problem"], "replaces": old})
    assert "Replaces the exact-value rule" in facts_text(rec)
    new = rule_review.save(rec["id"], "make it apply to everyone", "Tester", acknowledge=True)
    assert new["kind"] == "issue_policy" and rules.get(old) is None
    assert rules.history(old)[-1]["reason"].startswith("replaced by the generic rule")


def test_problem_rule_review_shows_where_it_would_have_lost_data(isolated):
    run = new_run("v1")
    answer_column_questions(run)
    resolve.resolve(phone_card(run)["id"], "correct", {"phone": "9876501234"}, "", False, "Tester")
    rec = rule_review.review({"kind": "issue_policy", "field": "phone", "problem": "bad_phone"})
    assert rec["needed"] == "yes" and "EMP0017" in facts_text(rec)
    assert "would have been lost" in facts_text(rec) and rec["needs_acknowledgement"]


def test_problem_rule_review_finds_cases_on_cards_from_older_versions(isolated):
    run = new_run("v1")
    answer_column_questions(run)
    card = phone_card(run)
    ctx = dict(card["context"])
    ctx.pop("code")  # cards created before the problem type was stored
    db.execute("UPDATE escalations SET context_json=? WHERE id=?", (db.dumps(ctx), card["id"]))
    old_card = esc.get(card["id"])
    assert [o["value"] for o in resolve.remember_options(old_card)] == ["record", "problem"]
    resolve.resolve(card["id"], "approve", None, "", False, "Tester")
    rec = rule_review.review({"kind": "issue_policy", "field": "phone", "problem": "bad_phone"})
    assert rec["needed"] == "yes" and "also chose to leave it empty" in facts_text(rec)


def test_empty_values_are_described_as_leave_empty(isolated):
    run = new_run("v1")
    answer_column_questions(run)
    resolve.resolve(phone_card(run)["id"], "approve", None, "", True, "Tester")    # leave empty, this employee
    assert rules.find("record_value", "EMP0017|phone")["description"] == "EMP0017 Mobile number = (leave empty)"


# --- conflict card: a value that is in no file --------------------------------------------------

def test_conflict_other_value_needs_a_valid_value_and_a_note(isolated):
    run = new_run("v1")
    answer_column_questions(run)
    card = open_by_type(run)["conflict"][0]
    with pytest.raises(resolve.InputError):  # no note
        resolve.resolve(card["id"], "correct", {"other": "2018-06-20"}, "", False, "Tester")
    with pytest.raises(resolve.InputError):  # not a date
        resolve.resolve(card["id"], "correct", {"other": "sometime in June"}, "checked the offer letter", False, "Tester")
    resolve.resolve(card["id"], "correct", {"other": "2018-06-20"}, "offer letter says 20 June", False, "Tester")
    rec = Record.load(run, "EMP0015")
    assert rec.data["date_of_joining"] == "2018-06-20"
    assert "not from any source file" in rec.changes[-1]["reason"] and rec.status == "pushed"
    assert esc.get(card["id"])["status"] == "corrected"
