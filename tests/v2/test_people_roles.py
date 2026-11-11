"""People and roles (client meeting 4): admins vs everyone else, only admins act as someone else, admins change
the scoring criteria and decide the team's research direction, and each consultant's email goes out from their own
Gmail (api/people.py, api/scoring.py, api/services.py sender_for)."""
import pytest
from fastapi.testclient import TestClient

import backend.rules as R
from api.main import create_app
from api.settings import Settings
from api.toolkit.mail import MockMail, person_key
from tests.v2.test_toolkit_mail import BODY

ALEX = {"X-User": "Alex Morgan"}
SARAH = {"X-User": "Sarah Patel"}
ALEX_AS_SARAH = {"X-User": "Alex Morgan", "X-Act-As": "Sarah Patel"}
SARAH_AS_ALEX = {"X-User": "Sarah Patel", "X-Act-As": "Alex Morgan"}
TO = "dana@halvorsen.example"


@pytest.fixture(autouse=True)
def builtin_rules():
    yield
    R.apply(None)  # the rules engine is shared by every test in this process


def make(tmp_path, **settings):
    app = create_app(Settings(db_path=tmp_path / "people.sqlite3", **settings))
    return app, TestClient(app)


def activity(api, oid):
    return [a["text"] for a in api.get(f"/api/opportunities/{oid}").json()["activity"]]


def halvorsen(api, who=ALEX):
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["company"] == "Halvorsen Precision Castings")
    api.put(f"/api/opportunities/{oid}/outreach/draft", headers=who, json={"subject": "Your second foundry", "body": BODY})
    return oid


def send(api, oid, who, ok=200, item_id=None):
    params = {"to": TO, **({"itemId": item_id} if item_id else {})}
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=who, params=params).json()
    r = api.post(f"/api/opportunities/{oid}/outreach/email", headers=who,
                 json={"to": TO, "mode": "send", "seenHash": p["seenHash"], **({"itemId": item_id} if item_id else {})})
    assert r.status_code == ok, r.text
    return p, r.json()


# ---------- roles ----------
def test_without_admins_everyone_may_do_admin_work_as_before(tmp_path):
    app, api = make(tmp_path)
    me = api.get("/api/me", headers=SARAH).json()
    assert me["name"] == "Sarah Patel" and me["admin"] and me["everyoneIsAdmin"]
    assert api.get("/api/people", headers=SARAH_AS_ALEX).json()["actingAs"] == "Alex Morgan"


def test_only_an_admin_acts_as_someone_else_and_the_record_names_both(tmp_path):
    app, api = make(tmp_path, admins=("Alex Morgan",))
    assert api.get("/api/me", headers=SARAH).json()["admin"] is False and api.get("/api/me", headers=ALEX).json()["admin"] is True
    r = api.get("/api/people", headers=SARAH_AS_ALEX)
    assert r.status_code == 403 and r.json()["code"] == "not_admin" and "Only an admin can act as someone else" in r.json()["error"]
    assert api.get("/api/people", headers={"X-User": "Alex Morgan", "X-Act-As": "Nobody"}).json()["code"] == "unknown_user"
    oid = halvorsen(api, ALEX_AS_SARAH)
    send(api, oid, ALEX_AS_SARAH)
    assert any("by Alex Morgan as Sarah Patel" in t for t in activity(api, oid))
    assert app.state.mail.outbox[-1]["to"] == TO


def test_an_admin_names_another_admin_and_the_last_admin_stays(tmp_path):
    app, api = make(tmp_path, admins=("Alex Morgan",))
    assert api.put("/api/people/Sarah Patel", headers=SARAH, json={"admin": True}).json()["code"] == "not_admin"
    r = api.put("/api/people/Sarah Patel", headers=ALEX, json={"admin": True}).json()
    assert r["changed"] == ["made Sarah Patel an admin"] and r["admins"] == ["Alex Morgan", "Sarah Patel"] and r["source"] == "workspace"
    assert api.get("/api/people", headers=SARAH_AS_ALEX).status_code == 200  # Sarah may act as others now
    assert api.put("/api/people/Alex Morgan", headers=SARAH, json={"admin": False}).json()["admins"] == ["Sarah Patel"]
    last = api.put("/api/people/Sarah Patel", headers=SARAH, json={"admin": False})
    assert last.status_code == 409 and last.json()["code"] == "last_admin"
    # the database list now decides, even after a restart with the old CGS_ADMINS
    app2 = create_app(Settings(db_path=tmp_path / "people.sqlite3", admins=("Alex Morgan",)))
    assert TestClient(app2).get("/api/me", headers=ALEX).json()["admin"] is False


