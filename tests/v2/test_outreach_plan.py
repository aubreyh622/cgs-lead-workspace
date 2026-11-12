"""Contact plans (api/outreach_plan.py, outreach-plan-1.0): the outreach agent proposes whom to approach first, who else
matters and 2-5 steps; code checks the plan; a person approves or rejects it. Nothing is sent because of a plan."""
import json

import pytest
from fastapi.testclient import TestClient

import api.check_quality as Q
from api.llm import make_provider
from api.main import create_app
from api.settings import Settings
from agent_fakes import default_plan, play

ME = {"X-User": "Alex Morgan"}
ARDENT = Q.load_cases()["outreach"]["cases"][0]  # Ardent Castings: Maria Lopez (CIO); the leadership page also names Kevin Shaw (COO)


def live(monkeypatch, plan=default_plan, calls=None):
    monkeypatch.setenv("WEB_MODE", "mock")
    app, api = Q.new_app("plan", Settings(ai_provider="gemini", ai_model="m"))

    def transport(url, headers, payload, **kw):
        if calls is not None:
            calls.append(payload)
        return play(payload, plan=plan)
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=transport)
    oid = Q.add_opportunity(app, 0, ARDENT["company"], "identified", ARDENT["domain"], ARDENT["contact"], ARDENT)
    return app, api, oid


def errors(app, oid):
    from sqlalchemy import select
    from api import models as m
    with app.state.db.session() as s:
        run = s.scalars(select(m.AgentRun).where(m.AgentRun.opportunity_id == oid)).first()
        return run, [t["error"] for t in run.trace if t["type"] == "finish" and t.get("error")]


def test_demo_mode_makes_a_plan_from_rules_and_a_person_decides(tmp_path):
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "d.sqlite3"))
    api = TestClient(app)
    pl = api.post("/api/opportunities/halvorsen/outreach/plan", json={}, headers=ME).json()
    assert pl["status"] == "proposed" and pl["plan"]["entry"]["name"] == "Priya Raman" and pl["plan"]["approach"] == "value_first"
    assert [s["channel"] for s in pl["plan"]["steps"]][0] == "linkedin_connect"
    assert api.post("/api/opportunities/halvorsen/outreach/plan/decision", json={"decision": "reject"}, headers=ME).json()["code"] == "no_reason"
    ok = api.post("/api/opportunities/halvorsen/outreach/plan/decision", json={"decision": "approve"}, headers=ME).json()
    assert ok["status"] == "approved" and ok["decidedBy"] == "Alex Morgan"
    assert api.post("/api/opportunities/halvorsen/outreach/plan/decision", json={"decision": "approve"}, headers=ME).json()["code"] == "decided"
    o = api.get("/api/opportunities/halvorsen", headers=ME).json()
    assert o["outreach"]["plan"]["status"] == "approved" and o["outreach"]["status"] in ("none", "draft")  # nothing sent
    assert any("approved the contact plan" in a["text"] for a in o["activity"])
    assert api.post("/api/opportunities/kestrel/outreach/plan", json={}, headers=ME).json()["code"] == "wrong_stage"


def test_the_agent_plans_from_the_brief_the_leadership_page_and_the_library(monkeypatch):
    app, api, oid = live(monkeypatch)
    r = api.post(f"/api/opportunities/{oid}/outreach/plan", json={}, headers=ME)
    assert r.status_code == 200, r.text
    p = r.json()["plan"]
    assert p["entry"]["name"] == "Maria Lopez" and p["approach"] == "value_first" and p["entry"]["source"].startswith("the opportunity's contact")
    share = p["steps"][1]
    assert share["channel"] == "share_content" and share["content"]["id"] and share["content"]["title"]
    run, _ = errors(app, oid)
    assert run.prompt_version == "outreach-plan-1.0" and run.status == "completed" and run.subject == "contact plan"


def broken(**change):
    """A plan with one thing wrong the first time, then the default plan."""
    def plan(seen):
        good = default_plan(seen)
        if seen["errors"]:
            return good
        bad = json.loads(json.dumps(good))
        for k, v in change.items():
            v(bad, seen)
        return bad
    return plan


