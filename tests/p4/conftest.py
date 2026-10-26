from pathlib import Path
import shutil
import sys

import pytest

for folder in ("p1", "p2"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / folder))
from p1_helpers import PreparedProvider, wait_terminal
from p2_helpers import sample
from backend.handoff_service import HandoffService
from backend.opportunity_service import OpportunityService
from backend.orchestrator_service import OrchestratorService
from p4_helpers import ControlledAnalysis, feedback
from backend.research_service import ResearchJobStore, ResearchService
from backend.theme_store import ThemeStore
from server import Store

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def manager_environment(tmp_path):
    root = tmp_path / "workspace"
    (root / "config").mkdir(parents=True)
    for name in ("problem_brief.json", "rubric.json"):
        shutil.copyfile(ROOT / "config" / name, root / "config" / name)
    research = ResearchService(root, ResearchJobStore(root / "runtime/research.sqlite3"), "gemini-test",
                               lambda model: PreparedProvider(model))
    opportunities = OpportunityService(research)
    qualification = Store(root / "runtime/qualification.sqlite3")
    handoffs = HandoffService(opportunities, qualification)
    themes = ThemeStore(root, research.store)
    research.theme_store = themes
    provider = ControlledAnalysis()
    manager = OrchestratorService(research, opportunities, handoffs, themes, "gemini-test", lambda model: provider)
    lead = sample(research)
    yield root, research, opportunities, qualification, themes, provider, manager, lead
    provider.release.set()
    manager.shutdown()
    research.shutdown()
    qualification.db.close()


@pytest.fixture
def proposal_body(manager_environment):
    f = feedback(manager_environment)
    return {"request_id": "proposal-1", "consultant_id": "consultant-a", "scope": "theme",
        "feedback_ids": [f["feedback_id"]], "base_theme_version": f["theme_version"],
        "patch": {"signals": ["Specific integration planning signals"]}, "rationale": "Follow the reviewer's request for focused business events."}


