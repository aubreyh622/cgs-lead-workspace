"""Traces for agents-cli: run the quality-check cases on one agent runtime and save every case as an agents-cli eval case.

    python -m api.adk_eval generate --runtime adk    [--parts evidence,research,outreach,discovery] [--reps 3] [--limit N]
    python -m api.adk_eval generate --runtime legacy  (the same cases on the hand-written loop: the baseline)
    agents-cli eval grade --traces runtime/eval/<run>/evidence/dev --config tests/eval/evidence.yaml --output ...
    python -m api.adk_eval gate runtime/eval/<legacy run> runtime/eval/<adk run>   (grades both, compares, applies the bar)

Or python -m api.adk_eval pair [--reps N]: both runtimes, then the gate. "ADK Eval.cmd" runs that. docs/ADK_EVAL.md explains the method and the bar.

The cases are the quality check's (api/check_quality.py, api/seed/quality_cases.json + ai_exam.json): everything is
synthetic, the web is the synthetic internet, email and CRM are off. Each case runs end to end through the workspace (the
agent, its code checks, the database), and is saved as one eval case in agents-cli's dataset format:
  agent_data   every agent run of the case as events: model turns with their tool calls, tool and finish results
  responses    the agent's final answer (the finish tool's accepted result), as JSON text
  observed     what the quality check measured for the case (found, over-claims, invented names, rubric scores ...)
  runs         per agent run: status, model turns, tool calls, answers sent back, tokens
The local metrics in tests/eval/metrics read `observed`, `runs` and `agent_data`; they need no Google Cloud project.

About a quarter of the cases are held out (split "holdout") and saved apart, so a final check can use cases nobody
looked at while changing the agents.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from api.settings import ROOT

OUT = ROOT / "runtime" / "eval"
EVAL_DIR = ROOT / "tests" / "eval"
PARTS = ("evidence", "research", "outreach", "discovery")
HOLDOUT_EVERY = 4  # every fourth case of a part (the 4th, 8th, ...) is held out


def split_of(i: int) -> str:
    return "holdout" if i % HOLDOUT_EVERY == HOLDOUT_EVERY - 1 else "dev"


def _agent_id(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def events(run) -> list[dict]:
    """One agent run's trace (api/agent_runtime.py format, the same on both runtimes) as agents-cli events."""
    me, out, asked = _agent_id(run.agent), [], []
    for t in run.trace or []:
        kind = t.get("type")
        if kind == "model":
            asked = t.get("calls") or []
            parts = ([{"text": t["text"]}] if t.get("text") else []) + [
                {"function_call": {"name": c["tool"], "args": c.get("args") if isinstance(c.get("args"), dict) else {}}}
                for c in t.get("calls") or []]
            if t.get("cut_off"):
                parts = [{"text": "[answer cut off before it finished; not used]"}]
            out.append({"author": me, "content": {"role": "model", "parts": parts or [{"text": ""}]}})
        elif kind == "tool":
            out.append({"author": me, "content": {"role": "user", "parts": [
                {"function_response": {"name": t.get("tool", ""), "response": {"result": str(t.get("result", ""))}}}]}})
        elif kind == "finish":
            res = {"accepted": bool(t.get("accepted"))} | ({"error": t["error"]} if t.get("error") else {})
            # the finish tool's own name (finish, submit_draft): the call in the last model turn with these arguments
            name = next((c["tool"] for c in asked if c.get("args") == t.get("args")), "finish")
            out.append({"author": me, "content": {"role": "user", "parts": [{"function_response": {"name": name, "response": res}}]}})
        elif kind == "stop":
            out.append({"author": me, "content": {"role": "model", "parts": [{"text": f"[stopped: {t.get('reason', '')}]"}]}})
    return out


def run_summary(run) -> dict:
    trace = run.trace or []
    finished = any(t.get("type") == "finish" and t.get("accepted") for t in trace)
    return {"agent": run.agent, "prompt_version": run.prompt_version, "model": run.model, "status": run.status,
            "finished": finished or (run.status == "completed" and not trace),
            "steps": sum(t.get("type") == "model" for t in trace), "tool_calls": sum(t.get("type") == "tool" for t in trace),
            "answers_sent_back": sum(t.get("type") == "finish" and not t.get("accepted") for t in trace),
            "cut_offs": sum(bool(t.get("cut_off")) for t in trace), "tokens_in": run.tokens_in or 0, "tokens_out": run.tokens_out or 0,
            "error": (run.error or "")[:300]}