def test_an_admin_sets_the_address_a_person_sends_from(tmp_path):
    app, api = make(tmp_path, admins=("Alex Morgan",))
    assert api.put("/api/people/Sarah Patel", headers=ALEX, json={"email": "not an address"}).json()["code"] == "bad_email"
    api.put("/api/people/Sarah Patel", headers=ALEX, json={"email": "Sarah.Patel@cgs.example"})
    people = {p["name"]: p for p in api.get("/api/people", headers=ALEX).json()["people"]}
    assert people["Sarah Patel"]["email"] == "sarah.patel@cgs.example" and people["Sarah Patel"]["mailbox"] == "shared"
    oid = halvorsen(api, SARAH)
    p, _ = send(api, oid, SARAH)
    assert p["from"]["kind"] == "shared" and "sarah.patel@cgs.example" in p["from"]["note"]
    assert app.state.mail.outbox[-1]["from"] == "sarah.patel@cgs.example"  # the mock keeps the From; Gmail keeps it for an alias


# ---------- the scoring criteria ----------
CASE = {"scores": {"B": 4, "A": 4, "N": 4, "T": 4, "H": 3, "E": 4},
        "conditions": {k: True for k in ("problem", "fit", "external", "next", "commercial", "conflict")},
        "revenue": {"low": 500000, "high": 700000}}


def test_only_an_admin_changes_the_scoring_criteria_and_every_score_follows(tmp_path):
    app, api = make(tmp_path, admins=("Alex Morgan",))
    assert api.post("/api/rules/calculate", json=CASE).json()["readiness"] == "Ready"  # Q 78 under the built-in criteria
    cur = api.get("/api/scoring", headers=SARAH).json()
    assert cur["canChange"] is False and cur["current"]["weights"] == R.DEFAULT["weights"]
    new = {"weights": {"B": 30, "A": 20, "N": 25, "T": 15, "H": 0, "E": 10}, "floors": {"B": 3, "N": 3},
           "ready": {"coverage": 80, "q": 85}, "bands": {"High": 80, "Medium": 60}, "reason": "Budget matters more"}
    assert api.put("/api/scoring", headers=SARAH, json=new).json()["code"] == "not_admin"
    bad = api.put("/api/scoring", headers=ALEX, json={**new, "weights": {**new["weights"], "B": 40}}).json()
    assert bad["code"] == "invalid" and "add up to 100; these add up to 110" in bad["error"]
    assert api.put("/api/scoring", headers=ALEX, json={**new, "reason": ""}).json()["code"] == "no_reason"
    saved = api.put("/api/scoring", headers=ALEX, json=new).json()
    assert saved["current"]["version"] == "cgs-1" and saved["changed"]["by"] == "Alex Morgan" and saved["history"][0]["by"] == "Built in"
    rules = api.get("/api/rules").json()
    assert rules["version"] == "cgs-1" and rules["weights"]["B"] == 30 and rules["floors"] == {"B": 3, "N": 3} and rules["ready"]["q"] == 85
    r = api.post("/api/rules/calculate", json=CASE).json()
    assert r["readiness"] == "More evidence needed" and r["ruleVersion"] == "cgs-1"
    assert api.put("/api/scoring", headers=ALEX, json=new).json()["code"] == "unchanged"
    back = api.put("/api/scoring", headers=ALEX, json={"builtIn": True, "reason": "Back to the start"}).json()
    assert back["current"]["weights"] == R.DEFAULT["weights"] and back["current"]["version"] == "cgs-2-built-in"
    assert api.post("/api/rules/calculate", json=CASE).json()["readiness"] == "Ready"


def test_a_new_server_on_the_same_database_uses_the_saved_criteria(tmp_path):
    app, api = make(tmp_path)
    api.put("/api/scoring", headers=ALEX, json={"weights": {"B": 20, "A": 20, "N": 20, "T": 20, "H": 10, "E": 10}, "floors": {},
                                                 "ready": {"coverage": 70, "q": 60}, "bands": {"High": 70, "Medium": 50}, "reason": "Even weights"})
    R.apply(None)  # another server instance, still on the built-in criteria
    create_app(Settings(db_path=tmp_path / "people.sqlite3"))
    assert R.version() == "cgs-1" and R.WEIGHTS["B"] == 20
    R.apply(None)
    assert api.get("/api/rules").json()["version"] == "cgs-1"  # every request checks the stored version


