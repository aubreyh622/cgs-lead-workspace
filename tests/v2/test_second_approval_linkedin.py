"""P2 from client meeting 4: an admin marks a person's emails as needing a second approval by someone they choose
(api/people.py, api/undo.py), and the outreach agent recommends LinkedIn outreach a person sends themselves
(api/linkedin.py)."""
import json

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings
from tests.v2.test_toolkit_mail import BODY
from tests.v2.test_live_agents import FakeAI, live_app

ALEX = {"X-User": "Alex Morgan"}
SARAH = {"X-User": "Sarah Patel"}
QUINN = {"X-User": "Quinn Harper"}
ALEX_AS_QUINN = {"X-User": "Alex Morgan", "X-Act-As": "Quinn Harper"}
TO = "dana@halvorsen.example"


@pytest.fixture
def ws(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "p2.sqlite3", admins=("Alex Morgan",)))
    api = TestClient(app)
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["company"] == "Halvorsen Precision Castings")
    api.put(f"/api/opportunities/{oid}/outreach/draft", headers=SARAH, json={"subject": "Your second foundry", "body": BODY})
    return app, api, oid


def approve(api, oid, who=SARAH, mode="send", delay=0, ok=200):
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=who, params={"to": TO}).json()
    r = api.post(f"/api/opportunities/{oid}/outreach/email", headers=who, json={"to": TO, "mode": mode, "seenHash": p["seenHash"], "delay": delay})
    assert r.status_code == ok, r.text
    return r.json()


def todo_rows(api, who):
    return [r for g in api.get("/api/todo", headers=who).json()["groups"] for r in g["items"]]


def activity(api, oid):
    return [a["text"] for a in api.get(f"/api/opportunities/{oid}").json()["activity"]]


# ---------- second approval ----------
def test_only_an_admin_names_a_second_approver(ws):
    app, api, oid = ws
    assert api.put("/api/people/Sarah Patel", headers=SARAH, json={"secondApprover": "Quinn Harper"}).json()["code"] == "not_admin"
    assert api.put("/api/people/Sarah Patel", headers=ALEX, json={"secondApprover": "Sarah Patel"}).json()["code"] == "self_approval"
    assert api.put("/api/people/Sarah Patel", headers=ALEX, json={"secondApprover": "Nobody"}).json()["code"] == "unknown_user"
    r = api.put("/api/people/Sarah Patel", headers=ALEX, json={"secondApprover": "Quinn Harper"}).json()
    assert r["changed"] == ["Sarah Patel's emails now need Quinn Harper's approval before they go out"]
    people = {p["name"]: p for p in r["people"]}
    assert people["Sarah Patel"]["secondApprover"] == "Quinn Harper" and people["Daniel Brooks"]["secondApprover"] == ""


def test_without_a_second_approver_nothing_changes(ws):
    app, api, oid = ws
    assert approve(api, oid)["mode"] == "send" and len(app.state.mail.outbox) == 1


def test_an_email_waits_for_the_approver_and_goes_out_as_its_writer(ws):
    app, api, oid = ws
    api.put("/api/people/Sarah Patel", headers=ALEX, json={"secondApprover": "Quinn Harper"})
    assert approve(api, oid, mode="draft", ok=409)["code"] == "needs_approval"  # Gmail drafts would skip the approval
    r = approve(api, oid)
    assert r["mode"] == "approval" and r["approver"] == "Quinn Harper" and app.state.mail.outbox == []
    w = api.get(f"/api/opportunities/{oid}").json()["outreach"]["waiting"]
    assert w["status"] == "approval" and w["approver"] == "Quinn Harper" and "second foundry" in w["body"]
    assert approve(api, oid, ok=409)["code"] == "queued"  # one email at a time
    # the approver sees it on their to-do list and in their updates; the writer does not have to do anything
    row = next(x for x in todo_rows(api, QUINN) if x["key"] == f"approve:{r['id']}")
    assert row["group"] == "waiting" and row["label"] == "Approve email" and row["open"]["params"] == {"id": oid, "team": "1"}
    assert any("Sarah Patel asks you to approve the email" in x["text"] for x in api.get("/api/updates", headers=QUINN, params={"days": 1}).json()["items"])
    # someone else cannot approve it; the approver (or an admin acting as them) can
    no = api.post(f"/api/outbound/{r['id']}/second-approval", headers=SARAH, json={"decision": "approve"})
    assert no.status_code == 403 and no.json()["code"] == "not_approver"
    ok = api.post(f"/api/outbound/{r['id']}/second-approval", headers=ALEX_AS_QUINN, json={"decision": "approve"}).json()
    assert ok["mode"] == "send" and len(app.state.mail.outbox) == 1 and app.state.mail.outbox[0]["to"] == TO
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["stage"] == "contacted"
    assert any("second approval by Alex Morgan as Quinn Harper" in t for t in activity(api, oid))


