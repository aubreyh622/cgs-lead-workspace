"""The mail connector: email goes out only when a person confirms exactly what they saw (permissions: test_toolkit_governance.py).
Gmail is checked against a fake Google (httpx.MockTransport); the workflow runs on the in-memory MockMail."""
import base64
import email
import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from api import autonomy as AU
from api import jobs as J
from api import models as m
from api.main import create_app
from api.settings import Settings
from api.toolkit.gmail_auth import save_env_value
from api.toolkit.mail import GmailMail, MailError, MockMail, new_text

ME = {"X-User": "Alex Morgan"}
BODY = "Hi Dana, I read that [[f1|Halvorsen opened a second foundry]]. Would a short call help?"


# ---------- Gmail over a fake Google ----------
def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class FakeGoogle:
    def __init__(self, token_status=200):
        self.token_status, self.calls = token_status, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        path = req.url.path
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(self.token_status, json={"access_token": "at", "expires_in": 3600})
        assert req.headers["Authorization"] == "Bearer at"
        if path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "alex@cgs.example"})
        if path.endswith("/drafts") and req.method == "POST":
            return httpx.Response(200, json={"id": "d1", "message": {"id": "m0", "threadId": "t1"}})
        if path.endswith("/drafts/gone"):
            return httpx.Response(404, json={})
        if path.endswith("/threads/t1"):
            return httpx.Response(200, json={"messages": [
                {"id": "m1", "internalDate": "1790000000000", "labelIds": ["SENT"],
                 "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": "Alex <alex@cgs.example>"},
                                                                   {"name": "Subject", "value": "Hello"}, {"name": "Message-ID", "value": "<a@x>"}],
                             "body": {"data": b64("Hi Dana")}}},
                {"id": "m2", "internalDate": "1790100000000", "labelIds": ["INBOX"],
                 "payload": {"mimeType": "multipart/alternative", "headers": [{"name": "From", "value": "Dana <dana@halvorsen.example>"}],
                             "parts": [{"mimeType": "text/plain", "body": {"data": b64("Yes, Tuesday works.\n\nOn Mon, Alex wrote:\n> Hi Dana")}},
                                       {"mimeType": "text/html", "body": {"data": b64("<p>Yes</p>")}}]}},
                {"id": "m3", "internalDate": "1790200000000", "labelIds": ["DRAFT"], "payload": {"headers": []}},
            ]})
        return httpx.Response(500, text="unexpected")


def gmail(google):
    return GmailMail("cid", "secret", "refresh", http=httpx.Client(transport=httpx.MockTransport(google)))


def test_gmail_creates_a_draft_with_the_right_message():
    google = FakeGoogle()
    res = gmail(google).create_draft("dana@halvorsen.example", "Hello", "Hi Dana", "t1", "<a@x>")
    assert res == {"draft_id": "d1", "thread_id": "t1"}
    sent = json.loads(google.calls[-1].content)["message"]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(sent["raw"]))
    assert (msg["To"], msg["Subject"], msg["In-Reply-To"], sent["threadId"]) == ("dana@halvorsen.example", "Hello", "<a@x>", "t1")
    assert msg["From"] is None  # Gmail fills in the sender with the account's display name
    assert msg.get_payload().strip() == "Hi Dana"
    assert sum(r.url.host == "oauth2.googleapis.com" for r in google.calls) == 1


def test_gmail_reads_a_thread_without_drafts_or_quoted_text():
    box = gmail(FakeGoogle())
    msgs = box.thread("t1")
    assert [(x["id"], x["ours"]) for x in msgs] == [("m1", True), ("m2", False)]
    assert msgs[1]["text"] == "Yes, Tuesday works." and msgs[0]["rfc_id"] == "<a@x>"
    assert box.draft_exists("gone") is False


def test_gmail_explains_sign_in_problems():
    with pytest.raises(MailError, match="not signed in"):
        GmailMail("", "", "").account()
    with pytest.raises(MailError, match="refused or has expired"):
        gmail(FakeGoogle(token_status=400)).account()


def test_quoted_history_is_dropped_from_a_reply():
    assert new_text("Thanks, not now.\n\n-----Original Message-----\nFrom: Alex") == "Thanks, not now."
    assert new_text("> old\nSure.") == "Sure."


def test_sign_in_helper_saves_the_token_without_touching_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("APP_MODE=live\n# GMAIL_REFRESH_TOKEN=comment\nGMAIL_REFRESH_TOKEN=old\n", encoding="utf-8")
    save_env_value(env, "GMAIL_REFRESH_TOKEN", "new")
    assert env.read_text(encoding="utf-8") == "APP_MODE=live\n# GMAIL_REFRESH_TOKEN=comment\nGMAIL_REFRESH_TOKEN=new\n"


