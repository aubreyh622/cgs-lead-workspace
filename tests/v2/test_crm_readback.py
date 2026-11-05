"""The CRM read-back: edits made in the CRM are not overwritten, a person decides; the CRM opportunity's stage and tasks
done there are reported; the next step is a CRM task. Insightly over the fake Insightly, the workflow on the MockCRM."""
from api.toolkit.crm import MockCRM, core_name, search_name

from tests.v2.test_crm import LEAD, ME, FakeInsightly, accept, get, insightly, make, post


def test_insightly_reads_fields_back_and_leaves_skipped_fields_alone():
    fake = FakeInsightly()
    crm = insightly(fake)
    lid = crm.save_lead(LEAD)["id"]
    lead = crm.get_lead(lid)
    assert lead["fields"]["priority_tier"] == "2" and lead["fields"]["status"] == "Identified" and lead["fields"]["title"] == "COO"
    crm.save_lead({**LEAD, "title": "CEO", "status": "contacted", "target_problem": "Third foundry"}, lid,
                  skip={"title", "status", "target_problem"})
    put = [x[3] for x in fake.calls if x[0] == "PUT" and x[1] == "Leads"][-1]
    assert "TITLE" not in put and "LEAD_STATUS_ID" not in put  # Insightly keeps what it is not sent
    assert "Target_Problem__c" not in {c["FIELD_NAME"] for c in put["CUSTOMFIELDS"]} and "LAST_NAME" in put


def test_insightly_reads_the_deal_and_writes_tasks():
    fake = FakeInsightly()
    crm = insightly(fake)
    assert crm.get_deal("45") == {"id": "45", "name": "Deal", "state": "WON", "stage": "Qualification", "updated": "2026-11-05 10:00:00",
                                  "url": "https://crm.na1.insightly.com/details/Opportunity/45"}
    t = crm.save_task("lead", "501", "Send outreach", "2026-11-06")
    body = next(x[3] for x in fake.calls if x[0] == "POST" and x[1] == "Tasks")
    assert body["LINKS"] == [{"LINK_OBJECT_NAME": "Lead", "LINK_OBJECT_ID": 501}] and body["DUE_DATE"] == "2026-11-06 00:00:00"
    done = crm.save_task("lead", "501", "Send outreach", None, task_id=t["id"], done=True)
    assert done["completed"] and done["status"] == "COMPLETED"


def test_company_names_match_without_legal_endings():
    assert core_name("Meridian Industries, Inc.") == core_name("meridian industries") == "meridian industries"
    assert core_name("Acme Co") == "acme" and core_name("Co") == "co"
    assert search_name("Meridian Industries, Inc.") == "Meridian Industries"
    crm = MockCRM()
    crm.save_lead({"first": "A", "last": "B", "company": "Meridian Industries Inc", "status": "identified"})
    assert len(crm.matches(company="Meridian Industries")["leads"]) == 1
    assert crm.matches(company="Meridian Holdings")["leads"] == []


def test_the_description_names_the_title_once(tmp_path):
    from api import crm_sync as CRM
    from api import models as m
    api = make(tmp_path)
    with api.app.state.db.session() as s:
        o = s.get(m.Opportunity, "meridian")
        d = CRM.lead_record(o)["description"]
        assert o.brief.hypothesis.lower().startswith(o.title.lower()) and d.startswith(o.brief.hypothesis)


def lead_of(api, crm, oid):
    return crm.leads[get(api, oid)["crm"]["leadId"]]


REPLY = {"text": "Thanks. Could we talk on Wednesday 4 November?", "on": "2026-10-28"}


def test_an_edit_made_in_the_crm_is_not_overwritten_and_a_person_decides(tmp_path):
    api = make(tmp_path)
    crm = api.app.state.crm
    post(api, "/api/candidates/c1/pick")
    accept(api, "corvane")
    lid = get(api, "corvane")["crm"]["leadId"]
    crm.edit(lid, title="Chief Digital Officer (from Insightly)")
    post(api, "/api/opportunities/corvane/outreach/sent", {"via": "Email"})  # a workspace change: the sync runs
    o = get(api, "corvane")
    lead = lead_of(api, crm, "corvane")
    assert lead["title"] == "Chief Digital Officer (from Insightly)" and lead["status"] == "Contacted"  # the status still follows
    [x] = o["crm"]["conflicts"]
    assert x["field"] == "title" and x["crm"] == "Chief Digital Officer (from Insightly)" and x["workspace"]
    assert any("job title was changed there" in a["text"] for a in o["activity"])
    assert api.get("/api/crm/state", headers=ME).json()["conflicts"] == 1
    # keep Insightly's: no longer asked, and later syncs leave it alone
    post(api, "/api/opportunities/corvane/crm/resolve", {"field": "title", "use": "crm"})
    post(api, "/api/opportunities/corvane/outreach/reply", REPLY)
    o = get(api, "corvane")
    assert o["crm"]["conflicts"] == [] and o["crm"]["kept"] == ["title"] and o["crm"]["status"] == "Active"
    assert lead_of(api, crm, "corvane")["title"] == "Chief Digital Officer (from Insightly)"
    # the read-back finds a new edit; use the workspace's value: it goes out at once
    crm.edit(lid, rating=2)
    assert post(api, "/api/crm/pull")["changes"] >= 1
    [x] = get(api, "corvane")["crm"]["conflicts"]
    assert x["field"] == "rating" and x["crm"] == "2" and x["workspace"] == "4"
    post(api, "/api/opportunities/corvane/crm/resolve", {"field": "rating", "use": "workspace"})
    assert lead_of(api, crm, "corvane")["rating"] == 4 and get(api, "corvane")["crm"]["conflicts"] == []
    post(api, "/api/opportunities/corvane/crm/resolve", {"field": "rating", "use": "crm"}, ok=409)  # nothing left to decide


