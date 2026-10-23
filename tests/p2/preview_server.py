"""Isolated browser-test server. Never loads .env or calls external providers."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/p1"))
sys.path.insert(0, str(ROOT / "tests/p4"))

from backend.research_service import ResearchService
from backend.workspace_config import WorkspaceConfig
from backend.workspace_runtime import WorkspaceStores, make_workspace_server
from p2_helpers import sample
from p1_helpers import PreparedProvider
from p4_helpers import ControlledAnalysis


class SlowSyntheticProvider(PreparedProvider):
    def search(self, *args, **kwargs):
        time.sleep(1)
        return super().search(*args, **kwargs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-test-data", action="store_true", required=True)
    parser.add_argument("--db-root", type=Path, required=True)
    parser.add_argument("--unconfigured", action="store_true")
    args = parser.parse_args()
    root = args.db_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "config").mkdir()
    for name in ("problem_brief.json", "rubric.json"):
        shutil.copyfile(ROOT / "config" / name, root / "config" / name)
    # Fake presence metadata for the test UI only. No secret is read or sent.
    env = {} if args.unconfigured else {"GEMINI_API_KEY": "synthetic-test-only",
        "TAVILY_API_KEY": "synthetic-test-only", "GEMINI_MODEL": "gemini-test"}
    config = WorkspaceConfig.load(root, env)
    stores = WorkspaceStores.open(config)
    research = ResearchService(root, stores.research, "gemini-test")
    sample(research)
    research.shutdown()
    server = make_workspace_server(replace(config, port=0), stores,
        research_provider_factory=lambda model: SlowSyntheticProvider(model),
        manager_provider_factory=lambda model: ControlledAnalysis())
    print(json.dumps({"url": "http://127.0.0.1:" + str(server.server_port),
                      "synthetic": True}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.orchestrator_service.shutdown()
        server.research_service.shutdown()
        server.server_close()
        stores.close()