def test_gmail_is_refused_in_demo_mode():
    with pytest.raises(ValueError, match="needs APP_MODE=live"):
        Settings(mail_provider="gmail")


# ---------- the workflow, on the mock mailbox ----------
@pytest.fixture
def ws(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "mail.sqlite3"))  # demo mode: the mailbox is a MockMail
    api = TestClient(app)
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["company"] == "Halvorsen Precision Castings")
    api.put(f"/api/opportunities/{oid}/outreach/draft", headers=ME, json={"subject": "Your second foundry", "body": BODY})
    return app, api, oid


def test_email_goes_out_only_as_the_person_saw_it(ws):
    app, api, oid = ws
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=ME, params={"to": "dana@halvorsen.example"}).json()
    assert p["body"] == "Hi Dana, I read that Halvorsen opened a second foundry. Would a short call help?"  # no citation markers
    send = {"to": "dana@halvorsen.example", "mode": "send", "seenHash": p["seenHash"]}
    assert api.post(f"/api/opportunities/{oid}/outreach/email", headers=ME, json={**send, "seenHash": "x"}).json()["code"] == "changed"
    assert api.post(f"/api/opportunities/{oid}/outreach/email", headers=ME, json={**send, "to": "dana"}).json()["code"] == "no_address"
    assert app.state.mail.outbox == []
    r = api.post(f"/api/opportunities/{oid}/outreach/email", headers=ME, json=send).json()
    assert r["mode"] == "send" and len(app.state.mail.outbox) == 1
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["stage"] == "contacted" and o["outreach"]["via"] == "Gmail" and o["contact"]["email"] == "dana@halvorsen.example"


def test_a_draft_cites_a_synthetic_document_so_it_cannot_go_out(ws):
    app, api, oid = ws
    api.put(f"/api/opportunities/{oid}/outreach/draft", headers=ME,
            json={"subject": "Hello", "body": BODY + " We did similar work [[" + next(
                p.id for p in __import__("api.knowledge", fromlist=["library"]).library().passages if p.synthetic) + "|for a client]]."})
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=ME, params={"to": "dana@halvorsen.example"}).json()
    assert "SYNTHETIC" in p["blocked"]
    r = api.post(f"/api/opportunities/{oid}/outreach/email", headers=ME, json={"to": p["to"], "mode": "draft", "seenHash": p["seenHash"]})
    assert r.json()["code"] == "synthetic" and app.state.mail.drafts == {}


def test_gmail_draft_then_sent_from_gmail_then_reply_reaches_the_inbox(ws):
    app, api, oid = ws
    box = app.state.mail
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=ME, params={"to": "dana@halvorsen.example"}).json()
    d = api.post(f"/api/opportunities/{oid}/outreach/email", headers=ME, json={"to": p["to"], "mode": "draft", "seenHash": p["seenHash"]}).json()
    assert d["draftId"] in box.drafts and box.outbox == []  # in Gmail drafts, not sent
    again = api.post(f"/api/opportunities/{oid}/outreach/email", headers=ME, json={"to": p["to"], "mode": "draft", "seenHash": p["seenHash"]})
    assert again.json()["code"] == "draft_waiting"
    assert api.get(f"/api/opportunities/{oid}").json()["outreach"]["gmailDraftWaiting"] is True

    box.send_draft(d["draftId"])  # the person presses Send in Gmail
    assert api.post("/api/mail/sync", headers=ME).json()["sent"] == 1
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["stage"] == "contacted" and o["outreach"]["gmailDraftWaiting"] is False

    box.deliver_reply(d["threadId"], "Dana <dana@halvorsen.example>", "Happy to talk on Tuesday.\n\nOn Mon, Alex wrote:\n> Hi Dana")
    assert api.post("/api/mail/sync", headers=ME).json()["replies"] == 1
    assert api.post("/api/mail/sync", headers=ME).json()["replies"] == 0  # the same reply is not added twice
    assert api.get(f"/api/opportunities/{oid}").json()["stage"] == "contacted"  # nothing recorded until a person applies it
    item = next(i for i in api.get("/api/inbox").json() if i["kind"] == "reply")
    assert item["body"] == "Happy to talk on Tuesday." and item["expiresOn"] is None
    api.post(f"/api/inbox/{item['id']}", headers=ME, json={"action": "apply", "payload_hash": item["payloadHash"]})
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["stage"] == "active" and o["outreach"]["replyText"] == "Happy to talk on Tuesday."