def test_the_approver_returns_it_with_a_note_and_the_writer_sends_it_again(ws):
    app, api, oid = ws
    api.put("/api/people/Sarah Patel", headers=ALEX, json={"secondApprover": "Quinn Harper"})
    out = approve(api, oid)["id"]
    assert api.post(f"/api/outbound/{out}/second-approval", headers=QUINN, json={"decision": "return"}).json()["code"] == "no_note"
    r = api.post(f"/api/outbound/{out}/second-approval", headers=QUINN, json={"decision": "return", "note": "Name the plant, not the city."}).json()
    assert r["status"] == "returned" and app.state.mail.outbox == []
    w = api.get(f"/api/opportunities/{oid}").json()["outreach"]["waiting"]
    assert w["status"] == "returned" and w["error"] == "Returned by Quinn Harper: Name the plant, not the city."
    rows = {x["key"]: x for x in todo_rows(api, SARAH)}
    assert rows[f"email:{out}"]["label"] == "Email returned"
    # Sarah changes the draft and approves again; this time Quinn approves with an undo window
    api.put(f"/api/opportunities/{oid}/outreach/draft", headers=SARAH, json={"subject": "Your Halvorsen plant", "body": BODY})
    again = approve(api, oid)["id"]
    q = api.post(f"/api/outbound/{again}/second-approval", headers=QUINN, json={"decision": "approve", "delay": 10}).json()
    assert q["status"] == "queued" and q["seconds"] == 10 and app.state.mail.outbox == []
    assert api.post(f"/api/outbound/{again}/undo", headers=QUINN).json()["status"] == "undone"


def test_the_writer_may_withdraw_it_while_it_waits(ws):
    app, api, oid = ws
    api.put("/api/people/Sarah Patel", headers=ALEX, json={"secondApprover": "Quinn Harper"})
    out = approve(api, oid)["id"]
    assert api.post(f"/api/outbound/{out}/undo", headers=SARAH).json()["status"] == "undone"
    assert any("withdrew the first email" in t and "before Quinn Harper approved it" in t for t in activity(api, oid))
    late = api.post(f"/api/outbound/{out}/second-approval", headers=QUINN, json={"decision": "approve"})
    assert late.status_code == 409 and late.json()["code"] == "wrong_status"


# ---------- LinkedIn ----------
def test_demo_linkedin_note_fits_and_recording_it_is_a_first_contact(ws):
    app, api, oid = ws
    li = api.post(f"/api/opportunities/{oid}/linkedin/draft", headers=SARAH).json()
    assert li["note"].startswith("Hi ") and len(li["note"]) <= 200 and li["message"] and li["noteMax"] == 200
    assert li["search"].startswith("https://www.linkedin.com/search/results/people/?keywords=")
    assert api.get(f"/api/opportunities/{oid}").json()["outreach"]["linkedin"]["note"] == li["note"]
    assert api.put(f"/api/opportunities/{oid}/linkedin", headers=SARAH, json={"note": "x" * 201}).json()["code"] == "too_long"
    edited = api.put(f"/api/opportunities/{oid}/linkedin", headers=SARAH, json={"note": "Hi Dana, congratulations on the new foundry.", "message": li["message"]}).json()
    assert edited["note"] == "Hi Dana, congratulations on the new foundry." and edited["by"] == "Sarah Patel"
    sent = api.post(f"/api/opportunities/{oid}/linkedin/sent", headers=SARAH, json={"what": "note"}).json()
    assert list(sent["sent"]) == ["note"] and app.state.mail.outbox == []
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["stage"] == "contacted" and o["outreach"]["via"] == "LinkedIn connection request"
    api.post(f"/api/opportunities/{oid}/linkedin/sent", headers=SARAH, json={"what": "message"})  # later: noted, the stage stays
    assert api.get(f"/api/opportunities/{oid}").json()["stage"] == "contacted"
    assert any(t.startswith("LinkedIn message sent by Sarah Patel. The workspace did not send it.") for t in activity(api, oid))


def _live_linkedin(tmp_path, answer):
    fake = FakeAI(answer)
    api = live_app(tmp_path, fake)
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["company"] == "Halvorsen Precision Castings")
    return api, oid, fake


def test_live_linkedin_text_is_grounded_and_checked(tmp_path):
    good = {"note": "Hi Dana, I read about Halvorsen's second foundry. I work with leaders on changes like that and would be glad to connect.",
            "message": "Hi Dana,\n\nThanks for connecting. A second foundry raises questions about teams and timing. Would it help to compare notes?\n\nAlex"}
    api, oid, fake = _live_linkedin(tmp_path, good)
    r = api.post(f"/api/opportunities/{oid}/linkedin/draft", headers=ALEX)
    assert r.status_code == 200, r.text
    assert r.json()["note"] == good["note"]
    sent = json.loads(fake.requests[-1][2]["contents"][0]["parts"][0]["text"])
    assert sent["to"]["company"] == "Halvorsen Precision Castings" and sent["facts"]  # grounded on the brief's facts
    runs = api.get("/api/agent-runs?limit=5").json()
    assert any(x["agent"] == "Outreach agent" and x["prompt"] == "linkedin-1.0" for x in runs)


def test_live_linkedin_text_with_an_invented_number_is_refused(tmp_path):
    bad = {"note": "Hi Dana, companies like yours save 30 percent with us.", "message": "We cut costs by $2M for a client."}
    api, oid, _ = _live_linkedin(tmp_path, bad)
    r = api.post(f"/api/opportunities/{oid}/linkedin/draft", headers=ALEX)
    assert r.status_code == 502 and r.json()["code"] == "ai_check_failed"
    assert api.get(f"/api/opportunities/{oid}").json()["outreach"]["linkedin"]["note"] == ""
