"""Manager agent shadow check on the full demo sample (14 synthetic opportunities), with the real AI.

Run:  python -m api.check_manager            (MANAGER_MODE is set to shadow for this run only)
The demo records are loaded into a live-mode workspace on the synthetic internet. The script then jumps through several
dates. On each date the daily patrol runs: the rules queue their work, the manager agent reviews the board and decides,
and all queued agent work is carried out, so the workspace changes the way it would in use. Nobody applies inbox items.
The manager's decisions are recorded and compared with the rules' work that day; nothing it decides is carried out.
Results: runtime/manager-shadow-result.txt and runtime/manager-shadow-result.json
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import warnings

warnings.filterwarnings("ignore", message=".*httpx.*")

from api.settings import ROOT, Settings, load_settings  # noqa: E402

DAYS = (1, 8, 15, 30, 60)  # days after the demo's today
LINES: list[str] = []
ROWS = (("both", "agree"), ("manager only", "managerOnly"), ("rules only", "rulesOnly"), ("note to owner", "notes"), ("refused", "refused"))


def say(text: str = "") -> None:
    print(text, flush=True)
    LINES.append(text)


def main(prepare=None, days=DAYS) -> int:
    """prepare(app) lets the tests plug in a fake AI; normal runs leave it out."""
    os.environ["WEB_MODE"] = "mock"
    LINES.clear()
    from fastapi.testclient import TestClient
    from api import autonomy as AU
    from api.main import create_app

    s = load_settings()
    db = Path(tempfile.mkdtemp()) / "manager-shadow.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()  # the 14 synthetic demo opportunities
    app = create_app(Settings(app_mode="live", db_path=db, ai_provider=s.ai_provider, ai_model=s.ai_model))
    if prepare:
        prepare(app)
    api = TestClient(app)
    me = {"X-User": "Quinn Harper"}
    api.patch("/api/agents", headers=me, json={"manager_mode": "shadow", "paused": False})
    start = api.get("/api/agents").json()["today"]
    say(f"Manager agent shadow check  {datetime.now():%Y-%m-%d %H:%M}")
    say(f"AI: {s.ai_provider} {s.ai_model or '(default model)'}   Data: the 14 synthetic demo opportunities, starting {start}")
    say(f"Dates: {', '.join(f'+{d}' for d in days)} days. Nobody applies inbox items in between.\n")

    totals = {k: 0 for _, k in ROWS}
    repeats, results, failed, moved = 0, [], 0, 0
    for d in days:
        api.post(f"/api/demo/advance-days?days={d - moved}", headers=me)
        moved = d
        today = api.get("/api/agents").json()["today"]
        queued = api.post("/api/agents/patrol", headers=me).json()["queued"]
        ran = AU.run_pending(app, limit=100)
        failed += sum(r["status"] == "failed" for r in ran)
        cmp = api.get("/api/manager/compare", params={"day": today}).json()
        review = next((r for r in ran if r["kind"] == "manager_review"), None)
        say(f"{today} (+{d})  rules queued {sum(j['kind'] != 'manager_review' for j in queued)}, agents ran {len(ran)} jobs"
            + (f", {sum(r['status'] == 'failed' for r in ran)} failed" if any(r["status"] == "failed" for r in ran) else ""))
        if not review or review["status"] != "done":
            say(f"  Manager review did not finish: {review['result'] if review else 'not queued'}")
        say("  " + ", ".join(f"{len(cmp.get(k, []))} {label}" for label, k in ROWS))
        for label, key in ROWS:
            for x in cmp.get(key, []):
                extra = "  (repeats recent work)" if x.get("repeatsRecentWork") else f"  -> {x['refusal'][:100]}" if x.get("refusal") else ""
                say(f"    {label:<14} {x['kind']:<16} {x['company']:<26} {x['why'][:120]}{extra}")
                repeats += bool(x.get("repeatsRecentWork"))
            totals[key] += len(cmp.get(key, []))
        results.append(cmp)
        say()

    distinct = {k: {(x["kind"], x["opportunityId"]) for cmp in results for x in cmp.get(k, [])} for _, k in ROWS}
    decided = totals["agree"] + totals["managerOnly"] + totals["rulesOnly"]
    runs = [r for r in api.get("/api/agent-runs", params={"limit": 200}).json() if r["agent"] == "Manager agent"]
    detail = [api.get(f"/api/agent-runs/{r['id']}").json() for r in runs]
    say("Summary")
    say(f"  Work decisions: {decided}. Both: {totals['agree']}, manager only: {totals['managerOnly']} "
        f"({repeats} repeat recent work), rules only: {totals['rulesOnly']}.")
    if decided:
        say(f"  Agreement: {totals['agree'] / decided:.0%} of all work decisions; the manager covered "
            f"{totals['agree'] / max(1, totals['agree'] + totals['rulesOnly']):.0%} of the rules' work.")
    say(f"  Notes to owners: {totals['notes']} (about {len(distinct['notes'])} opportunities). Refused by code: {totals['refused']}. "
        f"Failed agent jobs: {failed}.")
    say(f"  Distinct over the whole run: both {len(distinct['agree'])}, manager only {len(distinct['managerOnly'])}, "
        f"rules only {len(distinct['rulesOnly'])}. In shadow mode the manager's work is never carried out, so it may decide it again.")
    quiet = sorted({x["company"] for cmp in results for x in cmp.get("rulesOnly", [])}
                   - {x["company"] for cmp in results for k in ("agree", "managerOnly", "notes") for x in cmp.get(k, [])})
    say(f"  Opportunities the rules acted on that the manager never decided anything about: {', '.join(quiet) or 'none'}.")
    say(f"  Manager cost: {sum(r['calls'] for r in detail)} AI requests, {sum(r['tokensIn'] or 0 for r in detail):,} tokens in, "
        f"{sum(r['tokensOut'] or 0 for r in detail):,} out, over {len(detail)} reviews.")
    out = ROOT / "runtime"
    out.mkdir(parents=True, exist_ok=True)
    (out / "manager-shadow-result.txt").write_text("\n".join(LINES), encoding="utf-8")
    (out / "manager-shadow-result.json").write_text(json.dumps({"days": results, "totals": totals, "repeats": repeats,
                                                               "reviews": [{"id": r["id"], "trace": r["trace"]} for r in detail]},
                                                              indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nSaved to {out / 'manager-shadow-result.txt'} (and .json with every step of each review)")
    return 0 if len(detail) == len(days) and not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
