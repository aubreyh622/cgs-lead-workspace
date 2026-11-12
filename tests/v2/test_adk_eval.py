"""The agents-cli eval harness (api/adk_eval.py, tests/eval): the quality-check cases run on either agent runtime and are
saved as agents-cli eval cases, and the local metrics score them. A fake AI plays the agents, so this runs offline."""
import importlib.util
import json
from pathlib import Path

import pytest

import api.adk_eval as E
from test_quality_check import setup

METRICS = Path(__file__).resolve().parents[1] / "eval" / "metrics"


def metric(name):
    spec = importlib.util.spec_from_file_location(name, METRICS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.evaluate


@pytest.mark.parametrize("runtime", ["legacy", "adk"])
def test_traces_for_both_runtimes(monkeypatch, tmp_path, runtime):
    if runtime == "adk":
        pytest.importorskip("google.adk")
    prepare, judge, _ = setup(monkeypatch, tmp_path)
    out = tmp_path / runtime
    assert E.generate(["--runtime", runtime, "--parts", "evidence,outreach,discovery", "--reps", "1", "--limit", "4",
                       "--out", str(out)], prepare=prepare, judge=judge) == 0
    dev = json.loads((out / "evidence" / "dev" / "rep1.json").read_text(encoding="utf-8"))["eval_cases"]
    held = json.loads((out / "evidence" / "holdout" / "rep1.json").read_text(encoding="utf-8"))["eval_cases"]
    assert len(dev) == 3 and len(held) == 1 and all(c["runtime"] == runtime for c in dev + held)
    case = dev[0]
    assert case["eval_case_id"].startswith("evidence:") and case["runs"][0]["agent"] == "Evidence agent"
    assert case["runs"][0]["finished"] and case["agent_data"]["turns"][0]["events"][0]["author"] == "user"
    calls = [p["function_call"]["name"] for e in case["agent_data"]["turns"][0]["events"] for p in e["content"]["parts"] if "function_call" in p]
    assert "notes" in calls and "finish" in calls
    assert json.loads(case["responses"][0]["response"]["parts"][0]["text"])  # the accepted answer
    draft = json.loads((out / "outreach" / "dev" / "rep1.json").read_text(encoding="utf-8"))["eval_cases"][0]
    names = [p["function_response"]["name"] for e in draft["agent_data"]["turns"][0]["events"] for p in e["content"]["parts"] if "function_response" in p]
    assert "submit_draft" in names  # the finish tool keeps its own name
    assert (out / "discovery" / "dev" / "rep1.json").exists() and json.loads((out / "run.json").read_text())["runtime"] == runtime


def test_metrics_score_what_the_quality_check_measured():
    ok = {"runs": [{"agent": "Evidence agent", "status": "completed", "finished": True, "steps": 3, "answers_sent_back": 1, "tokens_in": 4000}],
          "observed": {"expected": 4, "hit": ["B=3", "A=3", "N=4"], "missed": ["T"], "over": [], "wrong": []}}
    assert metric("task_completed")(ok)["score"] == 1.0
    assert metric("evidence_recall")(ok)["score"] == 0.75
    assert metric("no_overclaim")(ok)["score"] == 1.0
    assert metric("no_overclaim")({"observed": {"over": ["N=5 (max 4)"]}})["score"] == 0.0
    assert metric("model_turns")(ok)["score"] == 3 and metric("answers_sent_back")(ok)["score"] == 1 and metric("tokens_in_k")(ok)["score"] == 4
    assert metric("task_completed")({"runs": [{"agent": "x", "status": "failed", "finished": False, "error": "quota"}]})["score"] == 0.0
    assert metric("research_checks")({"observed": {"brief": True, "signal": True, "dated": False, "contact": True,
                                                   "no_invented_name": True, "recommendation": True}})["score"] == 5 / 6
    assert metric("no_invented_name")({"observed": {"brief": True, "no_invented_name": False}})["score"] == 0.0
    assert metric("outreach_rubric")({"observed": {"mean": 3.6}, "rubric": ["hook"]})["score"] == 3.6
    assert metric("outreach_rubric")({"observed": {"error": "no draft"}})["score"] == 0.0
    assert metric("discovery_fit")({"observed": {"nominations": 4, "fits": 3}})["score"] == 0.75
    assert metric("discovery_no_misfit")({"observed": {"rows": [{"company": "Harborline Bank", "kind": "not a fit"}]}})["score"] == 0.0


def test_every_config_names_existing_metrics():
    for part in E.PARTS:
        text = (METRICS.parent / f"{part}.yaml").read_text(encoding="utf-8")
        files = [l.split("metrics/")[1].strip() for l in text.splitlines() if "custom_function_file" in l]
        assert files and all((METRICS / f).exists() for f in files)
        assert all(m in text for m in E.BAR[part])  # every metric the parity bar gates is graded
