from pathlib import Path
import shutil

import pytest

from backend.research_service import ResearchJobStore, ResearchService
from p1_helpers import PreparedProvider

ROOT = Path(__file__).resolve().parents[2]




@pytest.fixture
def environment(tmp_path):
    root = tmp_path / "workspace"
    (root / "config").mkdir(parents=True)
    for name in ("problem_brief.json", "rubric.json"):
        shutil.copyfile(ROOT / "config" / name, root / "config" / name)
    return root, ResearchJobStore(root / "runtime/research.sqlite3")


@pytest.fixture
def build_service(environment):
    root, store = environment
    services, providers = [], []

    def build(factory=None):
        def default_factory(model):
            provider = PreparedProvider(model)
            providers.append(provider)
            return provider
        service = ResearchService(root, store, "gemini-test", factory or default_factory)
        services.append(service)
        return service

    yield build, providers
    for provider in providers:
        provider.release.set()
    for service in services:
        service.shutdown()


@pytest.fixture
def request_body():
    return {"request_id": "intent-1", "consultant_id": "consultant-1",
            "company": {"company": "Synthetic Factory 1", "domain": "synthetic-factory-1.example"}}
