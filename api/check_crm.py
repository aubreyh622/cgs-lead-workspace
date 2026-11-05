"""Check the Insightly connection: python -m api.check_crm [--write]

Without --write (reads only): shows the account, checks that every name config/crm.json uses exists in Insightly
(statuses, sources, pipeline stage, custom fields) and runs a duplicate check for one company.

With --write: runs one synthetic opportunity through the whole workflow in a throwaway workspace (demo data, mock email,
CRM_MODE=write_approved) and checks Insightly after each step: Step 4 accept -> lead (Identified) with the research
brief as a note; email sent -> Contacted, with the lead's address in BCC; reply -> Active, reply copied as a note;
qualification approved -> organization, contact and opportunity at the qualification stage, lead converted. Along the
way: the next step as an Insightly task, a title edited in Insightly that the sync does not overwrite (kept by a
person's choice), and the read-back of a stage move and a task done in Insightly.
Each run uses a new company named "CRM Check <time> (TEST)", so runs never collide. Nothing is deleted afterwards:
deleted records go to Insightly's recycle bin and still lock the statuses they use.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import tempfile

from api.settings import ROOT, Settings, _load_dotenv
from api.toolkit.crm import CREDENTIALS, CrmError, InsightlyCRM

ME = {"X-User": "Alex Morgan"}


def say(ok: bool, text: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {text}")
    return ok


def check_reads(crm: InsightlyCRM) -> bool:
    print(f"Signed in to Insightly as {crm.account()} ({crm.web}).")
    problems = crm.check_setup()
    ok = say(not problems, "config/crm.json matches this Insightly account" + ("" if not problems else ": missing " + "; ".join(problems)))
    m = crm.matches(company="Northwind Fabrication (TEST)")
    say(True, f"duplicate check reads: {len(m['leads'])} lead(s), {len(m['organizations'])} organization(s) for a sample company")
    return ok


def check_writes(crm: InsightlyCRM) -> bool:
    from fastapi.testclient import TestClient
    from api.main import create_app
    stamp = datetime.now().strftime("%m%d-%H%M%S")
    company = f"CRM Check {stamp} (TEST)"
    email = f"jordan.check.{stamp}@example.com"
    db = Path(tempfile.mkdtemp(prefix="crm-check-")) / "ws.sqlite3"
    app = create_app(Settings(app_mode="demo", db_path=db, crm_mode="write_approved", crm_provider="insightly"))
    api = TestClient(app)

    def post(url, body=None):
        r = api.post(url, json=body or {}, headers=ME)
        if r.status_code != 200:
            raise RuntimeError(f"{url}: {r.status_code} {r.text[:300]}")
        return r.json()

    def opp():
        return api.get(f"/api/opportunities/{oid}").json()

    print(f"Running a synthetic opportunity through the workflow: {company}")
    oid = post("/api/opportunities", {"company": company, "domain": "", "name": "Jordan Check", "title": "Chief Operating Officer",
                                          "source": "CRM check", "notes": "Synthetic company for the CRM check. Not a real business."})["id"]
    ok = say(opp()["crm"] is None, "in research and review nothing is written to Insightly")
    post(f"/api/opportunities/{oid}/step4", {"decision": "accept", "rating": 3, "owner": "Alex Morgan", "next_action": "Send outreach",
                                             "duplicates_checked": True, "scope": "opportunity"})
    o = opp()
    lead = crm.get_lead(o["crm"]["leadId"]) if o["crm"] and o["crm"]["leadId"] else None
    ok &= say(bool(lead) and lead["status"] == crm.cfg["statuses"]["identified"] and lead["workspace_id"] == oid,
              f"Step 4 accept -> lead {lead['url'] if lead else '(none)'} ({lead['status'] if lead else o['crm']})")
    if not lead:
        return False
    tasks = crm._call("GET", f"Leads/{lead['id']}/Tasks") or []
    ok &= say([t.get("TITLE") for t in tasks] == ["Send outreach"], f"the next step is a task on the lead: {[t.get('TITLE') for t in tasks]}")
    # Insightly changes only the fields a PUT names: the custom fields not sent stay (the sync relies on it to skip fields)
    crm._call("PUT", "Leads", {"LEAD_ID": int(lead["id"]), "CUSTOMFIELDS": [{"FIELD_NAME": crm._field("leadType"), "FIELD_VALUE": crm.cfg["leadType"]}]})
    ok &= say(crm.get_lead(lead["id"])["workspace_id"] == oid, "a PUT leaves the custom fields it does not name as they were")
    # a person edits the lead in Insightly
    crm._call("PUT", "Leads", {"LEAD_ID": int(lead["id"]), "TITLE": "Edited in Insightly (TEST)"})
    api.put(f"/api/opportunities/{oid}/outreach/draft", headers=ME, json={"subject": "CRM check", "body": "A synthetic test email."})
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=ME, params={"to": email}).json()
    post(f"/api/opportunities/{oid}/outreach/email", {"to": email, "mode": "send", "seenHash": p["seenHash"]})
    sent = app.state.mail.outbox[-1]
    ok &= say(sent["bcc"] == lead["mailbox"], f"email sent (mock mailbox, nothing really sent) with BCC {sent['bcc'] or '(none)'}")
    now = crm.get_lead(lead["id"])
    ok &= say(now["status"] == crm.cfg["statuses"]["contacted"], "email sent -> Contacted")
    conflicts = opp()["crm"]["conflicts"]
    ok &= say(now["fields"]["title"] == "Edited in Insightly (TEST)" and [x["field"] for x in conflicts] == ["title"],
              "the title edited in Insightly was not overwritten; the workspace asks a person")
    post(f"/api/opportunities/{oid}/crm/resolve", {"field": "title", "use": "crm"})
    post(f"/api/opportunities/{oid}/outreach/reply", {"text": "Synthetic reply: happy to talk next week."})
    ok &= say(crm.get_lead(lead["id"])["status"] == crm.cfg["statuses"]["active"], "reply recorded -> Active (reply copied as a note)")
    post(f"/api/opportunities/{oid}/packs", {"exception": "CRM check: synthetic opportunity, approved to test the conversion"})
    pack = next(x for x in opp()["packs"] if x["status"] == "open")["id"]
    post(f"/api/packs/{pack}/decision", {"decision": "approve", "role": "Director of Operations"})
    o = opp()
    after = crm.get_lead(lead["id"])
    ok &= say(o["crmSync"] == "synced" and after["converted"] and after["status"] == crm.cfg["statuses"]["converted"] and bool(o["crm"]["opportunityId"]),
              f"qualification approved -> lead converted, opportunity {o['crm']['opportunityUrl'] if o['crm'] else '(none)'}")
    ok &= say(crm.get_lead(lead["id"])["fields"]["title"] == "Edited in Insightly (TEST)", "the kept title is still Insightly's after later syncs")
    deal, task = o["crm"]["opportunityId"], o["crm"]["taskId"]
    t = crm.get_task(task) if task else None
    ok &= say(bool(t) and not t["completed"] and t["title"] == o["next"]["action"], f"the next step moved to a task on the opportunity: {t and t['title']}")
    stages = {x["STAGE_NAME"]: x["STAGE_ID"] for x in crm._call("GET", "PipelineStages") or [] if x["PIPELINE_ID"] == crm._stage()[0]}
    nxt = next((n for n in ("Needs Analysis", "Proposal") if n in stages), None)
    if nxt:  # a person moves the opportunity on in Insightly and ticks the task off
        crm._call("PUT", f"Opportunities/{deal}/Pipeline", {"PIPELINE_ID": crm._stage()[0], "PIPELINE_STAGE_CHANGE": {"STAGE_ID": stages[nxt]}})
    if t:
        crm._call("PUT", "Tasks", {"TASK_ID": int(task), "STATUS": "COMPLETED", "COMPLETED": True, "PERCENT_COMPLETE": 100})
    got = post("/api/crm/pull")
    o = opp()
    ok &= say(got["failed"] == 0 and (not nxt or o["crm"]["dealStage"] == nxt) and o["stage"] == "qualification",
              f"read-back: the opportunity is at {o['crm']['dealStage']} in Insightly; the workspace stage stays {o['stage']}")
    ok &= say(any("was marked done there" in a["text"] for a in o["activity"]), "read-back: the task done in Insightly is in the activity")
    errors = [a["text"] for a in o["activity"] if "sync failed" in a["text"]]
    ok &= say(not errors, "no sync errors" + ("" if not errors else ": " + errors[0]))
    return ok


def main(argv: list[str]) -> int:
    _load_dotenv(ROOT / ".env")
    crm = InsightlyCRM.from_env()
    if not crm.ready():
        print(f"Insightly is not set up. Put INSIGHTLY_API_KEY and INSIGHTLY_API_URL in {CREDENTIALS}.")
        return 1
    try:
        ok = check_reads(crm)
        if "--write" in argv:
            ok = check_writes(crm) and ok
        else:
            print("Reads only. Run with --write to push one synthetic opportunity through the workflow into Insightly.")
    except (CrmError, RuntimeError) as e:
        print(f"Problem: {e}")
        return 1
    print("CRM check passed." if ok else "CRM check found problems (see FAIL above).")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
