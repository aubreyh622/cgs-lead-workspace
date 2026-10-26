from datetime import date, timedelta
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "p2"))
from p2_helpers import sample
from backend.handoff_service import HandoffService
from backend.opportunity_service import OpportunityService
from backend.research_service import ResearchJobStore, ResearchService
from server import Store

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def handoff_environment(tmp_path):
    root = tmp_path / "workspace"
    (root / "config").mkdir(parents=True)
    for name in ("problem_brief.json", "rubric.json"):
        shutil.copyfile(ROOT / "config" / name, root / "config" / name)
    research = ResearchService(root, ResearchJobStore(root / "runtime/research.sqlite3"), "gemini-test")
    opportunities = OpportunityService(research)
    qualification = Store(root / "runtime/qualification.sqlite3")
    handoffs = HandoffService(opportunities, qualification)
    lead = sample(research)
    created, _ = opportunities.create({"request_id": "create-1", "run_id": lead.run_id, "lead_id": lead.lead_id,
        "title": "Planning opportunity", "team": None, "problem_hypothesis": lead.research.problem_hypothesis,
        "cgs_relevance_hypothesis": lead.research.cgs_relevance,
        "alternative_explanation": lead.research.alternative_explanation, "evidence_ids": ["problem"]})
    key = created["opportunity"]["opportunity_id"]
    opportunities.review(key, {"request_id": "review-1", "revision": 1, "consultant_id": "research-reviewer",
        "outcome": "worth_exploring", "reason_code": "relevant_signal", "scope": "opportunity",
        "source_support_checked": True})
    yield root, research, opportunities, handoffs, qualification, lead, key
    research.shutdown()
    qualification.db.close()


@pytest.fixture
def acceptance():
    return {"request_id": "accept-1", "opportunity_revision": 2, "status": "accepted",
        "reviewer": "pipeline-reviewer", "owner_id": "local-consultant", "next_action": "Find and verify an appropriate contact",
        "due_date": (date.today()+timedelta(days=3)).isoformat(), "relationship_checked": True,
        "relationship_note": "Checked existing local cases; no matching opportunity or known relationship recorded."}