def test_a_status_changed_in_the_crm_stays_until_a_person_decides(tmp_path):
    api = make(tmp_path)
    crm = api.app.state.crm
    post(api, "/api/candidates/c1/pick")
    accept(api, "corvane")
    crm.edit(get(api, "corvane")["crm"]["leadId"], status="Nurture")
    post(api, "/api/opportunities/corvane/outreach/sent", {"via": "Email"})
    o = get(api, "corvane")
    assert o["stage"] == "contacted" and lead_of(api, crm, "corvane")["status"] == "Nurture"  # neither side moved by the other
    assert [(x["field"], x["crm"], x["workspace"]) for x in o["crm"]["conflicts"]] == [("status", "Nurture", "Contacted")]


def test_a_lead_someone_else_made_keeps_its_values(tmp_path):
    from api import models as m
    api = make(tmp_path)
    crm = api.app.state.crm
    post(api, "/api/candidates/c1/pick")
    with api.app.state.db.session() as s:
        s.get(m.Opportunity, "corvane").contact.email = "elena@corvane.example"
        s.commit()
    lid = crm.save_lead({"first": "Elena", "last": "Fischer", "title": "Old title", "email": "elena@corvane.example",
                         "company": "Corvane", "status": "nurture"})["id"]
    accept(api, "corvane")
    o = get(api, "corvane")
    assert o["crm"]["leadId"] == lid and o["crm"]["conflicts"] == [] and {"title", "status"} <= set(o["crm"]["kept"])
    assert crm.leads[lid]["title"] == "Old title" and crm.leads[lid]["status"] == "Nurture"
    assert crm.leads[lid]["description"]  # an empty field is filled


def test_the_next_step_is_a_task_and_the_read_back_reports_the_crm(tmp_path):
    from api.demo_agents import STAR
    api = make(tmp_path)
    crm = api.app.state.crm
    post(api, "/api/candidates/c1/pick")
    accept(api, "corvane")
    [t] = crm.tasks.values()
    assert t["title"] == "Send outreach" and t["due"] == "2026-10-29" and t["record"] == "lead"
    post(api, "/api/opportunities/corvane/outreach/sent", {"via": "Email"})
    [t] = crm.tasks.values()
    assert t["title"] == "Follow up if no reply"  # the same task, renamed and moved
    t["completed"] = True  # someone ticks it off in the CRM
    assert post(api, "/api/crm/pull")["changes"] == 1
    assert any("was marked done there" in a["text"] for a in get(api, "corvane")["activity"])
    assert post(api, "/api/crm/pull")["changes"] == 0  # reported once
    post(api, "/api/opportunities/corvane/outreach/reply", REPLY)
    mine = lambda: [x for x in crm.tasks.values() if not x["completed"] and x["rid"] in (o["crm"]["leadId"], o["crm"]["opportunityId"])]
    o = get(api, "corvane")
    assert [x["title"] for x in mine()] == ["Hold the first conversation"]  # a new task, not the done one
    post(api, "/api/opportunities/corvane/notes", {"text": STAR["note"], "on": "2026-11-04"})
    for p in get(api, "corvane")["proposals"]:
        post(api, f"/api/proposals/{p['id']}", {"action": "accept"})
    pack = post(api, "/api/opportunities/corvane/packs", {})["id"]
    post(api, f"/api/packs/{pack}/decision", {"decision": "approve", "role": "Director of Operations"})
    o = get(api, "corvane")
    did = o["crm"]["opportunityId"]
    [t] = mine()
    assert t["record"] == "opportunity" and t["rid"] == did  # the next step moved to the CRM opportunity
    crm.deals[did]["stage"] = "Proposal"
    assert post(api, "/api/crm/pull")["changes"] == 1
    o = get(api, "corvane")
    assert o["crm"]["dealStage"] == "Proposal" and o["stage"] == "qualification"  # reported, never moved here
    assert any("at the stage Proposal" in a["text"] for a in o["activity"])


def test_the_daily_job_reads_back_then_syncs(tmp_path):
    from datetime import date
    from api import autonomy as AU
    from api.services import Ctx
    api = make(tmp_path)
    crm = api.app.state.crm
    post(api, "/api/candidates/c1/pick")
    accept(api, "corvane")
    crm.edit(get(api, "corvane")["crm"]["leadId"], title="Edited there")
    with api.app.state.db.session() as s:
        c = Ctx(s=s, actor="Patrol", today=date(2026, 10, 30), settings=api.app.state.settings, crm_factory=lambda: crm)
        status, text = AU.do_crm_sync(c, None, None)
    assert status == "done" and "1 new change(s)" in text
    assert get(api, "corvane")["crm"]["conflicts"][0]["field"] == "title"


def test_read_only_mode_reads_back_and_never_writes(tmp_path):
    api = make(tmp_path, mode="read_only")
    assert post(api, "/api/crm/pull")["read"] == 0  # nothing linked yet: read-only never makes leads
    post(api, "/api/opportunities/meridian/crm/resolve", {"field": "title", "use": "workspace"}, ok=409)
