"""The first email after Step 4: the outreach agent drafts it on its own; the owner approves and sends it (or edits it first).
Nothing goes out without that click, and an address is never guessed."""
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings

ME = {"X-User": "Alex Morgan"}


def accepted(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "first.sqlite3")))  # demo mode: rules instead of AI, a mock mailbox
    api.post("/api/candidates/c1/pick", headers=ME)
    api.post("/api/opportunities/corvane/step4", headers=ME, json={
        "decision": "accept", "rating": 4, "owner": "Alex Morgan", "next_action": "Send outreach", "due": "2026-10-29",
        "duplicates_checked": True, "scope": "theme"})
    return api


def first_draft_item(api):
    return next((i for i in api.get("/api/inbox").json() if i["kind"] == "draft" and i["opportunityId"] == "corvane"), None)


def test_accepting_a_lead_queues_the_first_draft_and_approve_sends_it(tmp_path):
    api = accepted(tmp_path)
    jobs = api.get("/api/agents/jobs", params={"opportunity": "corvane"}).json()
    assert any(j["kind"] == "first_draft" and j["status"] == "queued" for j in jobs)
    assert api.app.state.mail.outbox == [] and first_draft_item(api) is None  # nothing before the agent has worked

    api.post("/api/agents/run", headers=ME)
    item = first_draft_item(api)
    assert item and item["title"].startswith("First email") and api.app.state.mail.outbox == []  # drafted, not sent
    send = lambda **kw: api.post(f"/api/inbox/{item['id']}", headers=ME, json={"action": "send", "payload_hash": item["payloadHash"], **kw})
    assert send().json()["code"] == "no_address"  # the person gives the address; the agent never guesses one
    r = send(to="mark.ellison@corvane.example").json()
    assert r["status"] == "applied" and "sent to mark.ellison@corvane.example" in r["note"]
    sent = api.app.state.mail.outbox
    assert len(sent) == 1 and sent[0]["to"] == "mark.ellison@corvane.example"
    o = api.get("/api/opportunities/corvane").json()
    assert o["stage"] == "contacted" and o["outreach"]["status"] == "sent" and o["outreach"]["via"] == "Gmail"
    assert send(to="mark.ellison@corvane.example").json()["code"] == "wrong_status"  # never twice


def test_applying_the_first_draft_puts_it_in_step_5_for_editing(tmp_path):
    api = accepted(tmp_path)
    api.post("/api/agents/run", headers=ME)
    item = first_draft_item(api)
    r = api.post(f"/api/inbox/{item['id']}", headers=ME, json={"action": "apply", "payload_hash": item["payloadHash"]}).json()
    assert "Step 5 draft" in r["note"]
    o = api.get("/api/opportunities/corvane").json()
    assert o["stage"] == "identified" and o["outreach"]["status"] == "draft" and o["outreach"]["body"] == item["data"]["body"]
    assert api.app.state.mail.outbox == []


def test_no_second_first_draft_when_a_person_already_wrote_one(tmp_path):
    api = accepted(tmp_path)
    api.put("/api/opportunities/corvane/outreach/draft", headers=ME, json={"subject": "Hello", "body": "My own words."})
    api.post("/api/agents/run", headers=ME)
    assert first_draft_item(api) is None
    job = next(j for j in api.get("/api/agents/jobs", params={"opportunity": "corvane"}).json() if j["kind"] == "first_draft")
    assert job["status"] == "skipped" and "already exists" in job["result"]
