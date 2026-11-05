"""Undo: an approved email waits before it goes out, a Step 4 accept or reject holds its outside effects, and a skipped
candidate can be put back. Time is moved by editing the stored times, then the clock's functions are called."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from api import autonomy as AU
from api import jobs as J
from api import models as m
from api import undo as U
from api.main import create_app
from api.settings import Settings
from api.toolkit.mail import MailError

from tests.v2.test_crm import ME, accept, get, make, post
from tests.v2.test_toolkit_mail import BODY

TO = "dana@halvorsen.example"


@pytest.fixture
def ws(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "undo.sqlite3"))  # demo mode: the mailbox is a MockMail
    api = TestClient(app)
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["company"] == "Halvorsen Precision Castings")
    api.put(f"/api/opportunities/{oid}/outreach/draft", headers=ME, json={"subject": "Your second foundry", "body": BODY})
    return app, api, oid


def approve(api, oid, delay=10, ok=200):
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=ME, params={"to": TO}).json()
    return post(api, f"/api/opportunities/{oid}/outreach/email", {"to": TO, "mode": "send", "seenHash": p["seenHash"], "delay": delay}, ok=ok)


def pass_time(app, out_id=None, opp_id=None, by=timedelta(seconds=11)):
    with app.state.db.session() as s:
        if out_id:
            row = s.get(m.OutboundEmail, out_id)
            row.send_at = U.aware(row.send_at) - by
        if opp_id:
            o = s.get(m.Opportunity, opp_id)
            o.undo_until = U.aware(o.undo_until) - by
        s.commit()


def texts(api, oid):
    return [a["text"] for a in get(api, oid)["activity"]]


def test_an_approved_email_waits_and_can_be_undone(ws):
    app, api, oid = ws
    box = app.state.mail
    r = approve(api, oid)
    assert r["mode"] == "queued" and r["seconds"] == 10 and box.outbox == []
    o = get(api, oid)
    assert o["stage"] == "identified" and o["outreach"]["waiting"]["status"] == "queued" and o["outreach"]["waiting"]["to"] == TO
    assert approve(api, oid, ok=409)["code"] == "queued"  # never twice
    edit = api.put(f"/api/opportunities/{oid}/outreach/draft", headers=ME, json={"subject": "Changed", "body": "Changed"})
    assert edit.status_code == 409 and edit.json()["code"] == "queued"  # what was approved cannot change under it
    assert post(api, f"/api/opportunities/{oid}/outreach/sent", {"via": "Phone"}, ok=409)["code"] == "queued"
    assert post(api, f"/api/outbound/{r['id']}/undo")["status"] == "undone"
    pass_time(app, r["id"])
    assert U.send_due(app) == 0 and box.outbox == []
    o = get(api, oid)
    assert o["stage"] == "identified" and o["outreach"]["waiting"] is None and o["outreach"]["subject"] == "Your second foundry"
    assert any("undid the first email" in t for t in texts(api, oid))
    # approved again, and this time the window passes: it goes out
    r = approve(api, oid)
    pass_time(app, r["id"])
    assert U.send_due(app) == 1 and [x["subject"] for x in box.outbox] == ["Your second foundry"]
    o = get(api, oid)
    assert o["stage"] == "contacted" and o["outreach"]["via"] == "Gmail" and o["outreach"]["waiting"] is None
    assert any("sent through Gmail by Alex Morgan" in t for t in texts(api, oid))
    assert post(api, f"/api/outbound/{r['id']}/undo", ok=409)["code"] == "too_late"
    assert U.send_due(app) == 0 and len(box.outbox) == 1


def test_send_now_does_not_wait_and_no_delay_sends_at_once(ws):
    app, api, oid = ws
    r = approve(api, oid)
    sent = post(api, f"/api/outbound/{r['id']}/send-now")
    assert sent["mode"] == "send" and len(app.state.mail.outbox) == 1 and get(api, oid)["stage"] == "contacted"
    assert U.send_due(app) == 0 and post(api, f"/api/outbound/{r['id']}/send-now", ok=409)["code"] == "wrong_status"


def test_an_email_held_while_the_workspace_was_stopped_is_not_sent(ws):
    app, api, oid = ws
    r = approve(api, oid)
    pass_time(app, r["id"], by=timedelta(minutes=30))
    assert U.send_due(app) == 0 and app.state.mail.outbox == []
    o = get(api, oid)
    assert o["stage"] == "identified" and o["outreach"]["waiting"]["status"] == "expired"
    assert any("workspace was stopped before it went out" in t for t in texts(api, oid))
    assert approve(api, oid)["mode"] == "queued"  # the person approves it again


def test_a_send_that_fails_is_recorded_and_the_draft_stays(ws, monkeypatch):
    app, api, oid = ws
    r = approve(api, oid)

    def broken(*a, **k):
        raise MailError("Gmail is not reachable.")
    monkeypatch.setattr(app.state.mail, "send", broken)
    pass_time(app, r["id"])
    assert U.send_due(app) == 0
    o = get(api, oid)
    assert o["stage"] == "identified" and o["outreach"]["waiting"]["status"] == "failed"
    assert "Gmail is not reachable" in o["outreach"]["waiting"]["error"]
    assert any("was not sent: Gmail is not reachable" in t for t in texts(api, oid))


def test_an_inbox_draft_waits_too_and_is_applied_when_it_goes_out(ws):
    app, api, oid = ws
    box = app.state.mail
    approve(api, oid, delay=0)  # the first email, sent at once as before
    with app.state.db.session() as s:
        item = J.to_inbox(s, AU.agent_ctx(app, s).today, None, "draft", "Follow-up draft", "x",
                          {"subject": "Re: Your second foundry", "body": "Hi Dana, a short follow-up.", "purpose": "followup", "facts": []},
                          opp=s.get(m.Opportunity, oid), agent="Outreach agent")
        item_id, seen = item.id, item.payload_hash
        s.commit()
    r = post(api, f"/api/inbox/{item_id}", {"action": "send", "payload_hash": seen, "to": TO, "delay": 10})
    assert r["status"] == "queued" and len(box.outbox) == 1
    with app.state.db.session() as s:
        assert s.get(m.InboxItem, item_id).status == "new"  # applied only once it is sent
    pass_time(app, r["sent"]["id"])
    assert U.send_due(app) == 1 and box.outbox[-1]["subject"] == "Re: Your second foundry"
    with app.state.db.session() as s:
        assert s.get(m.InboxItem, item_id).status == "applied"
    assert get(api, oid)["outreach"]["status"] == "followup"


ACCEPT = {"decision": "accept", "rating": 4, "owner": "Alex Morgan", "next_action": "Send outreach", "due": "2026-10-29",
          "duplicates_checked": True, "scope": "theme", "undo_seconds": 10}


def test_an_accept_can_be_undone_before_anything_reaches_the_crm_or_the_agents(tmp_path):
    api = make(tmp_path)
    app, crm = api.app, api.app.state.crm
    post(api, "/api/candidates/c1/pick")
    rev = get(api, "corvane")["revision"]
    mine = lambda: [x for x in crm.leads.values() if x["workspace_id"] == "corvane"]
    r = post(api, "/api/opportunities/corvane/step4", {**ACCEPT, "revision": rev})
    assert r["undo"]["decision"] == "Accepted" and 0 < r["undo"]["seconds"] <= 10
    o = get(api, "corvane")
    assert o["stage"] == "identified" and o["crmSync"] == "sync_pending" and mine() == []  # held back
    assert o["undo"]["decision"] == "Accepted"
    with app.state.db.session() as s:
        job = s.query(m.AgentJob).filter_by(kind="first_draft", opportunity_id="corvane").one()
        job_id = job.id
    assert job_id not in AU.queued(app)  # the outreach agent waits for the window
    post(api, "/api/crm/sync", {})  # a sync now still leaves it alone
    assert get(api, "corvane")["crmSync"] == "sync_pending" and mine() == []
    post(api, "/api/opportunities/corvane/step4/undo")
    o = get(api, "corvane")
    assert o["stage"] == "review" and o["owner"] in (None, "") and o["crmSync"] == "local_saved" and o["undo"] is None and mine() == []
    assert [d["decision"] for d in o["decisions"]][-2:] == ["Accepted", "Undone"]
    assert not any(f["subject"] == "Corvane Industrial" and f["step"] == 4 for f in api.get("/api/feedback", headers=ME).json())
    with app.state.db.session() as s:
        assert s.get(m.AgentJob, job_id).status == "skipped"
    # accepted again; the window passes: the lead goes to the CRM and the agent may start
    post(api, "/api/opportunities/corvane/step4", {**ACCEPT, "revision": o["revision"]})
    pass_time(app, opp_id="corvane")
    assert U.release_due(app) == ["corvane"]
    o = get(api, "corvane")
    assert o["undo"] is None and o["crmSync"] == "synced" and len(mine()) == 1
    assert post(api, "/api/opportunities/corvane/step4/undo", ok=409)["code"] == "too_late"
    with app.state.db.session() as s:
        new_job = s.query(m.AgentJob).filter_by(kind="first_draft", opportunity_id="corvane", status="queued").one().id
    assert new_job in AU.queued(app)


def test_a_reject_can_be_undone_and_its_exclusion_is_withdrawn(tmp_path):
    api = make(tmp_path)
    post(api, "/api/candidates/c1/pick")
    rev = get(api, "corvane")["revision"]
    post(api, "/api/opportunities/corvane/step4", {"decision": "reject", "reason": "Existing relationship or already in progress",
                                                    "scope": "theme", "suggest_exclusion": True, "undo_seconds": 10, "revision": rev})
    [p] = [p for p in api.get("/api/direction").json()["proposals"] if p["target"] == "Corvane Industrial"]
    assert p["status"] == "pending"
    post(api, "/api/opportunities/corvane/step4/undo")
    assert get(api, "corvane")["stage"] == "review"
    [p] = [p for p in api.get("/api/direction").json()["proposals"] if p["target"] == "Corvane Industrial"]
    assert p["status"] == "withdrawn"


def test_no_undo_once_something_else_changed_or_without_a_window(tmp_path):
    api = make(tmp_path)
    post(api, "/api/candidates/c1/pick")
    post(api, "/api/opportunities/corvane/step4", {**ACCEPT, "revision": get(api, "corvane")["revision"]})
    post(api, "/api/opportunities/corvane/contact", {"name": "Elena Fischer", "title": "COO", "note": "Company website"})
    assert post(api, "/api/opportunities/corvane/step4/undo", ok=409)["code"] == "changed"
    post(api, "/api/candidates/c2/pick")
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["stage"] == "review" and o["id"] != "corvane")
    accept(api, oid)  # no undo_seconds: at once, as before
    assert get(api, oid)["undo"] is None and post(api, f"/api/opportunities/{oid}/step4/undo", ok=409)["code"] == "too_late"


def test_a_skipped_candidate_can_be_put_back_until_the_direction_changes(tmp_path):
    api = make(tmp_path)
    skip = {"reason": "Competitor or company we do not work with", "scope": "cgs"}
    post(api, "/api/candidates/c8/skip", skip)
    post(api, "/api/candidates/c8/unskip")
    cands = {c["id"]: c for c in api.get("/api/candidates", headers=ME).json()}
    assert cands["c8"]["status"] == "new"
    props = [p for p in api.get("/api/direction").json()["proposals"] if p["target"] == "Northgate Chemical"]
    assert [p["status"] for p in props] == ["withdrawn"]
    assert not any(f["subject"] == "Northgate Chemical" for f in api.get("/api/feedback", headers=ME).json())
    assert post(api, "/api/candidates/c8/unskip", ok=409)["code"] == "wrong_status"
    # skipped again, and this time the exclusion is approved: the skip has changed the direction
    post(api, "/api/candidates/c8/skip", skip)
    [p] = [p for p in api.get("/api/direction").json()["proposals"] if p["target"] == "Northgate Chemical" and p["status"] == "pending"]
    post(api, f"/api/direction/proposals/{p['id']}/approve")
    assert post(api, "/api/candidates/c8/unskip", ok=409)["code"] == "direction_changed"


def test_the_clock_sends_on_its_own(ws):
    import time
    app, api, oid = ws
    approve(api, oid, delay=1)
    clock = U.Clock(app, tick=0.1).start()
    try:
        for _ in range(50):
            if app.state.mail.outbox:
                break
            time.sleep(0.1)
    finally:
        clock.stop.set()
    assert len(app.state.mail.outbox) == 1 and get(api, oid)["stage"] == "contacted"
