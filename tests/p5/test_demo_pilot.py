"""Exercise the user-launchable rehearsal with real local HTTP and no external providers."""
import json
import urllib.request

import httpx
import pytest

from backend.demo_providers import DIRECTION_SIGNAL, SyntheticFeedbackProvider
from backend.demo_workflow import MARKER, prepare_demo, running_demo
from backend.workspace_config import WorkspaceConfig
from backend.workspace_runtime import make_workspace_server


@pytest.mark.loopback
def test_two_round_pilot_isolated_synthetic_and_reusable(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("An offline demo attempted an external provider call.")

    monkeypatch.setattr(httpx, "post", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    for key in ("GEMINI_API_KEY", "TAVILY_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(key, "synthetic-inherited-secret")
    monkeypatch.setenv("OPENAI_MODEL", "inherited-live-model")
    root = tmp_path / "demo"
    report = prepare_demo(root)
    assert report["passed"] and len(report["checks"]) == 27
    assert all(check["passed"] for check in report["checks"])
    assert report["external_api_calls"] == report["real_messages_sent"] == 0
    assert not report["business_value_verified"] and not report["cgs_client_acceptance"]
    assert report["rounds"][0]["theme_version"] != report["rounds"][1]["theme_version"]
    tasks = [t for r in report["rounds"] for t in r["tasks"]]
    assert len({t["run_id"] for t in tasks}) == 4
    assert all(t["card"]["research_method"] == "synthetic_fixture" for t in tasks)
    assert all(t["card"]["mode"] == "synthetic" and t["card"]["approval_basis"] == "mock" for t in tasks)
    assert "synthetic-inherited-secret" not in json.dumps(report)
    assert not (root / ".env").exists()
    assert not (tmp_path / "runtime").exists()
    before = (root / "pilot-evidence.json").read_bytes()
    assert prepare_demo(root) == report
    assert (root / "pilot-evidence.json").read_bytes() == before
    with running_demo(root, []) as client:
        assert len(client.request("GET", "/api/research/runs")) == 4
        bootstrap = client.request("GET", "/api/bootstrap")
        assert len(bootstrap["cases"]) == 9
        assert not bootstrap["config"]["liveConfigured"] and bootstrap["config"]["model"] == ""
        theme = client.request("GET", "/api/research/theme")
        assert theme["research"]["simulated"]
        assert len(theme["demo_companies"]) == 2
        dashboard = client.request("GET", "/api/research/orchestrator?consultant_id=Demo%20consultant&scope=theme")
        review = client.request("POST", "/api/research/orchestrator/reviews", {
            "request_id": "no-change-rehearsal", "consultant_id": "Demo consultant", "scope": "theme",
            "base_theme_version": theme["brief"]["version"],
            "feedback_ids": [f["feedback_id"] for f in dashboard["eligible_feedback"]]})
        result = client.wait("/api/research/orchestrator/reviews/" + review["id"])
        assert result["result"]["proposal_id"] is None
        assert client.request("GET", "/api/research/theme")["brief"]["version"] == theme["brief"]["version"]


def test_demo_refuses_unrelated_nonempty_directory(tmp_path):
    existing = tmp_path / "important.txt"
    existing.write_text("Preserve this file.")
    with pytest.raises(ValueError, match="empty demo directory"):
        prepare_demo(tmp_path)
    assert existing.read_text() == "Preserve this file."
    assert list(tmp_path.iterdir()) == [existing]


def test_demo_refuses_incomplete_preparation_without_reset(tmp_path):
    marker = tmp_path / MARKER
    marker.write_text(json.dumps({"kind": "cgs-offline-demo", "status": "preparing"}))
    before = marker.read_bytes()
    with pytest.raises(ValueError, match="incomplete"):
        prepare_demo(tmp_path)
    assert marker.read_bytes() == before


def test_offline_factory_guard_runs_before_socket_or_store_access(tmp_path):
    with pytest.raises(ValueError, match="synthetic provider factories"):
        make_workspace_server(WorkspaceConfig.load(tmp_path, {}), None, demo_companies={})


def test_simulated_feedback_does_not_repeat_an_existing_change():
    feedback = [{"feedback_id": "fixture-feedback"}]
    result, usage = SyntheticFeedbackProvider().analyze({"brief": {"signals": [DIRECTION_SIGNAL]}, "feedback": feedback})
    assert json.loads(result.patch_json) == {}
    assert usage == {"synthetic_model_calls": 1}
