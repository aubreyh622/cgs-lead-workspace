"""Full workflow check: Steps 1-8 with the real AI, on the synthetic internet and simulated client replies.

Double-click "Check Workflow.cmd", or run:  python -m api.check_workflow
Every AI agent runs for real (Gemini by default). The web, the companies and the client's replies are invented
(api/seed/mock_web.json), so nothing reaches a real company. Human decisions are made by this script, as a
careful consultant would make them, and printed so you can see where a person is in the loop:
  - client declined, or a blocker is confirmed: submit the pack so the approver can close it (or nurture it, if deferred);
  - client deferred or only sent a courtesy reply: move to nurture with a review date;
  - client engaged: run the internal conflict check, enter a revenue estimate, then submit if the rules say Ready,
    otherwise stay Active and turn the first suggested action into a task.
The result is compared with the story written for each simulated company (tools/build_mock_web.py).
About 10 AI calls; it pauses a few seconds between calls to stay inside the free tier's per-minute limit.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
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


def consultant_plan(label: str, blocker: str | None) -> str:
    """What a careful consultant does after the conversation: close, nurture or qualify."""
    if blocker == "PROJECT_DEFERRED":
        return "nurture"
    if blocker or label == "decline":
        return "close"
    if label in ("defer", "courtesy"):
        return "nurture"
    return "qualify"


def close_reason(blocker: str | None, notes: str) -> str:
    import re
    if blocker == "FUNDING_UNAVAILABLE":
        return "No budget this year"
    if re.search(r"\b(selected|chose|went with|existing integration partner)\b", notes, re.I):
        return "Went with another advisor"
    return "Client declined"


from api.autonomy import review_date  # noqa: E402  (shared with the action agent)


def main(argv: list[str] | None = None, prepare=None) -> int:
    """prepare(app) lets the tests plug in a fake AI; normal runs leave it out."""
    import os
    import random
    import re
    import sys
    wanted = " ".join(argv if argv is not None else sys.argv[1:]).strip().lower()
    os.environ["WEB_MODE"] = "mock"  # this check always uses the synthetic internet
    from fastapi.testclient import TestClient
    from api.main import create_app
    from api.search import MockSearch

    LINES.clear()
    s = load_settings()
    sims = MockSearch().simulations
    say(f"CGS Workspace full workflow check  {datetime.now():%Y-%m-%d %H:%M}")
    say(f"AI: {s.ai_provider} {s.ai_model or '(default model)'}   Web: synthetic internet (invented companies)\n")
    db = Path(tempfile.mkdtemp()) / "workflow-check.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db, ai_provider=s.ai_provider, ai_model=s.ai_model))
    if prepare:
        prepare(app)
    api = TestClient(app)
    me = {"X-User": "Alex Morgan"}
    ok = True

    def call(label, method, url, actor="AI", **kw):
        nonlocal ok
        r = getattr(api, method)(url, headers=me, **kw)
        good = r.status_code == 200
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        say(f"  {'OK ' if good else 'ERR'} [{actor:<6}] {label}" + ("" if good else f"  -> {body.get('error', r.text)[:200]}"))
        ok &= good
        if actor == "AI":
            time.sleep(PAUSE)
        return body if good else None

    say("Step 1  Discovery")
    rec = call("Discovery agent searches and nominates candidates", "post", "/api/candidates/recommend", json={"intent": "US manufacturers with AI pilots that have not scaled, or a recent merger"})
    if not rec:
        return finish(False)
    new = [c for c in api.get("/api/candidates", params={"status": "new"}).json() if c["round"] == rec["round"]]
    say(f"         {rec['candidates']} kept after code checks {rec['checks']}")
    for c in new:
        say(f"         - {c['company']}  ({c['person'] or 'contact to confirm'})  {c['why'][:80]}")
    web = MockSearch()
    sim_names = {re.sub(r"[^a-z]", "", p["title"].split(" | ")[0].lower()): d for d, _ in sims.items()
                 for p in web.pages if p["url"] == f"https://www.{d}/"}
    key = lambda name: re.sub(r"[^a-z]", "", name.lower())
    with_sim = [c for c in new if key(c["company"]) in sim_names]
    if wanted:
        pick = next((c for c in new if wanted in c["company"].lower()), None)
        manual = next((p["title"].split(" | ")[0] for p in web.pages if p["url"].endswith(".example/") and wanted in p["title"].lower()), None)
    else:
        pick = random.choice(with_sim) if with_sim else None
        manual = random.choice([p["title"].split(" | ")[0] for p in web.pages if p["url"] in {f"https://www.{d}/" for d in sims}])
    if not pick and not manual:
        say(f"         No company called '{wanted}' on the synthetic internet.")
        return finish(False)
    say(f"         Person picks: {pick['company'] if pick else manual + ' (added by hand; not nominated this round)'}")

    say("Steps 2-3  Research")
    if pick:
        oid = call("Pick the candidate; research agent reads its pages", "post", f"/api/candidates/{pick['id']}/pick")["opportunities"][0]
    else:
        oid = call("Add the company; research agent reads its pages", "post", "/api/opportunities", json={"company": manual, "source": "Referral"})["id"]
    api.post("/api/research/sync", headers=me)
    o = api.get(f"/api/opportunities/{oid}").json()
    if o["stage"] != "review":
        say(f"         Research did not finish: {o['research'].get('error')}")
        return finish(False)
    b = o["brief"]
    say(f"         Brief: {len(b['facts'])} quoted facts, contact {o['contact']['name'] or '(to confirm)'} / {o['contact']['title']}, AI suggests {b['recommendation']['decision']}")
    for f in b["facts"][:3]:
        say(f"         - [{f['date']}] {f['text'][:100]}")
    if not o["contact"]["name"]:
        lead_page = next((p for p in web.pages if p["url"] == f"https://www.{o['company']['domain']}/leadership"), None)
        people = re.findall(r"([A-Z][a-z]+ [A-Z][a-z]+), ([^,.]+)", lead_page["content"]) if lead_page else []
        best = next((x for x in people if re.search(r"Digital|Information|Operat|Transformation|Integration|Quality|Data", x[1])), people[0] if people else None)
        if best:
            call(f"Confirm the contact from the leadership page: {best[0]}", "post", f"/api/opportunities/{oid}/contact", actor="Person",
                 json={"name": best[0], "title": best[1], "note": lead_page["url"]})

    say("Step 4  Accept")
    call("Accept: rating 4, owner Alex Morgan, duplicate check done", "post", f"/api/opportunities/{oid}/step4", actor="Person",
         json={"decision": "accept", "rating": 4, "owner": "Alex Morgan", "next_action": "Send tailored outreach", "duplicates_checked": True, "minutes": 5})

    say("Step 5  Outreach")
    d = call("A1 writes the outreach draft", "post", f"/api/opportunities/{oid}/outreach/generate", json={"tone": "standard"})
    if d:
        say("         Subject: " + d["subject"])
        for line in d["body"].splitlines()[:6]:
            say("         | " + line)
        for w in d.get("warnings", []):
            say("         WARNING: " + w)
    call("Consultant sends it from their own email and records it", "post", f"/api/opportunities/{oid}/outreach/sent", actor="Person", json={"via": "Email"})

    domain = api.get(f"/api/opportunities/{oid}").json()["company"]["domain"]
    sim = sims.get(domain)
    if not sim:
        say(f"         {domain} has no simulated client conversation; using a neutral reply.")
        sim = {"reply": "Thanks for reaching out. Happy to have a short call next week.", "notes": "Short intro call. The client listened and asked for a follow-up note."}
    say("Step 6  Engage")
    r = call("Client reply recorded; A2 labels it", "post", f"/api/opportunities/{oid}/outreach/reply", json={"text": sim["reply"]})
    label = r["replyLabel"] if r else "pending"
    if r:
        say(f"         Reply label: {label}  (stage is now Active)")
    n = call("Conversation notes added; A2 + A3 propose evidence", "post", f"/api/opportunities/{oid}/notes", json={"text": sim["notes"]})
    if n:
        say(f"         {n['proposals']} proposals; dropped by checks: quotes {n['droppedForInvalidQuote']}, other {n.get('droppedOther') or 'none'}")
    o = api.get(f"/api/opportunities/{oid}").json()
    pending = [x for x in o["proposals"] if x["status"] == "pending"]
    for p in pending:
        say(f"         - {p['kind']:<5} {p['key']:<10} = {str(p['value']):<5} [{p['evidenceType']}] \"{p['quote'][:60]}\"")
        api.post(f"/api/proposals/{p['id']}", headers=me, json={"action": "accept", "reason": "Checked against the conversation notes"})
    if pending:
        say(f"  OK  [Person] Consultant checks each proposal against the notes and accepts them")
    o = api.get(f"/api/opportunities/{oid}").json()
    plan = consultant_plan(label, o["blocker"])
    today = date.today()

    if plan == "nurture" and not o["blocker"]:
        when = review_date(sim["notes"], today)
        say(f"Step 6  Consultant decision: the client is not ready now ({label} reply), so move to nurture")
        call(f"Move to nurture, review on {when:%d %b %Y}", "post", f"/api/opportunities/{oid}/nurture", actor="Person",
             json={"reason": f"Client {'deferred' if label == 'defer' else 'sent a courtesy reply only'}; revisit then", "review_on": when.isoformat()})
    elif plan in ("close", "nurture"):
        from backend.rules import REASONS
        why = f"blocker confirmed: {REASONS.get(o['blocker'], o['blocker'])}" if o["blocker"] else "the client declined"
        say(f"Step 7  Consultant decision: {why}. Closing or nurturing is a Step 8 decision, so the pack goes to the approver")
        pk = call("Submit the pack with the reason", "post", f"/api/opportunities/{oid}/packs", actor="Person",
                  json={"exception": f"Not for qualification: {why}. Recommend {'nurture' if plan == 'nurture' else 'closing'}."})
        say("Step 8  Decide")
        if pk and plan == "close":
            reason = close_reason(o["blocker"], sim["notes"])
            call(f"Director of Operations closes it: {reason}", "post", f"/api/packs/{pk['id']}/decision", actor="Person",
                 json={"decision": "close", "role": "Director of Operations", "reason": reason})
        elif pk:
            when = review_date(sim["notes"], today)
            call(f"Director of Operations moves it to nurture, review on {when:%d %b %Y}", "post", f"/api/packs/{pk['id']}/decision", actor="Person",
                 json={"decision": "nurture", "role": "Director of Operations", "review_on": when.isoformat()})
    else:
        call("Consultant runs the internal conflict check and confirms no conflict", "post", f"/api/opportunities/{oid}/review", actor="Person",
             json={"kind": "cond", "key": "conflict", "value": True, "reason": "Insightly checked: no competing CGS engagement and no conflicting evidence"})
        call("Consultant enters a revenue estimate ($400K-$600K)", "post", f"/api/opportunities/{oid}/review", actor="Person",
             json={"kind": "revenue", "value": {"low": 400000, "high": 600000}, "reason": "First estimate from the scope discussed on the call"})
        say("Step 7  Readiness")
        a = api.get(f"/api/opportunities/{oid}").json()["assessment"]
        say(f"         Rules engine: Q {a['ql']:g}-{a['qu']:g}, coverage {a['coverage']}%, P {a['pl']:g}-{a['pu']:g}, {a['readiness']}")
        say(f"         Open gaps: {', '.join(g['label'] for g in a['gaps'][:5]) or 'none'}")
        if a["readiness"] == "Ready":
            pk = call("Submit the qualification pack", "post", f"/api/opportunities/{oid}/packs", actor="Person", json={})
            say("Step 8  Approve")
            if pk:
                call("Director of Operations approves", "post", f"/api/packs/{pk['id']}/decision", actor="Person",
                     json={"decision": "approve", "role": "Director of Operations"})
        else:
            call("A4 suggests next actions for the open gaps", "post", f"/api/opportunities/{oid}/suggestions")
            sug = api.get(f"/api/opportunities/{oid}").json()["suggestions"]
            for x in sug:
                say(f"         - {x['title']}")
            if sug:
                due = today + timedelta(days=7)
                call(f"Consultant turns the first one into a task (Alex Morgan, due {due:%d %b})", "post", f"/api/suggestions/{sug[0]['id']}/task",
                     actor="Person", json={"assignee": "Alex Morgan", "due": due.isoformat()})
            say("         Not Ready yet, so the consultant keeps collecting evidence. The opportunity stays Active.")

    o = api.get(f"/api/opportunities/{oid}").json()
    stage_names = {"qualification": "Qualification (approved)", "active": "Active (still collecting evidence)", "nurture": "Nurture (revisit later)", "closed": "Closed"}
    say(f"         Final stage: {stage_names.get(o['stage'], o['stage'])}" + (f", Insightly sync: {o['crmSync']}" if o["stage"] == "qualification" else ""))
    story = sim.get("story")
    if story:
        match = o["stage"] in story["stages"]
        say(f"         Story for this company: {story['text']}")
        say(f"         {'Matches the story.' if match else 'Differs from the story. Look at the proposals above: the AI read the notes differently.'}")

    say("Learning loop")
    fb = call("Feedback agent reviews recent decisions", "post", "/api/direction/analyze")
    if fb:
        say("         " + fb["summary"][:160])
    runs = [x for x in api.get("/api/agent-runs", params={"limit": 50}).json() if not x["model"].startswith("demo")]
    say(f"\nReal AI runs: {sum(x['status'] == 'completed' for x in runs)} completed, {sum(x['status'] == 'failed' for x in runs)} failed")
    for x in reversed(runs):
        say(f"  - {x['agent']:<36} {x['status']:<9} {x['model']}")
    return finish(ok and o["stage"] in stage_names)


def finish(ok: bool) -> int:
    say("")
    say("RESULT: the full workflow works." if ok else "RESULT: something did not work. Send a screenshot of this window.")
    out = ROOT / "runtime" / "workflow-check-result.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nSaved to {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