def eval_case(case_id: str, part: str, prompt: str, runs: list, observed: dict, fields: dict, runtime: str, rep: int, split: str) -> dict:
    accepted = None
    for run in runs:
        for t in run.trace or []:
            if t.get("type") == "finish" and t.get("accepted"):
                accepted = t.get("args")
    answer = json.dumps(accepted if accepted is not None else {"no_answer": True, "observed": observed}, ensure_ascii=False, default=str)
    agents = {_agent_id(r.agent): {"agent_id": _agent_id(r.agent), "agent_type": "LlmAgent", "instruction": f"{r.agent} ({r.prompt_version})"}
              for r in runs}
    turn_events = [{"author": "user", "content": {"role": "user", "parts": [{"text": prompt}]}}]
    for r in runs:
        turn_events += events(r)
    return {"eval_case_id": f"{part}:{case_id}:rep{rep}", "part": part, "case": case_id, "rep": rep, "runtime": runtime, "split": split,
            "prompt": {"role": "user", "parts": [{"text": prompt}]},
            "responses": [{"response": {"role": "model", "parts": [{"text": answer}]}}],
            "agent_data": {"agents": agents or {"none": {"agent_id": "none", "instruction": "no agent ran"}},
                           "turns": [{"turn_index": 0, "events": turn_events}]},
            "observed": observed, "runs": [run_summary(r) for r in runs], **fields}


def _runs(app, opp_id: str | None, agent: str | None = None) -> list:
    from sqlalchemy import select
    from api import models as m
    with app.state.db.session() as s:
        q = select(m.AgentRun).order_by(m.AgentRun.on, m.AgentRun.id)
        q = q.where(m.AgentRun.opportunity_id == opp_id) if opp_id else q.where(m.AgentRun.agent == agent)
        rows = list(s.scalars(q))
        s.expunge_all()
    return [r for r in rows if not (r.model or "").startswith(("demo", "rules"))]


def _opp_by_company(app, company: str) -> str | None:
    from sqlalchemy import select
    from api import models as m
    with app.state.db.session() as s:
        return s.scalar(select(m.Opportunity.id).join(m.Company).where(m.Company.name == company).limit(1))


def generate_part(part: str, cases: dict, settings, judge, runtime: str, rep: int, prepare=None) -> list[dict]:
    import api.check_quality as Q
    app, api = Q.new_app(f"{part}-{runtime}-{rep}", settings, prepare, seed_demo=part == "discovery")
    out = []
    try:
        if part == "evidence":
            res = Q.run_evidence(app, api, cases["evidence"]["cases"], 0)
            for i, (case, row) in enumerate(zip(cases["evidence"]["cases"], res["rows"])):
                out.append(eval_case(case["id"], part, f"Evidence agent: read the notes of case {case['id']} and propose evidence.",
                                     _runs(app, f"q-active-{i}"), row, {"expect": case["expect"], "forbid": case["forbid"]},
                                     runtime, rep, split_of(i)))
        elif part == "research":
            res = Q.run_research(app, api, cases["research"]["cases"], 0)
            for i, (case, row) in enumerate(zip(cases["research"]["cases"], res["rows"])):
                oid = _opp_by_company(app, case["company"])
                out.append(eval_case(case["company"], part, f"Research agent: research {case['company']} and write the brief.",
                                     _runs(app, oid) if oid else [], row, {k: case[k] for k in ("signal", "dates", "contact", "people")},
                                     runtime, rep, split_of(i)))
        elif part == "outreach":
            res = Q.run_outreach(app, api, cases["outreach"], judge, 0)
            for i, (case, row) in enumerate(zip(cases["outreach"]["cases"], res["rows"])):
                row = {k: v for k, v in row.items() if k not in ("body", "subject")} | {"subject": row.get("subject"), "body": row.get("body")}
                out.append(eval_case(case["company"], part, f"Outreach agent: write the first email to {case['contact']['name']} at {case['company']}.",
                                     _runs(app, f"q-identified-{i}"), row, {"rubric": res.get("keys") or [], "standard": res.get("standard"), "tone": case["tone"]},
                                     runtime, rep, split_of(i)))
        else:
            res = Q.run_discovery(app, api, cases["discovery"])
            out.append(eval_case("round", part, f"Discovery agent: {cases['discovery']['intent']}", _runs(app, None, "Discovery agent"),
                                 {k: v for k, v in res.items() if k != "unit"}, {"not_a_fit": cases["discovery"]["not_a_fit"]},
                                 runtime, rep, "dev"))
    finally:
        app.state.db.engine.dispose()
    return out


