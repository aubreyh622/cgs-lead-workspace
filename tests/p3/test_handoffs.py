from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest

from backend.handoff_service import HandoffService
from backend.opportunity_service import OpportunityService
from backend.research_service import ResearchServiceError
from backend.rules import calculate
from server import Conflict, Store


def submit(env):
    return env[3].submit(env[6], {"request_id": "submit-1", "revision": 2})[0]


def test_explicit_pending_acceptance_then_unknown_contact_case(handoff_environment, acceptance):
    _, _, opportunities, service, qualification, lead, key = handoff_environment
    before = qualification.bootstrap()["cases"]
    pending = submit(handoff_environment)
    assert pending["receipt"]["status"] == "pending_acceptance"
    assert pending["receipt"]["case_id"] is None
    assert qualification.bootstrap()["cases"] == before
    accepted, created = service.decide(pending["receipt"]["handoff_id"], acceptance)
    assert created and accepted["case_available"]
    receipt = accepted["receipt"]
    case = qualification.get(receipt["case_id"])
    assert receipt["initial_contact_status"] == "not_contacted" and receipt["reviewer"] == "pipeline-reviewer"
    assert case["stage"] == "Not contacted" and case["contact"] is None and case["lastReply"] is None
    assert case["contactStatus"] == "not_contacted"
    assert case["owner"] == acceptance["owner_id"] and case["tasks"][0]["due"] == acceptance["due_date"]
    assert all(x is None for x in case["scores"].values()) and all(x is None for x in case["conditions"].values())
    assert case["evidence"] == [] and case["revenue"] is None and case["approval"] is None
    assert calculate(case)["coverage"] == 0 and calculate(case)["readiness"] == "More evidence needed"
    assert case["researchContext"]["opportunity"]["research_snapshot"] == lead.model_dump(mode="json")
    assert opportunities.get(key)["qualification_handoff"] == "accepted"
    assert service.get(receipt["handoff_id"]) == accepted


def test_duplicate_requests_and_repeated_acceptance_do_not_duplicate_case(handoff_environment, acceptance):
    service, key = handoff_environment[3], handoff_environment[6]
    pending = submit(handoff_environment)
    assert service.submit(key, {"request_id": "submit-1", "revision": 2}) == (pending, False)
    assert service.submit(key, {"request_id": "another-submit", "revision": 2}) == (pending, False)
    accepted, _ = service.decide(pending["receipt"]["handoff_id"], acceptance)
    assert service.decide(pending["receipt"]["handoff_id"], acceptance) == (accepted, False)
    assert service.decide(pending["receipt"]["handoff_id"], {**acceptance, "request_id": "again", "owner_id": "other"}) == (accepted, False)
    assert service.submit(key, {"request_id": "submit-after-accept", "revision": 2}) == (accepted, False)
    assert len(handoff_environment[4].bootstrap()["cases"]) == 8
    with pytest.raises(ResearchServiceError):
        service.decide(pending["receipt"]["handoff_id"], {**acceptance, "owner_id": "changed"})


@pytest.mark.parametrize("change", [{"owner_id": None}, {"next_action": None}, {"due_date": None},
    {"due_date": (date.today()-timedelta(days=1)).isoformat()}, {"reviewer": " "},
    {"relationship_checked": False}, {"relationship_note": " "}, {"contact_index": 0},
    {"status": "Contacted"}, {"opportunity_revision": 1}, {"case_id": "injected"}])
def test_invalid_acceptance_leaves_pending_and_no_case(handoff_environment, acceptance, change):
    service = handoff_environment[3]
    pending = submit(handoff_environment)
    key = pending["receipt"]["handoff_id"]
    with pytest.raises((ValueError, ResearchServiceError)):
        service.decide(key, {**acceptance, **change})
    assert service.get(key)["receipt"]["status"] == "pending_acceptance"
    assert len(handoff_environment[4].bootstrap()["cases"]) == 7