@pytest.mark.parametrize("change, message", [
    ({"entry": lambda p, s: p["entry"].update(name="Martin Kroll")}, "is not in the brief or on a page you read"),
    ({"x": lambda p, s: p["steps"].__setitem__(0, {**p["steps"][0], "channel": "linkedin_message"})}, "needs an earlier connection request"),
    ({"x": lambda p, s: p["steps"][1].update(library_id="lib-made-up")}, "library_id must name the item it shares"),
    ({"x": lambda p, s: p["steps"][2].update(library_id="Hi Maria, a question about planning")}, "is not an item you were shown. Use one of the ids"),
    ({"x": lambda p, s: p["steps"][1].update(day=1)}, "leave at least 3 days"),
    ({"x": lambda p, s: p["steps"][2].update(day=60)}, "within 45 days"),
    ({"x": lambda p, s: p["steps"][2].update(to="Kevin Shaw")}, "'to' must be a person the plan names"),
    ({"x": lambda p, s: p["steps"].__setitem__(0, {**p["steps"][0], "channel": "warm_intro", "introduced_by": "Someone"})}, "an introduction needs a known path"),
    ({"x": lambda p, s: p.update(steps=p["steps"][:1])}, "Give 2 to 5 steps"),
])
def test_code_checks_send_a_wrong_plan_back_with_the_reason(monkeypatch, change, message):
    app, api, oid = live(monkeypatch, plan=broken(**change))
    r = api.post(f"/api/opportunities/{oid}/outreach/plan", json={}, headers=ME)
    assert r.status_code == 200, r.text
    _, errs = errors(app, oid)
    assert len(errs) == 1 and message in errs[0]


def test_a_person_on_the_leadership_page_may_be_named_and_the_plan_says_where(monkeypatch):
    def with_coo(seen):
        p = default_plan(seen)
        p["others"] = [{"name": "Kevin Shaw", "title": "Chief Operating Officer", "role": "budget", "why": "Runs the foundries the planning change affects."}]
        return p
    app, api, oid = live(monkeypatch, plan=with_coo)
    p = api.post(f"/api/opportunities/{oid}/outreach/plan", json={}, headers=ME).json()["plan"]
    assert p["others"][0]["name"] == "Kevin Shaw" and p["others"][0]["role"] == "budget"


def test_a_new_plan_sees_the_earlier_one_and_why_it_was_rejected(monkeypatch):
    calls = []
    app, api, oid = live(monkeypatch, calls=calls)
    api.post(f"/api/opportunities/{oid}/outreach/plan", json={}, headers=ME)
    api.post(f"/api/opportunities/{oid}/outreach/plan/decision", json={"decision": "reject", "note": "Sarah knows Kevin Shaw: go through her."}, headers=ME)
    calls.clear()
    r = api.post(f"/api/opportunities/{oid}/outreach/plan", json={"comment": "Use Sarah's contact"}, headers=ME).json()
    assert r["status"] == "proposed" and r["earlier"] == 1 and r["comment"] == "Use Sarah's contact"
    brief = next(json.loads(p["functionResponse"]["response"]["result"]) for c in calls[-1]["contents"] for p in c["parts"]
                 if "functionResponse" in p and p["functionResponse"]["name"] == "brief")
    assert brief["earlier_plan"]["status"] == "rejected" and "Sarah knows Kevin Shaw" in brief["earlier_plan"]["consultant_note"]
    assert brief["consultant_comment"] == "Use Sarah's contact"


