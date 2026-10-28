"""The AI exam: grade the evidence agent (A2 + A3) against notes with known answers.

Double-click "AI Exam.cmd", or run:  python -m api.ai_exam [model ...]
With no model names it uses AI_MODEL from .env. Give several names to compare models side by side, e.g.
    python -m api.ai_exam gemini-3.5-flash-lite gemini-3.5-flash

Grading follows the team rule "when unsure, say less":
  hit        an expected item, with a value in the allowed range
  missed     an expected item left out, or given below the range (acceptable, but counted)
  over       an over-claim: a forbidden item, or a value above the range (the serious error)
  wrong      a condition or blocker with the wrong value
  extra      anything else proposed (reported, not penalized)
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys

from api.knowledge import library
from api.live_agents import extract_evidence
from api.llm import LLMError, make_provider
from api.settings import ROOT, load_settings

EXAM = Path(__file__).parent / "seed" / "ai_exam.json"


def _matches(rule: dict, p: dict) -> bool:
    if rule.get("kind") != p["kind"] or ("key" in rule and rule["key"] != p["key"]):
        return False
    if "value" in rule and rule["value"] != p["value"]:
        return False
    if "above" in rule and not (isinstance(p["value"], (int, float)) and not isinstance(p["value"], bool) and p["value"] > rule["above"]):
        return False
    return True


def grade(case: dict, proposals: list[dict]) -> dict:
    r = {"hit": [], "missed": [], "over": [], "wrong": [], "extra": []}
    used = set()
    for e in case["expect"]:
        found = next((i for i, p in enumerate(proposals) if p["kind"] == e["kind"] and p["key"] == e["key"]), None)
        name = f"{e['key']}"
        if found is None:
            r["missed"].append(name)
            continue
        used.add(found)
        v = proposals[found]["value"]
        if e["kind"] == "score":
            if e["min"] <= v <= e["max"]:
                r["hit"].append(f"{name}={v:g}")
            elif v > e["max"]:
                r["over"].append(f"{name}={v:g} (max {e['max']:g})")
            else:
                r["missed"].append(f"{name}={v:g} (low)")
        elif e["kind"] == "cond":
            (r["hit"] if v == e["value"] else r["wrong"]).append(f"{name}={v}")
        else:
            r["hit"].append(name)
    for i, p in enumerate(proposals):
        label = f"{p['key']}={p['value'] if not isinstance(p['value'], float) else format(p['value'], 'g')}"
        if any(_matches(f, p) for f in case["forbid"]):
            if label not in " ".join(r["over"]):
                r["over"].append(label)
        elif i not in used:
            r["extra"].append(label)
    r["expected"] = len(case["expect"])
    return r


def run(provider, cases: list[dict]) -> dict:
    results, totals = [], {"hit": 0, "expected": 0, "over": 0, "wrong": 0, "errors": 0, "tokens": 0}
    for case in cases:
        try:
            lib = library().search(case["notes"] + " " + case["title"], k=3)
            (kept, dropped), usage = extract_evidence(provider, case["notes"], {"opportunity": case["title"]}, lib)
        except LLMError as e:
            results.append({"case": case, "error": str(e)})
            totals["errors"] += 1
            continue
        g = grade(case, kept)
        g["dropped"] = dropped
        results.append({"case": case, "grade": g})
        totals["hit"] += len(g["hit"])
        totals["expected"] += g["expected"]
        totals["over"] += len(g["over"])
        totals["wrong"] += len(g["wrong"])
        totals["tokens"] += (usage.tokens_in or 0) + (usage.tokens_out or 0)
    return {"results": results, "totals": totals}


def main(argv: list[str]) -> int:
    settings = load_settings()
    cases = json.loads(EXAM.read_text(encoding="utf-8"))["cases"]
    models = argv or [settings.ai_model or ""]
    lines = [f"AI exam  {datetime.now():%Y-%m-%d %H:%M}   provider {settings.ai_provider}   {len(cases)} cases (synthetic notes)", ""]
    summary = []
    for model in models:
        try:
            provider = make_provider(settings.ai_provider, model)
        except LLMError as e:
            lines.append(f"Setup problem: {e}")
            break
        lines.append(f"=== {provider.label} ===")
        out = run(provider, cases)
        for r in out["results"]:
            if "error" in r:
                lines.append(f"  {r['case']['id']:<18} ERROR: {r['error']}")
                continue
            g = r["grade"]
            lines.append(f"  {r['case']['id']:<18} hit {len(g['hit'])}/{g['expected']}   over-claims {len(g['over'])}   wrong {len(g['wrong'])}")
            for k in ("over", "wrong", "missed", "extra"):
                if g[k]:
                    lines.append(f"      {k}: {', '.join(g[k])}")
            if g["dropped"]:
                lines.append(f"      dropped by code checks: {g['dropped']}")
        t = out["totals"]
        summary.append(f"  {provider.label:<36} hit {t['hit']}/{t['expected']}   over-claims {t['over']}   wrong {t['wrong']}   errors {t['errors']}   tokens {t['tokens']}")
        lines.append("")
    lines += ["SUMMARY (best: most hits with 0 over-claims and 0 wrong)", *summary]
    text = "\n".join(lines)
    print(text)
    out_file = ROOT / "runtime" / "ai-exam-result.txt"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(text, encoding="utf-8")
    print(f"\nSaved to {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
