import json
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from backend.contracts import (ResearchTask, OpportunityBrief, ResearchFeedback,
                               ResearchDirectionPatch, ThemeChangeProposal, HandoffReceipt)
from backend.workspace_config import WorkspaceConfig
from backend.workspace_runtime import WorkspaceStores
from lead_agent.assessment import make_lead
from lead_agent.demo import fixture
from lead_agent.models import Brief, Rubric, Limits

ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


@pytest.fixture
def snapshots():
    brief = Brief.model_validate_json((ROOT / "config/problem_brief.json").read_text(encoding="utf-8"))
    rubric = Rubric.model_validate_json((ROOT / "config/rubric.json").read_text(encoding="utf-8"))
    research, sources = fixture()
    lead = make_lead("research-run", research, sources, brief, rubric,
                     date(2026, 10, 20), mode="synthetic")
    return brief, rubric, lead


def test_stores_have_separate_schemas_and_reopen_without_losing_research(tmp_path):
    config = WorkspaceConfig.load(tmp_path, {})
    stores = WorkspaceStores.open(config)
    run = stores.research.create_run({"test": "saved"})
    sample_count = len(stores.qualification.bootstrap()["cases"])
    with sqlite3.connect(config.research_db) as db:
        assert "payload" in [r[1] for r in db.execute("PRAGMA table_info(runs)")]
    with sqlite3.connect(config.qualification_db) as db:
        assert "case_id" in [r[1] for r in db.execute("PRAGMA table_info(runs)")]
    stores.close()
    reopened = WorkspaceStores.open(config)
    try:
        assert reopened.research.run(run)["payload"]["test"] == "saved"
        assert len(reopened.qualification.bootstrap()["cases"]) == sample_count
    finally:
        reopened.close()


def test_same_database_is_rejected_before_creating_files(tmp_path):
    target = tmp_path / "shared.sqlite3"
    with pytest.raises(ValueError, match="different database"):
        WorkspaceConfig.load(tmp_path, {"CGS_DB_PATH": str(target),
                                       "CGS_RESEARCH_DB_PATH": str(target)})
    assert not target.exists()


def test_provider_readiness_is_independent_and_never_reports_credentials(tmp_path):
    config = WorkspaceConfig.load(tmp_path, {
        "GEMINI_API_KEY": "test-gemini-secret", "TAVILY_API_KEY": "test-tavily-secret",
        "GEMINI_MODEL": "gemini-test", "OPENAI_API_KEY": "test-openai-secret"})
    report = json.dumps(config.providers)
    assert config.providers["research"]["configured"]
    assert not config.providers["qualification"]["configured"]
    assert "secret" not in report
    assert config.providers["orchestrator"]["implemented"]  # P4 adds the bounded manager.


@pytest.mark.parametrize("port", ["abc", "0", "65536"])
def test_invalid_port_is_rejected(tmp_path, port):
    with pytest.raises(ValueError, match="CGS_PORT"):
        WorkspaceConfig.load(tmp_path, {"CGS_PORT": port})


def test_task_keeps_approved_config_snapshot_and_rejects_duplicate_domains(snapshots):
    brief, rubric, _ = snapshots
    data = dict(run_id="run-1", request_id="intent-1", consultant_id="reviewer-1",
                knowledge_version="cgs-background-v1", brief=brief, rubric=rubric,
                companies=[{"company": "Example", "domain": "example.com"}],
                limits=Limits(), created_at=AT)
    task = ResearchTask(**data)
    assert task.brief.version == brief.version
    assert task.brief.approval_basis == "mock"
    with pytest.raises(ValidationError, match="once per task"):
        ResearchTask(**{**data, "companies": data["companies"] * 2})
    with pytest.raises(ValidationError):
        ResearchTask(**{**data, "api_key": "not-a-task-field"})


def test_two_opportunities_share_company_without_sharing_opportunity_id(snapshots):
    _, _, lead = snapshots
    data = dict(title="Investigate a business change", problem_hypothesis="Possible planning gap.",
                cgs_relevance_hypothesis="Possible fit; consultant must validate.",
                alternative_explanation="Internal teams may already cover the work.",
                evidence_ids=[lead.research.evidence[0].evidence_id], research_snapshot=lead)
    first = OpportunityBrief(opportunity_id="op-1", team="Finance", **data)
    second = OpportunityBrief(opportunity_id="op-2", team=None, **data)
    assert first.research_snapshot.lead_id == second.research_snapshot.lead_id
    assert first.opportunity_id != second.opportunity_id
    assert second.team is None and second.research_snapshot.research.people is None
    assert first.kind == "public_research"
    with pytest.raises(ValidationError):
        OpportunityBrief(opportunity_id="op-3", **{**data, "evidence_ids": ["invented"]})
    with pytest.raises(ValidationError):
        OpportunityBrief(opportunity_id="op-4", **data, qualification_scores={"B": 5})


def test_feedback_preserves_reason_scope_and_unknown_review_time():
    data = dict(feedback_id="fb-1", opportunity_id="op-1", consultant_id="consultant-1",
                theme_id="theme-1", theme_version="v1", outcome="defer",
                reason_code="already_owned", scope="opportunity", recorded_at=AT)
    feedback = ResearchFeedback(**data)
    assert feedback.scope == "opportunity"
    assert feedback.reason_code == "already_owned"
    assert feedback.review_minutes is None
    with pytest.raises(ValidationError, match="Explain"):
        ResearchFeedback(**{**data, "reason_code": "other"})


def test_direction_patch_cannot_change_qualification_weights_or_execute_code():
    assert ResearchDirectionPatch(excluded_domains=["https://www.example.com"]).excluded_domains == ["example.com"]
    with pytest.raises(ValidationError):
        ResearchDirectionPatch()
    with pytest.raises(ValidationError):
        ResearchDirectionPatch(weights={"B": 100})
    with pytest.raises(ValidationError):
        ResearchDirectionPatch(python="print('change files')")


def test_proposal_cannot_claim_approval_without_human_and_new_version():
    data = dict(proposal_id="proposal-1", theme_id="theme-1", base_theme_version="v1",
                consultant_id="consultant-1", scope="consultant", feedback_ids=["fb-1"],
                patch={"signals": ["Merger integration evidence"]},
                rationale="Reviewer requested specific business events.")
    assert ThemeChangeProposal(**data).status == "pending_review"
    with pytest.raises(ValidationError):
        ThemeChangeProposal(**data, status="approved")
    with pytest.raises(ValidationError):
        ThemeChangeProposal(**data, status="approved", reviewer="human", reviewed_at=AT,
                            resulting_theme_version="v1")
    approved = ThemeChangeProposal(**data, status="approved", reviewer="human", reviewed_at=AT,
                                   resulting_theme_version="v2")
    assert approved.resulting_theme_version == "v2"


def test_handoff_requires_explicit_acceptance_and_does_not_claim_contact():
    data = dict(handoff_id="handoff-1", opportunity_id="op-1", opportunity_revision=1)
    assert HandoffReceipt(**data).case_id is None
    with pytest.raises(ValidationError):
        HandoffReceipt(**data, status="accepted", case_id="case-1")
    receipt = HandoffReceipt(**data, status="accepted", case_id="case-1",
                             reviewer="human", decided_at=AT, owner_id="owner-1",
                             next_action="Identify a relevant contact", due_date=date(2026, 10, 29))
    assert receipt.initial_contact_status == "not_contacted"
    with pytest.raises(ValidationError):
        HandoffReceipt(**data, initial_contact_status="Contacted")