def test_a_follow_up_from_the_inbox_goes_into_the_same_thread(ws):
    app, api, oid = ws
    box = app.state.mail
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=ME, params={"to": "dana@halvorsen.example"}).json()
    first = api.post(f"/api/opportunities/{oid}/outreach/email", headers=ME, json={"to": p["to"], "mode": "send", "seenHash": p["seenHash"]}).json()
    with app.state.db.session() as s:
        o = s.get(m.Opportunity, oid)
        item = J.to_inbox(s, AU.agent_ctx(app, s).today, None, "draft", "Follow-up draft", "x",
                          {"subject": "Re: Your second foundry", "body": "Hi Dana, following up on my note. Would Tuesday work?",
                           "purpose": "followup", "facts": []}, opp=o, agent="Outreach agent")
        item_id = item.id
        s.commit()
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=ME, params={"itemId": item_id}).json()
    assert p["to"] == "dana@halvorsen.example" and p["inThread"] is True  # the address is remembered
    r = api.post(f"/api/opportunities/{oid}/outreach/email", headers=ME, json={"to": p["to"], "mode": "send", "seenHash": p["seenHash"], "itemId": item_id})
    assert r.status_code == 200, r.json()
    assert [x["subject"] for x in box.thread(first["threadId"])] == ["Your second foundry", "Re: Your second foundry"]
    assert next(i for i in api.get("/api/inbox", params={"status": "all"}).json() if i["id"] == item_id)["status"] == "applied"
    assert api.get(f"/api/opportunities/{oid}").json()["outreach"]["status"] == "followup"


def test_patrol_queues_the_daily_mail_check_and_the_toolkit_is_listed(ws):
    app, api, oid = ws
    with app.state.db.session() as s:
        assert "mail_sync" not in [j.kind for j in AU.patrol(AU.agent_ctx(app, s))]  # no email thread yet
        s.rollback()
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=ME, params={"to": "dana@halvorsen.example"}).json()
    api.post(f"/api/opportunities/{oid}/outreach/email", headers=ME, json={"to": p["to"], "mode": "send", "seenHash": p["seenHash"]})
    with app.state.db.session() as s:
        assert "mail_sync" in [j.kind for j in AU.patrol(AU.agent_ctx(app, s))]
    t = api.get("/api/toolkit", headers=ME).json()
    assert t["mail"] == {"provider": "mock", "ready": True}
    assert t["permissions"]["Outreach agent"]["mail"] == "off" and t["permissions"]["Research agent"]["mail"] == "forbidden"


def test_a_person_approves_an_inbox_draft_and_it_is_sent_at_once(ws):
    """Approve and send: the agent wrote it, the person approved exactly this version and this address, the workspace sent it."""
    app, api, oid = ws
    box = app.state.mail
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=ME, params={"to": "dana@halvorsen.example"}).json()
    api.post(f"/api/opportunities/{oid}/outreach/email", headers=ME, json={"to": p["to"], "mode": "send", "seenHash": p["seenHash"]})
    with app.state.db.session() as s:
        item = J.to_inbox(s, AU.agent_ctx(app, s).today, None, "draft", "Follow-up draft", "x",
                          {"subject": "Re: Your second foundry", "body": "Hi Dana, a short follow-up. Would Tuesday work?",
                           "purpose": "followup", "facts": []}, opp=s.get(m.Opportunity, oid), agent="Outreach agent")
        item_id, seen = item.id, item.payload_hash
        s.commit()
    act = lambda **kw: api.post(f"/api/inbox/{item_id}", headers=ME, json={"action": "send", **kw})
    assert act(payload_hash="old", to="dana@halvorsen.example").json()["code"] == "changed"
    assert act(payload_hash=seen).json()["code"] == "no_address"
    assert act(to="dana@halvorsen.example").json()["code"] == "no_hash"
    assert len(box.outbox) == 1  # only the first email so far
    r = act(payload_hash=seen, to="dana@halvorsen.example").json()
    assert r["status"] == "applied" and "sent to dana@halvorsen.example" in r["note"]
    assert [x["subject"] for x in box.outbox] == ["Your second foundry", "Re: Your second foundry"]
    assert act(payload_hash=seen, to="dana@halvorsen.example").json()["code"] == "wrong_status"  # never twice
    assert api.get(f"/api/opportunities/{oid}").json()["outreach"]["status"] == "followup"
