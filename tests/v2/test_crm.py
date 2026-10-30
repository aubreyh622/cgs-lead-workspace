"""The CRM connector (Insightly) and the sync that follows people's actions.
Insightly is checked against a fake Insightly (httpx.MockTransport); the workflow runs on the in-memory MockCRM."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from api import toolkit as TK
from api.main import create_app
from api.settings import Settings
from api.toolkit.crm import CrmError, InsightlyCRM, MockCRM, crm_config, make_crm, split_name

ME = {"X-User": "Alex Morgan"}


# ---------- Insightly over a fake Insightly ----------
class FakeInsightly:
    def __init__(self):
        self.calls, self.leads, self.next_id = [], {}, 500

    def new_id(self):
        self.next_id += 1
        return self.next_id

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, req.url.path.split("/v3.1/")[1], dict(req.url.params), body))
        path, q = req.url.path.split("/v3.1/")[1], req.url.params
        assert req.headers["Authorization"].startswith("Basic ")
        if path == "LeadStatuses":
            return httpx.Response(200, json=[{"LEAD_STATUS": n, "LEAD_STATUS_ID": i} for i, n in
                                             enumerate(["Identified", "Contacted", "Active", "Nurture", "Disqualified", "Converted(API)"], 1)])
        if path == "LeadSources":
            return httpx.Response(200, json=[{"LEAD_SOURCE": "AI Research Agent", "LEAD_SOURCE_ID": 77}])
        if path == "Pipelines":
            return httpx.Response(200, json=[{"PIPELINE_NAME": "Opportunity Pipeline", "PIPELINE_ID": 9}])
        if path == "PipelineStages":
            return httpx.Response(200, json=[{"PIPELINE_ID": 9, "STAGE_NAME": "Qualification", "STAGE_ID": 92}])
        if path == "Leads" and req.method == "POST":
            if not body.get("LAST_NAME"):
                return httpx.Response(400, json=[{"FIELD_NAME": "LAST_NAME", "MESSAGE": "Last Name is required"}])
            lid = self.new_id()
            self.leads[lid] = {**body, "LEAD_ID": lid, "CONVERTED": False, "LINKED_EMAIL_ADDRESS": f"x-L{lid}@mailbox.insight.ly"}
            return httpx.Response(200, json=self.leads[lid])
        if path == "Leads" and req.method == "PUT":
            lead = self.leads[body["LEAD_ID"]]
            if lead["CONVERTED"]:
                return httpx.Response(400, text='"Cannot change Lead status of converted Leads"')
            lead.update(body)
            return httpx.Response(200, json=lead)
        if path.startswith("Leads/") and path.count("/") == 1 and path.split("/")[1].isdigit():
            lead = self.leads.get(int(path.split("/")[1]))
            return httpx.Response(200, json=lead) if lead else httpx.Response(404)
        if path.endswith("/Search"):
            if path == "Leads/Search" and q["field_name"] == "Workspace_ID__c":
                return httpx.Response(200, json=[x for x in self.leads.values() if any(
                    c["FIELD_VALUE"] == q["field_value"] for c in x.get("CUSTOMFIELDS", []))])
            return httpx.Response(200, json=[])
        if path in ("Organisations", "Contacts", "Opportunities") and req.method == "POST":
            key = {"Organisations": "ORGANISATION_ID", "Contacts": "CONTACT_ID", "Opportunities": "OPPORTUNITY_ID"}[path]
            return httpx.Response(200, json={**body, key: self.new_id()})
        if path.endswith("/Pipeline") or path.endswith("/Notes"):
            return httpx.Response(200, json={"NOTE_ID": 1})
        if path.endswith("/Links"):
            return httpx.Response(200, json=[] if req.method == "GET" else {"LINK_ID": 1})
        return httpx.Response(500, text="unexpected " + path)


def insightly(fake):
    return InsightlyCRM("key", "https://api.na1.insightly.com/v3.1", http=httpx.Client(transport=httpx.MockTransport(fake)))


LEAD = {"first": "Dana", "last": "Halvorsen", "title": "COO", "email": "dana@halvorsen.example", "company": "Halvorsen (TEST)",
        "rating": 4, "status": "identified", "source": "ai", "owner": "Alex Morgan", "workspace_id": "halvorsen",
        "target_problem": "Second foundry", "evidence_url": "https://news.example/a", "priority_tier": 2, "lead_type": "New Prospect"}


def test_insightly_maps_a_lead_by_names_from_the_config():
    fake = FakeInsightly()
    out = insightly(fake).save_lead(LEAD)
    method, path, _, body = next(c for c in fake.calls if c[0] == "POST" and c[1] == "Leads")
    assert body["LEAD_STATUS_ID"] == 1 and body["LEAD_SOURCE_ID"] == 77 and body["TAGS"] == [{"TAG_NAME": "CGS-Workspace"}]
    assert {c["FIELD_NAME"]: c["FIELD_VALUE"] for c in body["CUSTOMFIELDS"]} == {
        "Workspace_ID__c": "halvorsen", "Target_Problem__c": "Second foundry", "Evidence_URL__c": "https://news.example/a",
        "Priority_Tier__c": 2, "Lead_Type__c": "New Prospect"}
    assert out["status"] == "Identified" and out["mailbox"].endswith("@mailbox.insight.ly")
    assert out["url"] == f"https://crm.na1.insightly.com/details/Lead/{out['id']}"
    assert sum(1 for c in fake.calls if c[1] == "LeadStatuses") == 1  # looked up once


def test_insightly_finds_the_lead_by_workspace_id_and_converts_in_the_right_order():
    fake = FakeInsightly()
    crm = insightly(fake)
    lid = crm.save_lead(LEAD)["id"]
    assert crm.find_lead(workspace_id="halvorsen")["id"] == lid
    r = crm.convert(lid, {"workspace_id": "halvorsen", "company": "Halvorsen (TEST)", "first": "Dana", "last": "Halvorsen",
                          "email": "dana@halvorsen.example", "name": "Halvorsen - foundry"})
    puts = [c[3] for c in fake.calls if c[0] == "PUT" and c[1] == "Leads"]
    assert puts[0] == {"LEAD_ID": int(lid), "LEAD_STATUS_ID": 6}  # status first ...
    assert puts[1]["CONVERTED"] is True and puts[1]["CONVERTED_OPPORTUNITY_ID"] == int(r["opportunity"])  # ... then converted
    assert any(c[1].endswith("/Pipeline") and c[3]["PIPELINE_STAGE_CHANGE"] == {"STAGE_ID": 92} for c in fake.calls)
    with pytest.raises(CrmError, match="Cannot change"):
        crm.save_lead(LEAD, lid)


def test_insightly_explains_problems():
    with pytest.raises(CrmError, match="Last Name is required"):
        insightly(FakeInsightly())._call("POST", "Leads", {"FIRST_NAME": "x"})
    with pytest.raises(CrmError, match="no lead status named 'Nope'"):
        crm = insightly(FakeInsightly())
        crm.cfg = {**crm.cfg, "statuses": {**crm.cfg["statuses"], "identified": "Nope"}}
        crm.save_lead(LEAD)
    with pytest.raises(CrmError, match="not set up"):
        InsightlyCRM("", "").account()
    deny = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(CrmError, match="refused the API key"):
        InsightlyCRM("k", "https://api.na1.insightly.com/v3.1", http=deny).account()


def test_settings_and_names():
    assert make_crm("", "demo", "disabled") is None and make_crm("", "live", "write_approved") is None
    assert isinstance(make_crm("", "demo", "read_only"), MockCRM)
    with pytest.raises(ValueError, match="CRM_PROVIDER"):
        Settings(crm_provider="salesforce")
    assert split_name("Mary Ann Lee") == ("Mary Ann", "Lee") and split_name(None) == ("", "Contact to confirm")
    assert crm_config()["statuses"]["converted"] == "Converted(API)"


# ---------- the workflow, on the mock CRM ----------
def make(tmp_path, mode="write_approved"):
    return TestClient(create_app(Settings(db_path=tmp_path / "crm.sqlite3", crm_mode=mode)))


def post(api, url, body=None, ok=200):
    r = api.post(url, json=body or {}, headers=ME)
    assert r.status_code == ok, r.text
    return r.json()


def get(api, oid):
    return api.get(f"/api/opportunities/{oid}").json()


def accept(api, oid):
    post(api, f"/api/opportunities/{oid}/step4", {"decision": "accept", "rating": 4, "owner": "Alex Morgan", "next_action": "Send outreach",
                                                  "due": "2026-10-29", "duplicates_checked": True, "scope": "theme"})


def test_a_candidate_becomes_a_crm_lead_only_when_a_person_accepts_it(tmp_path):
    api = make(tmp_path)
    crm = api.app.state.crm
    post(api, "/api/candidates/c1/pick")
    assert get(api, "corvane")["stage"] == "review" and crm.leads == {}  # research and review stay in the workspace
    accept(api, "corvane")
    o = get(api, "corvane")
    assert o["crmSync"] == "synced" and o["crm"]["status"] == "Identified"
    lead = crm.leads[o["crm"]["leadId"]]
    assert lead["workspace_id"] == "corvane" and lead["source"] == "ai" and lead["lead_type"] == "New Prospect"
    assert [n["title"] for n in crm.notes] == ["Research brief (CGS workspace)"]
    assert any("Insightly: lead created" in a["text"] for a in o["activity"])


def test_the_lead_follows_the_stages_and_is_converted_at_qualification(tmp_path):
    api = make(tmp_path)
    crm = api.app.state.crm
    post(api, "/api/candidates/c1/pick")
    accept(api, "corvane")
    post(api, "/api/opportunities/corvane/outreach/sent", {"via": "Email"})
    assert get(api, "corvane")["crm"]["status"] == "Contacted"
    post(api, "/api/opportunities/corvane/outreach/reply", {"text": "Thanks. Could we talk on Wednesday 4 November?", "on": "2026-10-28"})
    o = get(api, "corvane")
    assert o["crm"]["status"] == "Active" and any(n["title"].startswith("Email reply") for n in crm.notes)
    from api.demo_agents import STAR
    post(api, "/api/opportunities/corvane/notes", {"text": STAR["note"], "on": "2026-11-04"})
    for p in get(api, "corvane")["proposals"]:
        post(api, f"/api/proposals/{p['id']}", {"action": "accept"})
    pack = post(api, "/api/opportunities/corvane/packs", {})["id"]
    post(api, f"/api/packs/{pack}/decision", {"decision": "approve", "role": "Director of Operations"})
    o = get(api, "corvane")
    assert o["stage"] == "qualification" and o["crmSync"] == "synced"
    assert o["crm"]["converted"] and o["crm"]["status"] == "Converted(API)" and o["crm"]["opportunityId"]
    deal = crm.deals[o["crm"]["opportunityId"]]
    assert deal["stage"] == "Qualification" and deal["workspace_id"] == "corvane" and len(crm.leads) == 1
    assert any(n["record"] == "opportunity" and n["title"] == "Qualification (CGS workspace)" for n in crm.notes)


def test_an_existing_lead_is_linked_not_duplicated(tmp_path):
    api = make(tmp_path)
    crm = api.app.state.crm
    crm.save_lead({"first": "Someone", "last": "Earlier", "company": "Corvane", "status": "identified", "workspace_id": "corvane"})
    post(api, "/api/candidates/c1/pick")
    accept(api, "corvane")
    o = get(api, "corvane")
    assert len(crm.leads) == 1 and o["crm"]["leadId"] == next(iter(crm.leads))
    assert any("no duplicate created" in a["text"] for a in o["activity"])


def test_a_failed_sync_keeps_the_action_and_is_retried(tmp_path):
    api = make(tmp_path)
    crm = api.app.state.crm
    post(api, "/api/candidates/c1/pick")
    crm.fail_next = "Insightly answered 503: busy"
    accept(api, "corvane")
    o = get(api, "corvane")
    assert o["stage"] == "identified" and o["crmSync"] == "sync_failed" and "busy" in o["crm"]["lastError"]
    st = api.get("/api/crm", headers=ME).json()
    assert st["writes"] and any(x["id"] == "corvane" and x["crmSync"] == "sync_failed" for x in st["opportunities"])
    assert post(api, "/api/crm/sync")["synced"] >= 1
    o = get(api, "corvane")
    assert o["crmSync"] == "synced" and o["crm"]["lastError"] is None
    assert [x["workspace_id"] for x in crm.leads.values()].count("corvane") == 1  # the retry did not create a second lead


def test_the_email_is_bcc_to_the_lead_so_the_crm_logs_it(tmp_path):
    api = make(tmp_path)
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["company"] == "Halvorsen Precision Castings")
    assert post(api, "/api/crm/sync?all=true")["synced"] >= 1  # fills the empty CRM from the workspace
    mailbox = get(api, oid)["crm"] and api.app.state.crm.leads[get(api, oid)["crm"]["leadId"]]["mailbox"]
    api.put(f"/api/opportunities/{oid}/outreach/draft", headers=ME, json={"subject": "Your second foundry", "body": "Hi Dana, a short call?"})
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=ME, params={"to": "dana@halvorsen.example"}).json()
    post(api, f"/api/opportunities/{oid}/outreach/email", {"to": p["to"], "mode": "send", "seenHash": p["seenHash"]})
    sent = api.app.state.mail.outbox[-1]
    assert sent["to"] == "dana@halvorsen.example" and sent["bcc"] == mailbox
    assert get(api, oid)["crm"]["status"] == "Contacted"


def test_read_only_mode_checks_for_duplicates_and_writes_nothing(tmp_path):
    api = make(tmp_path, "read_only")
    crm = api.app.state.crm
    post(api, "/api/candidates/c1/pick")
    crm.save_lead({"first": "Ann", "last": "Old", "company": get(api, "corvane")["company"]["name"], "status": "nurture"})
    m = api.get("/api/opportunities/corvane/crm", headers=ME).json()
    assert m["off"] is False and len(m["leads"]) == 1 and "1 lead(s) (1 open)" in m["summary"]
    accept(api, "corvane")
    assert len(crm.leads) == 1 and get(api, "corvane")["crmSync"] == "local_saved"
    assert post(api, "/api/crm/sync", ok=409)["code"] == "crm_off"


def test_with_the_crm_disabled_nothing_changes(tmp_path):
    api = make(tmp_path, "disabled")
    post(api, "/api/candidates/c1/pick")
    assert api.app.state.crm is None and api.get("/api/opportunities/corvane/crm", headers=ME).json()["off"] is True
    accept(api, "corvane")
    assert get(api, "corvane")["crm"] is None


def test_agents_read_the_crm_only_through_the_gate(tmp_path, monkeypatch):
    api = make(tmp_path)
    from api import services as S
    from api.actions import demo_today
    with api.app.state.db.session() as s:
        c = S.Ctx(s=s, actor="x", today=demo_today(s, api.app.state.settings), settings=api.app.state.settings,
                  crm_factory=lambda: api.app.state.crm)
        assert TK.crm_if_allowed("Discovery agent", c) is None  # crm is off in config/toolkit.json
        with pytest.raises(TK.Denied, match="Outreach agent may not use the crm connector"):
            TK.crm_for("Outreach agent", c)  # the code never allows it
        monkeypatch.setattr(TK, "allowed", lambda agent, conn, s=None: True)
        monkeypatch.setattr(TK, "require", lambda agent, conn, s=None: None)
        assert TK.crm_if_allowed("Discovery agent", c) is None  # client data may not go to the AI yet
        monkeypatch.setenv("CLIENT_DATA_TO_AI", "allowed")
        assert TK.crm_if_allowed("Discovery agent", c) is not None


def test_discovery_skips_a_company_the_crm_is_already_working():
    from api import agent_runtime as R
    from api import discovery_agent as D
    crm = MockCRM()
    crm.save_lead({"first": "A", "last": "B", "company": "Kessler Motion Controls", "status": "contacted"})
    result = {"url": "https://news.example/k", "title": "Kessler Motion Controls opens a plant",
              "content": "Kessler Motion Controls opens a second plant in Ohio this spring, the company said."}

    class Search:
        name = "fake"

        def search(self, *a, **k):
            return [result]

    spec, record = D.discovery_agent(Search(), {"problem": "x"}, "", set(), __import__("datetime").date(2026, 10, 30), crm=crm)
    tools = {t.name: t for t in spec.tools}
    tools["search_news"].run({"query": "plant"})
    assert tools["check_company"].run({"company": "Kessler Motion Controls"})["in_crm"]["open_leads"] == 1
    with pytest.raises(R.ToolError, match="already has an open lead"):
        tools["nominate"].run({"company": "Kessler Motion Controls", "url": result["url"], "why": "New plant", "signal": "new plant",
                              "quote": "Kessler Motion Controls opens a second plant in Ohio this spring"})
    assert record["dropped"] == {"in_crm": 1}