@pytest.mark.parametrize("decision", ["needs_research", "rejected"])
def test_return_and_resubmit_new_review(handoff_environment, acceptance, decision):
    _, _, opportunities, service, qualification, _, key = handoff_environment
    pending = submit(handoff_environment)
    body = {"request_id": "return", "status": decision, "reviewer": "pipeline-reviewer", "opportunity_revision": 2}
    with pytest.raises(ValueError):
        service.decide(pending["receipt"]["handoff_id"], body)
    returned, _ = service.decide(pending["receipt"]["handoff_id"], {**body, "reason": "Need relationship context."})
    assert returned["receipt"]["case_id"] is None and len(qualification.bootstrap()["cases"]) == 7
    with pytest.raises(ResearchServiceError):
        service.submit(key, {"request_id": "repeat-without-review", "revision": 2})
    opportunities.review(key, {"request_id": "updated-review", "revision": 2, "consultant_id": "research-reviewer",
        "outcome": "worth_exploring", "reason_code": "relevant_signal", "scope": "opportunity", "source_support_checked": True})
    new, _ = service.submit(key, {"request_id": "updated-submission", "revision": 3})
    assert new["receipt"]["handoff_id"] != pending["receipt"]["handoff_id"]
    service.decide(new["receipt"]["handoff_id"], {**acceptance, "opportunity_revision": 3})
    assert len(service.list()) == 2 and len(qualification.bootstrap()["cases"]) == 8


def test_feedback_changes_block_stale_pending_acceptance_and_accepted_feedback_is_frozen(handoff_environment, acceptance):
    _, _, opportunities, service, _, _, key = handoff_environment
    pending = submit(handoff_environment)
    opportunities.review(key, {"request_id": "changed-review", "revision": 2, "consultant_id": "research-reviewer",
        "outcome": "defer", "reason_code": "timing", "scope": "opportunity"})
    with pytest.raises(ResearchServiceError):
        service.decide(pending["receipt"]["handoff_id"], acceptance)
    with pytest.raises(ResearchServiceError):
        service.submit(key, {"request_id": "stale-submit", "revision": 3})


def test_acceptance_transaction_rolls_back_case_receipt_and_idempotency_on_failure(handoff_environment, acceptance, monkeypatch):
    service = handoff_environment[3]
    pending = submit(handoff_environment)
    original = service._remember
    def fail(*args):
        raise RuntimeError("Injected interruption before transaction commit")
    monkeypatch.setattr(service, "_remember", fail)
    with pytest.raises(RuntimeError):
        service.decide(pending["receipt"]["handoff_id"], acceptance)
    assert service.get(pending["receipt"]["handoff_id"])["receipt"]["status"] == "pending_acceptance"
    assert len(handoff_environment[4].bootstrap()["cases"]) == 7
    assert handoff_environment[4].db.execute("SELECT count(*) FROM workspace_handoff_cases").fetchone()[0] == 0
    monkeypatch.setattr(service, "_remember", original)
    assert service.decide(pending["receipt"]["handoff_id"], acceptance)[1]


def test_concurrent_acceptance_and_restart_keep_one_case(handoff_environment, acceptance):
    root, research, opportunities, service, qualification, _, key = handoff_environment
    pending = submit(handoff_environment)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: service.decide(pending["receipt"]["handoff_id"], acceptance), range(2)))
    assert sorted(r[1] for r in results) == [False, True]
    restored_store = Store(root / "runtime/qualification.sqlite3")
    try:
        restored = HandoffService(OpportunityService(research), restored_store)
        assert restored.decide(pending["receipt"]["handoff_id"], acceptance)[1] is False
        assert len(restored_store.bootstrap()["cases"]) == 8
    finally:
        restored_store.db.close()
    with pytest.raises(ResearchServiceError):
        opportunities.review(key, {"request_id": "late-review", "revision": 2, "consultant_id": "reviewer",
            "outcome": "defer", "reason_code": "timing", "scope": "opportunity"})
    with pytest.raises(ResearchServiceError):
        service.guard_reset()


def test_contact_confirmation_draft_and_recorded_outreach_do_not_change_qualification(handoff_environment, acceptance):
    service, store = handoff_environment[3:5]
    pending = submit(handoff_environment)
    accepted, _ = service.decide(pending["receipt"]["handoff_id"], acceptance)
    key = accepted["receipt"]["case_id"]
    case = store.get(key)
    before = calculate(case)
    with pytest.raises(ValueError):
        store.generate(key, "outreach", {"revision": case["revision"]})
    with pytest.raises(ValueError):
        service.record_contacted(key, {"revision": 1, "contacted_on": date.today().isoformat(), "note": "Sent"})
    case = service.confirm_contact(key, {"revision": 1, "contact": "Test Person", "role": "Operations lead",
        "source_rationale": "Consultant verified this synthetic contact for testing."})
    assert case["stage"] == "Not contacted" and calculate(case) == before
    assert store.generate(key, "outreach", {"revision": case["revision"]})["draft"].startswith("Hi Test,")
    assert store.get(key)["stage"] == "Not contacted"
    with pytest.raises(Conflict):
        service.record_contacted(key, {"revision": 1, "contacted_on": date.today().isoformat(), "note": "Sent"})
    with pytest.raises(ValueError):
        service.record_contacted(key, {"revision": case["revision"], "contacted_on": (date.today()+timedelta(days=1)).isoformat(), "note": "Sent"})
    case = service.record_contacted(key, {"revision": case["revision"], "contacted_on": date.today().isoformat(),
        "note": "Human records sending an introduction outside this app."})
    assert case["stage"] == "Contacted" and case["lastReply"] is None and calculate(case) == before
    store.ingest(key, {"revision": case["revision"], "requestId": "reply", "text": "Thank you for reaching out.",
        "source": "Synthetic reply", "kind": "Client reply", "date": date.today().isoformat()})
    assert store.get(key)["stage"] == "Active" and store.get(key)["contactStatus"] == "reply_received"
    assert calculate(store.get(key)) == before