def save(folder: Path, part: str, cases: list[dict]) -> None:
    for split in ("dev", "holdout"):
        mine = [c for c in cases if c["split"] == split]
        if mine:
            d = folder / part / split
            d.mkdir(parents=True, exist_ok=True)
            (d / f"rep{mine[0]['rep']}.json").write_text(json.dumps({"eval_cases": mine}, indent=1, ensure_ascii=False, default=str),
                                                         encoding="utf-8")


def generate(argv: list[str], prepare=None, judge=None) -> int:
    """prepare(app) and judge let the tests plug in a fake AI; normal runs leave them out."""
    from api.settings import load_settings
    import api.check_quality as Q
    opts = _opts(argv, {"--runtime": "adk", "--parts": ",".join(PARTS), "--reps": "3", "--limit": "", "--out": ""})
    runtime, parts, reps = opts["--runtime"], [p for p in opts["--parts"].split(",") if p], int(opts["--reps"])
    if runtime not in ("adk", "legacy") or any(p not in PARTS for p in parts):
        print(f"--runtime adk|legacy, --parts from {', '.join(PARTS)}")
        return 2
    os.environ["AGENT_RUNTIME"] = runtime
    os.environ["WEB_MODE"] = "mock"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    from api.agent_runtime import runtime as chosen
    assert chosen() == runtime, f"AGENT_RUNTIME={runtime} is not available here"
    settings = load_settings()
    cases = Q.load_cases()
    if opts["--limit"]:
        for name in ("evidence", "research", "outreach"):
            cases[name]["cases"] = cases[name]["cases"][:int(opts["--limit"])]
    judge = judge or (Q.judge_provider(settings) if "outreach" in parts else None)
    folder = Path(opts["--out"]) if opts["--out"] else OUT / f"{datetime.now():%Y%m%d-%H%M}-{runtime}"
    meta = {"runtime": runtime, "provider": settings.ai_provider, "model": settings.ai_model or "(default)",
            "agent_models": settings.agent_models, "judge": judge.label if judge else None, "reps": reps, "parts": parts,
            "limit": opts["--limit"] or None, "started": datetime.now().isoformat(timespec="seconds")}
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Traces for agents-cli: runtime {runtime}, {reps} repetition(s), parts {', '.join(parts)} -> {folder}")
    start = time.time()
    for rep in range(1, reps + 1):
        for part in parts:
            print(f"\n[{part}] repetition {rep}/{reps} on {runtime}")
            save(folder, part, generate_part(part, cases, settings, judge, runtime, rep, prepare))
    meta["seconds"] = round(time.time() - start)
    (folder / "run.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    print(f"\nSaved to {folder}")
    return 0


# ---------- grading and the parity bar ----------
# Per metric: "hard" = the ADK mean must reach this value; otherwise the ADK mean may be at most this much below legacy.
BAR = {
    "evidence": {"no_overclaim": ("hard", 1.0), "evidence_recall": ("drop", 0.05), "task_completed": ("drop", 0.05)},
    "research": {"no_invented_name": ("hard", 1.0), "research_checks": ("drop", 0.08), "task_completed": ("drop", 0.10)},
    "outreach": {"outreach_rubric": ("drop", 0.30), "no_unsupported_claim": ("drop", 0.15), "task_completed": ("drop", 0.10)},
    "discovery": {"discovery_no_misfit": ("hard", 1.0), "discovery_fit": ("drop", 0.15), "task_completed": ("drop", 0.34)},
}
REPORTED = ("model_turns", "answers_sent_back", "tokens_in_k")  # shown, not gated: effort and cost


def agents_cli_env() -> dict:
    """agents-cli builds its Google client even for local metrics, which never call it. Without Google Cloud credentials
    on this computer, give it a placeholder that is never used; a metric that did call Google would fail loudly."""
    env = dict(os.environ)
    if not env.get("GOOGLE_APPLICATION_CREDENTIALS") and not (Path.home() / "AppData/Roaming/gcloud/application_default_credentials.json").exists():
        stub = OUT / "offline-adc.json"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(json.dumps({"type": "authorized_user", "client_id": "offline", "client_secret": "offline",
                                    "refresh_token": "offline"}), encoding="utf-8")
        env["GOOGLE_APPLICATION_CREDENTIALS"] = str(stub)
        env.setdefault("GOOGLE_CLOUD_PROJECT", "offline-eval")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def grade(folder: Path, part: str, split: str) -> Path | None:
    traces = folder / part / split
    if not traces.exists():
        return None
    out = folder / "grades" / f"{part}-{split}"
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["agents-cli", "eval", "grade", "--traces", str(traces), "--config", str(EVAL_DIR / f"{part}.yaml"), "--output", str(out), "--qps", "50"]
    print("$ " + " ".join(cmd))
    p = subprocess.run(cmd, env=agents_cli_env(), capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = [l for l in (p.stdout + p.stderr).splitlines() if l.strip()][-4:]
    print("\n".join("    " + l for l in tail))
    found = sorted(out.glob("results_*.json"))
    return found[-1] if found else None


def means(result: Path) -> dict:
    data = json.loads(result.read_text(encoding="utf-8"))
    return {m["metric_name"]: m.get("mean_score") for m in data.get("summary_metrics") or []}


def gate(argv: list[str]) -> int:
    if len(argv) < 2:
        print("python -m api.adk_eval gate <legacy traces folder> <adk traces folder> [--split dev|holdout|all]")
        return 2
    legacy, adk = Path(argv[0]), Path(argv[1])
    splits = {"all": ["dev", "holdout"]}.get(_opts(argv[2:], {"--split": "all"})["--split"], [_opts(argv[2:], {"--split": "all"})["--split"]])
    lines, ok = [], True
    for split in splits:
        for part in PARTS:
            a, b = grade(legacy, part, split), grade(adk, part, split)
            if not (a and b):
                continue
            subprocess.run(["agents-cli", "eval", "compare", str(a), str(b)], env=agents_cli_env())
            la, ad = means(a), means(b)
            lines.append(f"\n{part} ({split})")
            for metric, (kind, value) in BAR.get(part, {}).items():
                x, y = la.get(metric), ad.get(metric)
                if y is None:
                    passed, why = False, "no ADK score"
                elif kind == "hard":
                    passed, why = y >= value - 1e-9, f"must be {value:g}"
                else:
                    passed, why = x is None or y >= x - value, f"at most {value:g} below legacy"
                ok &= passed
                lines.append(f"  {'PASS' if passed else 'FAIL'}  {metric:<22} legacy {_f(x)}  adk {_f(y)}   ({why})")
            for metric in REPORTED:
                if metric in la or metric in ad:
                    lines.append(f"  info  {metric:<22} legacy {_f(la.get(metric))}  adk {_f(ad.get(metric))}")
    lines.append(f"\nPARITY: {'PASS' if ok else 'FAIL'}")
    text = "\n".join(lines)
    print(text)
    (adk / "parity.txt").write_text(f"legacy: {legacy}\nadk: {adk}\n{text}\n", encoding="utf-8")
    return 0 if ok else 1


def _f(x) -> str:
    return "  -  " if x is None else f"{x:.3f}"


def _opts(argv: list[str], defaults: dict) -> dict:
    out, it = dict(defaults), iter(argv)
    for a in it:
        if a in out:
            out[a] = next(it, "")
    return out


def pair(argv: list[str]) -> int:
    """Both runtimes on the same cases, then the parity bar: what "ADK Eval.cmd" runs."""
    opts = _opts(argv, {"--reps": "1", "--parts": ",".join(PARTS), "--limit": ""})
    stamp = f"{datetime.now():%Y%m%d-%H%M}"
    extra = ["--reps", opts["--reps"], "--parts", opts["--parts"]] + (["--limit", opts["--limit"]] if opts["--limit"] else [])
    folders = {}
    for runtime in ("legacy", "adk"):
        folders[runtime] = OUT / f"{stamp}-{runtime}"
        # each runtime in a process of its own, so nothing one run loaded or counted leaks into the other
        code = subprocess.run([sys.executable, "-B", "-m", "api.adk_eval", "generate", "--runtime", runtime, "--out", str(folders[runtime])] + extra,
                              cwd=ROOT).returncode
        if code:
            print(f"Generating the {runtime} traces failed (exit {code}).")
            return code
    return gate([str(folders["legacy"]), str(folders["adk"])])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["pair"]:
        return pair(argv[1:])
    if argv[:1] == ["generate"]:
        return generate(argv[1:])
    if argv[:1] == ["gate"]:
        return gate(argv[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
