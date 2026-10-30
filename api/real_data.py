"""Real public company data: python -m api.real_data [--request "..."] [--pick 3] [--db runtime/real-public.sqlite3]

Fills a separate workspace database with REAL companies found on the public web, so the prototype can be shown on
something other than invented companies. The synthetic demo database is not touched.

1. The discovery agent searches the real web (Tavily) for what the request says, in plain words, within the research
   direction (config/problem_brief.json), and nominates companies. Code checks every nomination as usual.
2. The first --pick candidates (those with a website first) are picked; the research agent researches them in parallel
   and writes Step 4 briefs from public pages only: dated, quoted facts with sources.
3. Open the result with "Start Real Data Workspace.cmd" and review the briefs at Step 4.

Safety: public web only, no client data. Email and the CRM are off in that database (MAIL_PROVIDER=off, CRM_MODE=disabled):
these are real people, so nothing can be sent to them and no address is guessed. The background agents start paused so
they spend no search credits on their own; a person can resume them (PATCH /api/agents).
Cost: about 10 Tavily credits for discovery and up to 22 per company researched, plus the AI calls (AI_PROVIDER in .env).
"""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import time
import warnings

warnings.filterwarnings("ignore", message=".*httpx.*")

from api.settings import ROOT, Settings, load_settings  # noqa: E402

DEFAULT_DB = "runtime/real-public.sqlite3"
DEFAULT_REQUEST = ("US manufacturers that in the last six months completed an acquisition or merger, or appointed a new "
                   "Chief Digital, Chief Information or Transformation officer, and may now need to plan AI, digital or "
                   "operating-model integration")
ME = {"X-User": "Alex Morgan"}
LINES: list[str] = []


def say(text: str = "") -> None:
    print(text, flush=True)
    LINES.append(text)


def real_settings(db: Path) -> Settings:
    """Live AI on the real web, email and CRM off, research in background threads (so companies run in parallel)."""
    os.environ["WEB_MODE"] = "tavily"
    s = load_settings()
    return Settings(app_mode="live", db_path=db, ai_provider=s.ai_provider, ai_model=s.ai_model, agent_worker=True,
                    research_engine="agent", mail_provider="off", crm_mode="disabled", users=s.users,
                    directors=s.directors, admins=s.admins)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--request", default=DEFAULT_REQUEST, help="What to look for, in plain words")
    ap.add_argument("--pick", type=int, default=3, help="How many candidates to research (each up to 22 Tavily credits)")
    ap.add_argument("--db", default=DEFAULT_DB, help="The separate database for real data")
    a = ap.parse_args(argv)
    if not os.environ.get("TAVILY_API_KEY"):
        load_settings()  # reads .env
    if not os.environ.get("TAVILY_API_KEY"):
        say("TAVILY_API_KEY is not set in .env: real web search is not possible.")
        return 1

    from fastapi.testclient import TestClient
    from api import jobs as J
    from api.main import create_app
    db = ROOT / a.db
    db.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(real_settings(db))
    with app.state.db.session() as s:
        J.set_setting(s, "agents_paused", True)  # nothing spends credits on its own in this database
        s.commit()
    api = TestClient(app)
    say(f"Real public company data  {datetime.now():%Y-%m-%d %H:%M}   database: {a.db}")
    say(f"AI: {app.state.settings.ai_provider}  web: Tavily (real)  email: off  CRM: off\n")

    say(f"[1/2] Discovery agent, request: {a.request}")
    r = api.post("/api/candidates/recommend", json={"intent": a.request}, headers=ME)
    if r.status_code != 200:
        say(f"      FAILED: {r.json().get('error', r.text)[:300]}")
        return finish(False)
    res = r.json()
    say(f"      {res['candidates']} candidate(s) kept after the code checks. Details: {res['checks']}")
    new = [c for c in api.get("/api/candidates", params={"status": "new"}).json() if c["round"] == res["round"]]
    for c in new:
        say(f"      - {c['company'][:40]:<40} {c['domain'][:26]:<26} {c['person'] or '(contact to confirm)'}  [{c['signal']}, {c['signalDate']}]")
        if c.get("quote"):
            say(f"          \"{c['quote'][:150]}\"  {c.get('sourceUrl', '')[:90]}")
    picks = sorted(new, key=lambda c: not c["domain"])[:max(0, a.pick)]
    if not picks:
        say("      No candidates to research.")
        return finish(bool(new))

    say(f"\n[2/2] Research agent on {len(picks)} compan{'y' if len(picks) == 1 else 'ies'}, in parallel (up to about 8 minutes)...")
    oids = []
    for c in picks:
        rr = api.post(f"/api/candidates/{c['id']}/pick", headers=ME)
        if rr.status_code != 200:
            say(f"      {c['company']}: could not start: {rr.json().get('error', rr.text)[:200]}")
            continue
        oids += rr.json()["opportunities"][:1]
    start, waiting = time.monotonic(), set(oids)
    while waiting and time.monotonic() - start < 15 * 60:
        for u in api.post("/api/research/sync", headers=ME).json()["updates"]:
            if u["opportunityId"] in waiting and u["status"] != "running":
                waiting.discard(u["opportunityId"])
                say(f"      ... {int(time.monotonic() - start)} s  {u['opportunityId']}: {u['status']}")
        for oid in list(waiting):  # a run that failed to start never reports through sync
            if api.get(f"/api/opportunities/{oid}").json()["research"]["status"] in ("failed", "done"):
                waiting.discard(oid)
        if waiting:
            time.sleep(15)

    ok = 0
    for oid in oids:
        o = api.get(f"/api/opportunities/{oid}").json()
        b = o["brief"]
        say(f"\n  {o['company']['name']}  ({o['company']['domain'] or 'no website'})  stage: {o['stage']}")
        if o["stage"] != "review":
            say(f"      no brief: {o['research'].get('error') or 'still running after 15 minutes'}")
            continue
        ok += 1
        say(f"      contact: {o['contact']['name'] or '(to confirm)'}  {o['contact']['title']}")
        if b.get("hypothesis"):
            say(f"      why now: {b['hypothesis'][:220]}")
        for f in b["facts"][:4]:
            say(f"      - [{f.get('date') or 'undated'}] {f['text'][:150]}")
            say(f"        {f.get('src', '')[:110]}")
        for fl in b["flags"]:
            say(f"      flag: {fl['type']}: {fl['text'][:120]}")
        say(f"      AI suggests: {b['recommendation'].get('decision')} ({(b['recommendation'].get('reason') or '')[:110]})")
        say(f"      usage: {b.get('usage')}")
    say(f"\n{ok} of {len(oids)} brief(s) ready for Step 4. Open them with \"Start Real Data Workspace.cmd\".")
    return finish(ok > 0)


def finish(ok: bool) -> int:
    say("RESULT: real company data added." if ok else "RESULT: no real briefs this time (see above).")
    out = ROOT / "runtime" / "real-data-result.txt"
    out.write_text("\n".join(LINES), encoding="utf-8")
    print(f"Saved to {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