def test_latest_review_and_revision_required_before_submission(handoff_environment):
    _, _, opportunities, service, _, _, key = handoff_environment
    with pytest.raises(ResearchServiceError):
        service.submit(key, {"request_id": "stale", "revision": 1})
    opportunities.review(key, {"request_id": "defer", "revision": 2, "consultant_id": "reviewer",
        "outcome": "defer", "reason_code": "timing", "scope": "opportunity"})
    with pytest.raises(ResearchServiceError):
        service.submit(key, {"request_id": "not-worth", "revision": 3})
    assert service.list() == []


def test_same_company_different_opportunities_warn_without_merging(handoff_environment, acceptance):
    _, _, opportunities, service, store, lead, key = handoff_environment
    pending = submit(handoff_environment)
    first, _ = service.decide(pending["receipt"]["handoff_id"], acceptance)
    other, _ = opportunities.create({"request_id": "different-team", "run_id": lead.run_id, "lead_id": lead.lead_id,
        "title": "Separate team planning", "team": "Another team", "problem_hypothesis": lead.research.problem_hypothesis,
        "cgs_relevance_hypothesis": lead.research.cgs_relevance,
        "alternative_explanation": lead.research.alternative_explanation, "evidence_ids": ["problem"]})
    other_key = other["opportunity"]["opportunity_id"]
    opportunities.review(other_key, {"request_id": "review-team", "revision": 1, "consultant_id": "reviewer",
        "outcome": "worth_exploring", "reason_code": "relevant_signal", "scope": "opportunity", "source_support_checked": True})
    pending, _ = service.submit(other_key, {"request_id": "submit-team", "revision": 2})
    assert pending["duplicate_candidates"][0]["case_id"] == first["receipt"]["case_id"]
    second, _ = service.decide(pending["receipt"]["handoff_id"], {**acceptance, "request_id": "accept-team",
        "relationship_note": "Same company, explicitly reviewed as another team's distinct opportunity."})
    assert second["receipt"]["case_id"] != first["receipt"]["case_id"]
    assert len(store.bootstrap()["cases"]) == 9


def test_saved_contact_can_be_selected_without_assuming_qualification(handoff_environment, acceptance):
    _, _, _, service, store, lead, _ = handoff_environment
    # Test-only public person record in the frozen submission snapshot.
    pending = submit(handoff_environment)
    import json
    snapshot = pending["snapshot"]
    person = {"name": "Synthetic Person", "role": "Operations lead", "company": lead.research.company,
        "evidence_ids": ["problem"], "role_as_of": None, "role_date_excerpt": None,
        "verified_at": date.today().isoformat()}
    snapshot["opportunity"]["research_snapshot"]["research"]["people"] = [person]
    with store.db:
        store.db.execute("UPDATE workspace_handoffs SET snapshot=? WHERE id=?", (json.dumps(snapshot), pending["receipt"]["handoff_id"]))
    accepted, _ = service.decide(pending["receipt"]["handoff_id"], {**acceptance, "contact_index": 0})
    case = store.get(accepted["receipt"]["case_id"])
    assert case["contact"] == person["name"] and case["contactProvenance"]["kind"] == "public_research"
    assert case["stage"] == "Not contacted" and case["scores"]["A"] is None


def test_rejected_or_changed_request_cannot_overwrite_an_accepted_receipt(handoff_environment, acceptance):
    service = handoff_environment[3]
    pending = submit(handoff_environment)
    accepted, _ = service.decide(pending["receipt"]["handoff_id"], acceptance)
    with pytest.raises(ResearchServiceError):
        service.decide(pending["receipt"]["handoff_id"], {**acceptance, "request_id": "reject-late", "status": "rejected", "reason": "Late change"})
    assert service.get(pending["receipt"]["handoff_id"]) == accepted
