"""Manager-as-orchestrator check: the manager's triage, memory, scorecard and lessons with the real AI, on invented data.

Double-click "Check Orchestrator.cmd", or run:  python -m api.check_orchestrator
The web, the companies, the client replies and the reviewer decisions are invented (api/seed/mock_web.json and below).
Throwaway databases; nothing is sent anywhere but the AI provider. About 40 AI calls, about 5 minutes.

Story (the manager is on):
  1. Orchard Lane   research, outreach, the client's reply and the call notes of 7 Nov. The evidence agent asks for "the CEO"
                    -> the manager's triage decides on that request, remembers Daniel's promise ("come back within two weeks")
                    and writes the owner one message. 15 days later the promise is overdue -> the patrol sends it back to the
                    manager, which asks the owner for a nudge.
  2. Northstar      the approver returns the pack asking "Who signs off above $250K?" -> the triage answers that first.
  3. Lessons        reviewers raised the evidence agent's N twice in five, saying the notes stated the business impact ->
                    the manager proposes a lesson; a person approves it; the evidence agent must still not raise N for
                    notes that state no impact (Northwind).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import os
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore", message=".*httpx.*")

from api.settings import ROOT, Settings, load_settings  # noqa: E402

LINES: list[str] = []
PAUSE = 2
ME = {"X-User": "Alex Morgan"}
REVIEWS = [("edited", 2, 3, "The notes state the lost orders, which is the business impact; that meets N 3."),
           ("edited", 2, 3, "Impact is stated (two key customers lost), so N is 3, not 2."),
           ("accepted", 3, 3, None), ("accepted", 2, 2, None),
           ("rejected", 3, None, "The quote describes a different plant, not this client's problem.")]


def say(text: str = "") -> None:
    print(text, flush=True)
    LINES.append(text)


class Check:
    def __init__(self):
        self.failed: list[str] = []

    def __call__(self, ok: bool, what: str, detail: str = "") -> bool:
        say(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ({detail})" if detail else ""))
        if not ok:
            self.failed.append(what)
        return ok


def main(prepare=None) -> int:
    """prepare(app) lets the tests plug in a fake AI; normal runs leave it out."""
    os.environ["WEB_MODE"] = "mock"
    LINES.clear()
    if hasattr(sys.stdout, "reconfigure"):  # a Windows console cannot show every character (the folder name, the AI's text)
        sys.stdout.reconfigure(errors="replace")
    from fastapi.testclient import TestClient
    from sqlalchemy import delete
    from api import autonomy as AU
    from api import jobs as J
    from api import models as m
    from api.main import create_app
    from api.search import MockSearch

    s0 = load_settings()
    check = Check()
    sims = MockSearch().simulations
    say(f"CGS Workspace manager-as-orchestrator check  {datetime.now():%Y-%m-%d %H:%M}")
    say(f"AI: {s0.ai_provider} {s0.ai_model or '(default model)'}   Web: synthetic internet (invented companies)\n")

    def workspace(seed: bool):
        db = Path(tempfile.mkdtemp()) / "orchestrator-check.sqlite3"
        if seed:  # the 14 demo opportunities
            create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
        app = create_app(Settings(app_mode="live", db_path=db, ai_provider=s0.ai_provider, ai_model=s0.ai_model))
        if prepare:
            prepare(app)
        api = TestClient(app)
        api.patch("/api/agents", headers=ME, json={"manager_mode": "on", "ai_budget": 400})
        return app, api

    def quiet_week(app):
        """Keep the weekly discovery, feedback and lessons rounds out of the story (lessons are tried in part 3)."""
        with app.state.db.session() as s:
            today = AU.agent_ctx(app, s).today.isoformat()
            for k in ("last_discovery", "last_feedback_review", "last_lessons_review"):
                J.set_setting(s, k, today)
            s.commit()

    def run_board(app):
        for x in AU.run_pending(app, pause=0 if prepare else PAUSE):
            mark = {"done": "OK ", "skipped": "-- ", "failed": "ERR", "queued": "..."}.get(x["status"], x["status"])
            say(f"    {mark} [{x['agent']:<15}] {x['kind']:<15} {x['result'][:120]}")

    def messages(api, oid):
        return [i for i in api.get("/api/inbox", params={"owner": "Alex Morgan"}).json()
                if i["opportunityId"] == oid and i["kind"] in ("actions", "advice")]

    def show_message(api, oid):
        for i in messages(api, oid):
            say(f"    Owner's message: {i['title']}")
            for line in (i["body"] or "").splitlines()[:5]:
                say(f"      {line[:180]}")

    def memory(api, oid):
        mem = api.get(f"/api/opportunities/{oid}/memory").json()
        say(f"    Memory: {mem['summary'][:300]}")
        for c in mem["commitments"]:
            say(f"      {c['id']} [{c['status']}{', OVERDUE' if c['overdue'] else ''}] {c['who']}: {c['what'][:80]} (due {c['due']})")
        return mem

    # ---------- 1. Orchard Lane ----------
    say("1. Orchard Lane: the client's notes name the approver only as \"the CEO\", and Daniel promises to come back in two weeks")
    try:
        app, api = workspace(seed=False)
        quiet_week(app)
        sim = sims["orchardlanefoods.example"]
        oid = api.post("/api/opportunities", headers=ME, json={"company": "Orchard Lane Foods", "source": "Referral"}).json()["id"]
        api.post("/api/research/sync", headers=ME)
        researched = api.get(f"/api/opportunities/{oid}").json()["stage"] == "review"
        check(researched, "research brief ready for Step 4")
        if researched:
            api.post(f"/api/opportunities/{oid}/contact", headers=ME, json={"name": "Daniel Ruiz", "title": "Chief Operating Officer",
                                                                           "note": "leadership page"})
            api.post(f"/api/opportunities/{oid}/step4", headers=ME, json={"decision": "accept", "rating": 4, "owner": "Alex Morgan",
                                                                          "next_action": "Send outreach", "duplicates_checked": True})
            api.post(f"/api/opportunities/{oid}/outreach/generate", headers=ME, json={"tone": "standard"})
            api.post(f"/api/opportunities/{oid}/outreach/sent", headers=ME, json={"via": "Email"})
            today = date.fromisoformat(api.get("/api/agents").json()["today"])
            api.post(f"/api/demo/advance-days?days={max(0, (date(2026, 11, 7) - today).days)}", headers=ME)  # the call was on 7 Nov
            quiet_week(app)
            api.post(f"/api/opportunities/{oid}/outreach/reply", headers=ME, json={"text": sim["reply"]})
            n = api.post(f"/api/opportunities/{oid}/notes", headers=ME, json={"text": sim["notes"]}).json()
            check("CEO" in (n.get("handoffs") or []), "the evidence agent asks for the CEO", f"handoffs {n.get('handoffs')}")
            jobs = api.get("/api/agents/jobs", params={"opportunity": oid}).json()
            check(not [j for j in jobs if j["kind"] == "person_lookup"] and any(j["kind"] == "manager_triage" for j in jobs),
                  "the request goes to the manager, not straight to the research agent")
            run_board(app)
            decided = {d["kind"]: d for d in api.get("/api/manager/decisions", params={"scope": "triage"}).json()}
            check("person_lookup" in decided and decided["person_lookup"]["outcome"] in ("accepted", "declined"),
                  "the manager decides on the request", decided.get("person_lookup", {}).get("outcome", "no decision"))
            mem = memory(api, oid)
            promise = next((c for c in mem["commitments"] if "two weeks" in c["quote"]), None)
            check(bool(promise and promise["due"]), "Daniel's promise is remembered with its quote and a due date",
                  promise["due"] if promise else "not remembered")
            show_message(api, oid)
            check(len(messages(api, oid)) == 1, "the owner gets one message")

            api.post("/api/demo/advance-days?days=15", headers=ME)
            quiet_week(app)
            say("  15 days later: the patrol")
            queued = api.post("/api/agents/patrol", headers=ME).json()["queued"]
            back = [j for j in queued if j["kind"] == "manager_triage" and j["opportunityId"] == oid]
            for j in queued:
                say(f"    patrol: {j['kind']:<15} {j['reason'][:150]}")
            check(bool(back) and "promised" in back[0]["reason"], "the overdue promise sends the opportunity back to the manager")
            run_board(app)
            mem = memory(api, oid)
            check(any(c["overdue"] for c in mem["commitments"]), "the memory marks the promise overdue")
            show_message(api, oid)
            check(len(messages(api, oid)) == 1, "still one message for the owner (one voice)")
    except Exception as e:  # a broken step must not hide the others
        check(False, "part 1 ran to the end", f"{type(e).__name__}: {e}")

    # ---------- 2. Northstar ----------
    say("\n2. Northstar: the approver returns the pack asking \"Who signs off above $250K?\"")
    try:
        app, api = workspace(seed=True)
        pack = next(p for p in api.get("/api/opportunities/northstar").json()["packs"] if p["status"] == "open")
        api.post(f"/api/packs/{pack['id']}/decision", headers=ME, json={"decision": "return", "role": "Director of Operations",
                                                                        "questions": "Who signs off above $250K?"})
        job = next(j for j in api.get("/api/agents/jobs", params={"opportunity": "northstar"}).json() if j["status"] == "queued")
        check(job["kind"] == "manager_triage", "the returned pack goes to the manager's triage")
        AU.run_job(app, job["id"])
        show_message(api, "northstar")
        msg = messages(api, "northstar")
        first = (msg[0]["data"].get("suggestions") or [{}])[0] if msg else {}
        check(first.get("gap_key") == "approver", "the first next action answers the approver", first.get("title", "no message"))
        check(not [j for j in api.get("/api/agents/jobs", params={"opportunity": "northstar"}).json() if j["kind"] == "gap_actions"],
              "no fallback to the next-actions step was needed")
    except Exception as e:
        check(False, "part 2 ran to the end", f"{type(e).__name__}: {e}")

    # ---------- 3. Lessons ----------
    say("\n3. Lessons: reviewers raised the evidence agent's N twice in five decisions")
    try:
        app, api = workspace(seed=True)
        with app.state.db.session() as s:
            today = AU.agent_ctx(app, s).today
            for t in (m.Evidence, m.Proposal, m.InboxItem):
                s.execute(delete(t))
            s.add(m.Note(id="n-check", opportunity_id="meridian", type="Meeting notes", on=today - timedelta(days=3), text="(synthetic)", added_by="check"))
            for i, (st, v, f, why) in enumerate(REVIEWS):
                s.add(m.Proposal(id=f"pr-check{i}", opportunity_id="meridian", note_id="n-check", kind="score", key="N", value=v, quote="(synthetic)",
                                 evidence_type="client_statement", status=st, final_value=f, reason=why))
            s.commit()
        found = api.get("/api/lessons/findings").json()
        check(any(f["ref"] == "evidence:N" for f in found), "the scorecard finds that reviewers raise N")
        job = api.post("/api/lessons/review", headers=ME).json()["job"]
        AU.run_job(app, job["id"])
        lessons = api.get("/api/lessons").json()
        for x in lessons:
            say(f"    {x['label']} [{x['status']}] {x['agent']}: {x['text']}")
        ev = next((x for x in lessons if x["agent"] == "Evidence agent" and x["status"] == "proposed"), None)
        if check(bool(ev), "the manager proposes a lesson for the evidence agent"):
            api.post(f"/api/lessons/{ev['id']}/decision", headers=ME, json={"decision": "approve", "reason": "Orchestrator check"})
            api.post("/api/opportunities/atlas/notes", headers=ME, json={"text": sims["northwindfluid.example"]["notes"]})
            props = [p for p in api.get("/api/opportunities/atlas").json().get("proposals", []) if p["status"] == "pending" and p["key"] == "N"]
            values = [p["value"] for p in props]
            say(f"    evidence agent on Northwind's notes (no business impact stated): N {values or 'not proposed'}")
            check(all(v <= 2 for v in values), "with the lesson, N is not raised where the notes state no impact")
            uses = api.get("/api/lessons", params={"status": "approved"}).json()[0]["uses"]
            check(uses >= 1, "the approved lesson was in the evidence agent's prompt", f"uses {uses}")
    except Exception as e:
        check(False, "part 3 ran to the end", f"{type(e).__name__}: {e}")

    return finish(check)


def finish(check: Check) -> int:
    say("")
    if check.failed:
        say(f"RESULT: {len(check.failed)} check{'s' if len(check.failed) != 1 else ''} failed: " + "; ".join(check.failed))
    else:
        say("RESULT: the manager runs the agents as intended: triage, memory, one voice, approver first, lessons with approval.")
    out = ROOT / "runtime" / "orchestrator-check-result.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nSaved to {out}")
    return 0 if not check.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
