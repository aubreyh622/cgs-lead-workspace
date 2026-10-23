from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from pydantic import ValidationError

from backend.opportunity_service import OpportunityService
from backend.research_service import ResearchNotFound, ResearchServiceError


def test_multiple_opportunities_and_idempotency(environment, opportunity_body):
    _, _, service, lead = environment
    one, created = service.create(opportunity_body)
    assert created and one["revision"] == 1 and one["latest_feedback"] is None
    assert one["opportunity"]["research_snapshot"] == lead.model_dump(mode="json")
    assert service.create(opportunity_body) == (one, False)
    two, created = service.create({**opportunity_body, "request_id": "different-problem", "team": "Operations"})
    assert created and two["opportunity"]["opportunity_id"] != one["opportunity"]["opportunity_id"]
    assert len(service.list()) == 2
    with pytest.raises(ResearchServiceError):
        service.create({**opportunity_body, "title": "Changed input"})


@pytest.mark.parametrize("change", [
    {"evidence_ids": ["invented"]}, {"evidence_ids": ["problem", "problem"]}, {"evidence_ids": []},
    {"research_snapshot": {}}, {"title": " "}, {"opportunity_id": "client-assigned"}])
def test_invalid_or_injected_opportunity_input(environment, opportunity_body, change):
    service = environment[2]
    with pytest.raises(ValidationError):
        service.create({**opportunity_body, **change})
    assert service.list() == []


def test_missing_and_running_research_not_saved(environment, opportunity_body):
    _, research, service, lead = environment
    with pytest.raises(ResearchNotFound):
        service.create({**opportunity_body, "run_id": "foreign-run"})
    with pytest.raises(ResearchNotFound):
        service.create({**opportunity_body, "lead_id": "foreign-card"})
    with research.store.db() as db:
        db.execute("UPDATE runs SET status='running' WHERE id=?", (lead.run_id,))
    with pytest.raises(ResearchServiceError):
        service.create(opportunity_body)
    assert service.list() == []


def test_saved_snapshot_survives_research_changes_and_restart(environment, opportunity_body):
    _, research, service, lead = environment
    one, _ = service.create(opportunity_body)
    lead.research.problem_hypothesis = "The source research was subsequently replaced with another hypothesis."
    research.store.save_research(lead)
    restored = OpportunityService(research).get(one["opportunity"]["opportunity_id"])
    assert restored == one
    assert restored["opportunity"]["research_snapshot"]["research"]["problem_hypothesis"] != lead.research.problem_hypothesis


def test_feedback_history_replay_server_context_and_no_theme_changes(environment, opportunity_body, review_body):
    root, research, service, _ = environment
    original = (root / "config/problem_brief.json").read_bytes()
    one, _ = service.create(opportunity_body)
    key = one["opportunity"]["opportunity_id"]
    two, created = service.review(key, review_body)
    assert created and two["revision"] == 2
    feedback = two["latest_feedback"]["feedback"]
    assert feedback["review_minutes"] is None and feedback["theme_version"] == research.theme()[0].version
    assert feedback["recorded_at"] and feedback["consultant_id"] == "reviewer-local"
    assert service.review(key, review_body) == (two, False)
    three, _ = service.review(key, {**review_body, "request_id": "next-review", "revision": 2,
        "outcome": "worth_exploring", "reason_code": "relevant_signal", "source_support_checked": True})
    assert three["revision"] == 3 and len(three["feedback_history"]) == 2
    assert three["feedback_history"][0]["feedback"] == feedback
    assert three["qualification_handoff"] == "not_implemented"
    assert (root / "config/problem_brief.json").read_bytes() == original
    assert OpportunityService(research).get(key) == three
    with pytest.raises(ResearchServiceError):
        service.review(key, {**review_body, "comment": "Changed same request"})


@pytest.mark.parametrize("scope", ["opportunity", "theme", "consultant", "cgs"])
def test_feedback_scope_is_preserved(environment, opportunity_body, review_body, scope):
    service = environment[2]
    one, _ = service.create(opportunity_body)
    two, _ = service.review(one["opportunity"]["opportunity_id"], {**review_body, "scope": scope})
    assert two["latest_feedback"]["feedback"]["scope"] == scope


@pytest.mark.parametrize("change", [
    {"outcome": "worth_exploring"}, {"reason_code": "other", "comment": " "},
    {"review_minutes": -1}, {"review_minutes": float('nan')}, {"consultant_id": " "},
    {"theme_version": "injected"}, {"recorded_at": "2020-01-01T00:00:00Z"},
    {"revision": True}, {"scope": "all"}])
def test_invalid_review_is_atomic(environment, opportunity_body, review_body, change):
    service = environment[2]
    one, _ = service.create(opportunity_body)
    with pytest.raises(ValueError):
        service.review(one["opportunity"]["opportunity_id"], {**review_body, **change})
    assert service.get(one["opportunity"]["opportunity_id"]) == one


def test_stale_and_concurrent_feedback(environment, opportunity_body, review_body):
    service = environment[2]
    one, _ = service.create(opportunity_body)
    key = one["opportunity"]["opportunity_id"]
    def submit(number):
        try:
            return service.review(key, {**review_body, "request_id": f"review-{number}"})[1]
        except ResearchServiceError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, range(2)))
    assert sorted(results) == [False, True]
    assert service.get(key)["revision"] == 2
    with pytest.raises(ResearchServiceError):
        service.review(key, {**review_body, "request_id": "stale-review"})


def test_concurrent_creation_replays_one_snapshot(environment, opportunity_body):
    service = environment[2]
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: service.create(opportunity_body), range(2)))
    assert sorted(x[1] for x in replies) == [False, True]
    assert len(service.list()) == 1


def test_feedback_request_cannot_be_replayed_against_another_opportunity(environment, opportunity_body, review_body):
    service = environment[2]
    one, _ = service.create(opportunity_body)
    two, _ = service.create({**opportunity_body, "request_id": "another"})
    service.review(one["opportunity"]["opportunity_id"], review_body)
    with pytest.raises(ResearchServiceError):
        service.review(two["opportunity"]["opportunity_id"], review_body)
    with pytest.raises(ResearchNotFound):
        service.get("unknown")
