"""Autonomous agents check: watch the agents find work and do it, with the real AI, over five simulated months.

Double-click "Check Agents.cmd", or run:  python -m api.check_agents
The web, the companies and the client replies are invented (api/seed/mock_web.json). About 25 AI calls, about 5 minutes.

Story:
  Day 0     A consultant sets up three leads (Kessler, Orchard Lane, Northwind): research, outreach, replies, call notes.
            Agents react on their own: "not now" from Kessler -> nurture suggestion; Orchard Lane's notes name an approver
            only as "the CEO" -> the evidence agent asks the research agent to find her.
  Day 8     Patrol: Northwind has not replied for a week -> follow-up draft; weekly discovery and feedback rounds.
  April     Patrol: Kessler's nurture date is reached -> the research agent finds news -> asks the outreach agent for a
            reconnect draft. Orchard Lane has stalled -> next actions.
The consultant only reads the inbox and applies what is useful. Nothing is ever sent.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import os
import tempfile
import time
import warnings

warnings.filterwarnings("ignore", message=".*httpx.*")

from api.settings import ROOT, Settings, load_settings  # noqa: E402

LINES: list[str] = []
PAUSE = 4


def say(text: str = "") -> None:
    print(text, flush=True)
    LINES.append(text)


def main(prepare=None) -> int:
    """prepare(app) lets the tests plug in a fake AI; normal runs leave it out."""
    os.environ["WEB_MODE"] = "mock"
    LINES.clear()
    from fastapi.testclient import TestClient
    from api import autonomy as AU
    from api.main import create_app
    from api.search import MockSearch

    s = load_settings()
    say(f"CGS Workspace autonomous agents check  {datetime.now():%Y-%m-%d %H:%M}")
    say(f"AI: {s.ai_provider} {s.ai_model or '(default model)'}   Web: synthetic internet (invented companies)   "
        f"Manager agent: {os.environ.get('MANAGER_MODE', 'off')}\n")
    db = Path(tempfile.mkdtemp()) / "agents-check.sqlite3"  # an empty workspace: only the three leads below
    app = create_app(Settings(app_mode="live", db_path=db, ai_provider=s.ai_provider, ai_model=s.ai_model))
    if prepare:
        prepare(app)
    api = TestClient(app)
    me = {"X-User": "Alex Morgan"}
    sims = MockSearch().simulations
    ok = True

    def call(label, method, url, **kw):
        nonlocal ok
        r = getattr(api, method)(url, headers=me, **kw)
        good = r.status_code == 200
        if not good:
            say(f"  ERR {label}  -> {r.json().get('error', r.text)[:200]}")
            ok = False
        return r.json() if good else None

    def board_run():
        """What the background worker does: work through the board, slowly enough for the free tier."""
        for x in AU.run_pending(app, pause=PAUSE if not prepare else 0):
            mark = {"done": "OK ", "skipped": "-- ", "failed": "ERR", "queued": "..."}[x["status"]]
            company = company_of(x["opportunityId"])
            say(f"  {mark} [{x['agent']:<28}] {x['kind']:<16} {company:<24} {x['result'][:80]}")
            if x["status"] == "failed":
                nonlocal ok
                ok = False

    def show_manager():
        """With MANAGER_MODE=shadow or on: what the manager agent decided today, next to what the rules did."""
        if api.get("/api/agents").json()["managerMode"] == "off":
            return
        cmp = api.get("/api/manager/compare").json()
        if not cmp.get("day"):
            say("  Manager agent: no decisions today.")
            return
        rows = [("both", "agree"), ("manager only", "managerOnly"), ("rules only", "rulesOnly"), ("note to owner", "notes"), ("refused", "refused")]
        say(f"  Manager agent ({cmp['mode']} mode) next to the rules: " + ", ".join(f"{len(cmp[k])} {label}" for label, k in rows))
        for label, key in rows:
            for x in cmp[key]:
                extra = " (repeats recent work)" if x.get("repeatsRecentWork") else f"  -> {x['refusal'][:90]}" if x.get("refusal") else ""
                say(f"    {label:<14} {x['kind']:<16} {x['company']:<24} {x['why'][:110]}{extra}")

    def company_of(oid):
        return api.get(f"/api/opportunities/{oid}").json()["company"]["name"] if oid else "(all)"

    def show_inbox(apply=()):
        items = api.get("/api/inbox", params={"owner": "Alex Morgan"}).json()
        say(f"  Inbox for Alex Morgan: {len(items)} new item{'s' if len(items) != 1 else ''}")
        for i in items:
            say(f"   * [{i['agent']}] {i['title']}")
            for line in (i["body"] or "").splitlines()[:2]:
                say(f"       {line[:150]}")
            if i["kind"] in apply:
                res = call("apply", "post", f"/api/inbox/{i['id']}", json={"action": "apply"})
                if res:
                    say(f"       -> Alex applies it: {res['note']}")

    def setup(company, reply, notes):
        oid = call(f"add {company}", "post", "/api/opportunities", json={"company": company, "source": "Referral"})["id"]
        api.post("/api/research/sync", headers=me)
        o = api.get(f"/api/opportunities/{oid}").json()
        if o["stage"] != "review":
            say(f"  ERR research did not finish for {company}: {o['research'].get('error')}")
            return None
        if not o["contact"]["name"]:
            import re
            page = next((p for p in MockSearch().pages if p["url"] == f"https://www.{o['company']['domain']}/leadership"), None)
            people = re.findall(r"([A-Z][a-z]+ [A-Z][a-z]+), ([^,.]+)", page["content"]) if page else []
            best = next((x for x in people if re.search(r"Digital|Information|Operat|Transformation", x[1])), people[0] if people else None)
            if best:
                call("contact", "post", f"/api/opportunities/{oid}/contact", json={"name": best[0], "title": best[1], "note": page["url"]})
        call("accept", "post", f"/api/opportunities/{oid}/step4", json={"decision": "accept", "rating": 4, "owner": "Alex Morgan",
             "next_action": "Send tailored outreach", "duplicates_checked": True})
        call("draft", "post", f"/api/opportunities/{oid}/outreach/generate", json={"tone": "standard"})
        time.sleep(0 if prepare else PAUSE)
        call("sent", "post", f"/api/opportunities/{oid}/outreach/sent", json={"via": "Email"})
        line = f"  {company:<26} researched, accepted, email sent"
        if reply:
            r = call("reply", "post", f"/api/opportunities/{oid}/outreach/reply", json={"text": reply})
            n = call("notes", "post", f"/api/opportunities/{oid}/notes", json={"text": notes})
            time.sleep(0 if prepare else PAUSE)
            line += f"; reply labeled {r['replyLabel'] if r else '?'}; notes analyzed"
            if n and n.get("handoffs"):
                line += f"; evidence agent asked for help finding: {', '.join(n['handoffs'])}"
        else:
            line += "; no reply yet"
        say(line)
        return oid

    say("Day 0  The consultant works three leads (Steps 1-6). Agents react on their own.")
    k = setup("Kessler Motion Controls", sims["kesslermotion.example"]["reply"], sims["kesslermotion.example"]["notes"])
    orch = setup("Orchard Lane Foods", sims["orchardlanefoods.example"]["reply"], sims["orchardlanefoods.example"]["notes"])
    setup("Northwind Fluid Systems", None, None)
    if not (k and orch):
        return finish(False)
    say("  Agents' board (events and handoffs from today):")
    board_run()
    show_inbox(apply=("nurture", "contact"))

    say("\nDay 8  Nobody clicks anything. The daily patrol runs.")
    call("advance", "post", "/api/demo/advance-days?days=8")
    found = call("patrol", "post", "/api/agents/patrol")["queued"]
    for j in found:
        say(f"  Patrol found: {j['agent']:<28} {company_of(j['opportunityId']):<24} {j['reason']}")
    board_run()
    show_manager()
    show_inbox()

    today = date.fromisoformat(api.get("/api/agents").json()["today"])
    days = (date(2027, 4, 2) - today).days
    say(f"\n{days} days later (2 April 2027)  The patrol runs again.")
    call("advance", "post", f"/api/demo/advance-days?days={days}")
    for j in call("patrol", "post", "/api/agents/patrol")["queued"]:
        say(f"  Patrol found: {j['agent']:<28} {company_of(j['opportunityId']):<24} {j['reason']}")
    board_run()
    show_manager()
    show_inbox()

    board = api.get("/api/agents/jobs", params={"limit": 200}).json()
    handoffs = [j for j in board if j["trigger"] == "handoff"]
    say(f"\nTask board: {len(board)} jobs, {sum(j['status'] == 'done' for j in board)} done, "
        f"{sum(j['status'] == 'failed' for j in board)} failed; {len(handoffs)} handoffs between agents:")
    for j in reversed(handoffs):
        say(f"  - {j['requestedBy']} -> {j['agent']}: {j['reason'][:110]}")
    o = api.get(f"/api/opportunities/{k}").json()
    say(f"Kessler stage: {o['stage']}. Emails sent by the agents: 0 (the workspace never sends).")
    runs = [x for x in api.get("/api/agent-runs", params={"limit": 200}).json() if not x["model"].startswith(("demo", "rules"))]
    say(f"Real AI runs: {sum(x['status'] == 'completed' for x in runs)} completed, {sum(x['status'] == 'failed' for x in runs)} failed")
    want = {"defer_nurture", "person_lookup", "followup_draft", "research_refresh", "reconnect_draft"}
    done = {j["kind"] for j in board if j["status"] == "done"}
    if want - done:
        say(f"Did not happen: {', '.join(sorted(want - done))}")
    return finish(ok and not (want - done))


def finish(ok: bool) -> int:
    say("")
    say("RESULT: the agents find and do their work on their own." if ok else "RESULT: something did not work. Send a screenshot of this window.")
    out = ROOT / "runtime" / "agents-check-result.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nSaved to {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