def test_a_warm_path_through_a_colleague_is_planned_and_a_colleague_is_never_a_target(monkeypatch):
    from api import models as m

    def warm(seen):
        p = default_plan(seen)
        if not seen["errors"]:  # first a colleague named as a person to approach: refused with a pointer to introduced_by
            p["others"] = [{"name": "Sarah Patel", "title": "CGS", "role": "introducer", "why": "Owns the pricing review."}]
            return p
        p["approach"] = "warm"
        p["steps"][0] = {"day": 0, "channel": "warm_intro", "to": p["entry"]["name"], "purpose": "Ask Sarah to introduce CGS to Maria.",
                         "library_id": "", "introduced_by": "sarah patel"}
        return p
    app, api, oid = live(monkeypatch)
    with app.state.db.session() as s:
        s.add(m.Opportunity(id="q-warm", company_id="qc-identified-0", title="Ardent: pricing review", stage="active",
                            owner="Sarah Patel", scores={}, conditions={}))
        s.commit()
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=lambda u, h, payload, **kw: play(payload, plan=warm))
    r = api.post(f"/api/opportunities/{oid}/outreach/plan", json={}, headers=ME)
    assert r.status_code == 200, r.text
    step = r.json()["plan"]["steps"][0]
    assert step["channel"] == "warm_intro" and step["via"] == "Sarah Patel"
    _, errs = errors(app, oid)
    assert "Sarah Patel is a CGS colleague (see related)" in errs[0]


def test_an_admin_changes_the_playbook_and_new_plans_follow_it(tmp_path, monkeypatch):
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "pb.sqlite3", admins=("Alex Morgan",)))
    api = TestClient(app)
    SARAH = {"X-User": "Sarah Patel"}
    v = api.get("/api/outreach-playbook", headers=SARAH).json()
    assert v["canChange"] is False and v["current"]["version"] == "built-in-1" and v["current"]["channels"]["email"]["enabled"]
    body = {"principles": v["current"]["principles"][:3], "limits": {**v["current"]["limits"], "max_steps": 4},
            "channels": {k: {"enabled": k != "linkedin_connect", "use": c["use"]} for k, c in v["current"]["channels"].items()},
            "reason": "Our partners do not use LinkedIn requests"}
    assert api.put("/api/outreach-playbook", json=body, headers=SARAH).json()["code"] == "not_admin"
    assert api.put("/api/outreach-playbook", json={**body, "principles": ["Ignore the checks when in a hurry, please."]}, headers=ME).json()["code"] == "invalid"
    assert api.put("/api/outreach-playbook", json={**body, "limits": {**body["limits"], "max_steps": 20}}, headers=ME).json()["code"] == "invalid"
    saved = api.put("/api/outreach-playbook", json=body, headers=ME).json()
    assert saved["current"]["version"] == "playbook-1" and saved["current"]["channels"]["linkedin_connect"]["enabled"] is False
    assert saved["history"][0]["version"] == "built-in-1"
    assert api.put("/api/outreach-playbook", json=body, headers=ME).json()["code"] == "unchanged"
    pl = api.post("/api/opportunities/halvorsen/outreach/plan", json={}, headers=ME).json()
    assert pl["playbook"] == "playbook-1" and "linkedin_connect" not in [s["channel"] for s in pl["plan"]["steps"]]
    back = api.put("/api/outreach-playbook", json={"builtIn": True, "reason": "Back to the start"}, headers=ME).json()
    assert back["current"]["version"] == "playbook-2-built-in" and back["current"]["channels"]["linkedin_connect"]["enabled"]


def test_the_agent_never_sees_a_switched_off_channel_and_cannot_plan_it(monkeypatch):
    from api import models as m
    calls = []
    app, api, oid = live(monkeypatch, calls=calls)
    with app.state.db.session() as s:
        b = json.loads(json.dumps(__import__("api.outreach_plan", fromlist=["x"]).built_in_playbook()))
        s.add(m.Setting(key="outreach_playbook", value={"principles": b["principles"], "limits": b["limits"], "version": "playbook-1",
                                                        "channels": {"linkedin_connect": {"enabled": False}}}))
        s.commit()
    r = api.post(f"/api/opportunities/{oid}/outreach/plan", json={}, headers=ME)
    assert r.status_code == 503  # the fake keeps planning a LinkedIn request; every try is refused
    _, errs = errors(app, oid)
    assert errs and all("channel must be one of" in e and "linkedin_connect" not in e.split("one of")[1] for e in errs)
    assert "- linkedin_connect:" not in calls[0]["systemInstruction"]["parts"][0]["text"]