# ---------- the research direction ----------
def test_only_an_admin_decides_a_change_to_the_teams_direction(tmp_path):
    app, api = make(tmp_path, admins=("Alex Morgan",))
    signals = api.get("/api/direction").json()["current"]["signals"]
    body = {"field": "signals", "scope": "theme", "value": signals + ["New plant announced"], "rationale": "CGS works on plant ramp-ups"}
    pid = api.post("/api/direction/proposals", headers=SARAH, json=body).json()["id"]  # anyone may propose
    r = api.post(f"/api/direction/proposals/{pid}/approve", headers=SARAH)
    assert r.status_code == 403 and r.json()["code"] == "not_admin"
    assert api.post(f"/api/direction/proposals/{pid}/approve", headers=ALEX).json()["version"]
    assert "New plant announced" in api.get("/api/direction").json()["current"]["signals"]
    mine = api.post("/api/direction/proposals", headers=SARAH, json={**body, "scope": "consultant", "value": signals + ["Mine"]}).json()["id"]
    assert api.post(f"/api/direction/proposals/{mine}/approve", headers=SARAH).status_code == 200  # her own searches: her call


# ---------- each consultant's own Gmail ----------
def test_email_goes_out_from_the_senders_own_gmail_and_the_conversation_stays_there(tmp_path):
    app, api = make(tmp_path, admins=("Alex Morgan",))
    own = MockMail(me="sarah@cgs.example")
    app.state.own_mail = {"Sarah Patel": own}
    assert {p["name"]: p["mailbox"] for p in api.get("/api/people", headers=ALEX).json()["people"]}["Sarah Patel"] == "own"
    oid = halvorsen(api, SARAH)
    p, first = send(api, oid, SARAH)
    assert p["from"] == {"person": "Sarah Patel", "kind": "own", "address": "sarah@cgs.example",
                         "note": "From Sarah Patel's own Gmail (sarah@cgs.example)."}
    assert len(own.outbox) == 1 and app.state.mail.outbox == []  # not the shared mailbox
    assert any("from Sarah Patel's own Gmail" in t for t in activity(api, oid))

    # the follow-up stays in Sarah's mailbox: Alex cannot send it as himself, but may act as Sarah (he is an admin)
    from api import autonomy as AU, jobs as J, models as m
    with app.state.db.session() as s:
        it = J.to_inbox(s, AU.agent_ctx(app, s).today, None, "draft", "Follow-up draft", "x",
                        {"subject": "Re: Your second foundry", "body": "Hi Dana, following up on my note. Would Tuesday work?",
                         "purpose": "followup", "facts": []}, opp=s.get(m.Opportunity, oid), agent="Outreach agent")
        item_id = it.id
        s.commit()
    pa = api.get(f"/api/opportunities/{oid}/outreach/email", headers=ALEX, params={"itemId": item_id}).json()
    assert "in Sarah Patel's own Gmail" in pa["blocked"]
    _, r = send(api, oid, ALEX, ok=409, item_id=item_id)
    assert r["code"] == "wrong_sender"
    send(api, oid, ALEX_AS_SARAH, item_id=item_id)
    assert [x["subject"] for x in own.thread(first["threadId"])] == ["Your second foundry", "Re: Your second foundry"]

    # the reply arrives in Sarah's mailbox and the daily check finds it there
    own.deliver_reply(first["threadId"], "Dana <dana@halvorsen.example>", "Tuesday works.")
    assert api.post("/api/mail/sync", headers=ALEX).json()["replies"] == 1
    app.state.own_mail = {}  # Sarah's Gmail was disconnected
    res = api.post("/api/mail/sync", headers=ALEX).json()
    assert any("Sarah Patel's own Gmail, which is not signed in" in e for e in res["errors"])


def test_a_persons_gmail_sign_in_has_its_own_key():
    assert person_key("Sarah Patel") == "SARAH_PATEL" and person_key("  Quinn  Harper-Ng ") == "QUINN_HARPER_NG"
