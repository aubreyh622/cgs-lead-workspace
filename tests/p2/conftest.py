from pathlib import Path
import shutil

import pytest

from backend.opportunity_service import OpportunityService
from backend.research_service import ResearchJobStore, ResearchService
from p2_helpers import sample

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def environment(tmp_path):
    root = tmp_path / "workspace"
    (root / "config").mkdir(parents=True)
    for name in ("problem_brief.json", "rubric.json"):
        shutil.copyfile(ROOT / "config" / name, root / "config" / name)
    store = ResearchJobStore(root / "runtime/research.sqlite3")
    research = ResearchService(root, store, "gemini-test")
    service = OpportunityService(research)
    lead = sample(research)
    yield root, research, service, lead
    research.shutdown()


@pytest.fixture
def opportunity_body(environment):
    _, _, _, lead = environment
    return {"request_id": "opportunity-1", "run_id": lead.run_id, "lead_id": lead.lead_id,
        "title": "Planning opportunity", "team": None,
        "problem_hypothesis": lead.research.problem_hypothesis,
        "cgs_relevance_hypothesis": lead.research.cgs_relevance,
        "alternative_explanation": lead.research.alternative_explanation,
        "evidence_ids": ["problem"]}


@pytest.fixture
def review_body():
    return {"request_id": "feedback-1", "revision": 1, "consultant_id": "reviewer-local",
        "outcome": "research_further", "reason_code": "evidence_gap", "scope": "opportunity",
        "comment": "Need to verify who owns the issue.", "review_minutes": None,
        "source_support_checked": False}
