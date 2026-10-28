"""Step 4 checks: every human action through the API, and the workflow rules that guard them."""
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings

ME = {"X-User": "Alex Morgan"}
STAR_NOTE_HINT = "Arden"


def make(tmp_path, mode="demo"):
    return TestClient(create_app(Settings(app_mode=mode, db_path=tmp_path / "ws.sqlite3")))


@pytest.fixture
def api(tmp_path):
    return make(tmp_path)


def post(api, url, body=None, ok=200, user=ME):
    r = api.post(url, json=body or {}, headers=user)
    assert r.status_code == ok, r.text
    return r.json()


def get(api, oid):
    return api.get(f"/api/opportunities/{oid}").json()


def star_note():
    from api.demo_agents import STAR
    return STAR["note"]


def accept_body(**kw):
    return {"decision": "accept", "rating": 4, "owner": "Alex Morgan", "next_action": "Send tailored outreach", "due": "2026-10-29",
            "duplicates_checked": True, "scope": "theme", **kw}


def test_writes_need_a_known_user(api):
    assert api.post("/api/candidates/c2/pick").status_code == 401
    assert api.post("/api/candidates/c2/pick", headers={"X-User": "Mallory"}).status_code == 401
    assert api.get("/api/users").json()[0] == "Alex Morgan"


def test_full_corvane_flow_from_step_1_to_8(api):
    # Step 1 -> 3: pick; demo research finishes, and finds a second opportunity at the same company
    created = post(api, "/api/candidates/c1/pick")["opportunities"]
    assert created == ["corvane", "corvane-ops"]
    o = get(api, "corvane")
    assert o["stage"] == "review" and len(o["brief"]["facts"]) == 4
    assert {x["id"] for x in api.get("/api/companies/corvane/opportunities").json()} == {"corvane", "corvane-ops"}

    # Step 4
    post(api, "/api/opportunities/corvane/step4", accept_body(minutes=6))
    o = get(api, "corvane")
    assert o["stage"] == "identified" and o["owner"] == "Alex Morgan" and o["brief"]["frozenOn"]

    # Step 5
    post(api, "/api/opportunities/corvane/outreach/sent", {"via": "Email"})
    assert get(api, "corvane")["stage"] == "contacted"

    # Step 6: any reply makes it Active; the reply is labeled
    r = post(api, "/api/opportunities/corvane/outreach/reply", {"text": "Thanks. Could we talk on Wednesday 4 November?", "on": "2026-10-28"})
    assert r["replyLabel"] == "meet" and get(api, "corvane")["stage"] == "active"
    res = post(api, "/api/opportunities/corvane/notes", {"text": star_note(), "on": "2026-11-04"})
    assert res["proposals"] == 12 and res["droppedForInvalidQuote"] == 0
    o = get(api, "corvane")
    assert o["assessment"]["readiness"] == "More evidence needed"
    for p in o["proposals"]:
        post(api, f"/api/proposals/{p['id']}", {"action": "accept"})

    # Step 7: the rules engine says Ready; the need statement was filled from the call
    o = get(api, "corvane")
    assert o["assessment"]["ql"] == 70.5 and o["assessment"]["readiness"] == "Ready"
    assert o["need"]["problem"] and o["next"]["action"] == "Submit the qualification pack"
    pack = post(api, "/api/opportunities/corvane/packs", {})["id"]

    # Step 8
    post(api, f"/api/packs/{pack}/decision", {"decision": "approve", "role": "Director of Operations"})
    o = get(api, "corvane")
    assert o["stage"] == "qualification" and o["crmSync"] == "sync_pending"
    assert [d["step"] for d in o["decisions"]] == [4, 8]
    assert len(o["evidence"]) == 12 and len(o["activity"]) > 10


def test_step4_rules(api):
    assert post(api, "/api/opportunities/ostrander/step4", accept_body(duplicates_checked=False), ok=400)["code"] == "check_required"
    assert post(api, "/api/opportunities/pellham/step4", {"decision": "reject", "reason": "Other", "scope": "theme"}, ok=400)["code"] == "reason_required"
    assert post(api, "/api/opportunities/meridian/step4", accept_body(), ok=409)["code"] == "wrong_stage"
    # reject with an exclusion suggestion: it waits for approval, nothing changes yet
    post(api, "/api/opportunities/pellham/step4", {"decision": "reject", "reason": "Existing relationship or already in progress", "scope": "theme", "suggest_exclusion": True})
    d = api.get("/api/direction").json()
    assert any(p["type"] == "exclude" and p["domain"] == "pellham.example" and p["status"] == "pending" for p in d["proposals"])
    assert "pellham.example" not in d["current"]["excluded_domains"]
    fb = api.get("/api/feedback").json()[0]
    assert fb["subject"] == "Pellham Packaging" and fb["minutes"] is None


def test_return_for_research_runs_a_second_pass(api):
    post(api, "/api/opportunities/ostrander/step4", {"decision": "return", "questions": "Who now leads the automation program?", "scope": "opportunity"})
    o = get(api, "ostrander")
    assert o["stage"] == "review" and o["contact"]["name"] == "Rachel Duval" and o["brief"]["flags"] == []
    assert o["brief"]["recommendation"]["decision"] == "accept"


def test_contact_confirmation_and_sibling(api):
    post(api, "/api/candidates/c1/pick")
    post(api, "/api/opportunities/corvane-ops/contact", {"name": "Owen Price", "title": "COO", "note": "Corvane leadership page, 26 Oct"})
    o = get(api, "corvane-ops")
    assert o["contact"]["confirmedBy"] == "Alex Morgan" and o["brief"]["flags"] == []
    sib = post(api, "/api/opportunities/corvane/siblings", {"title": "Plant network data platform", "team": "IT"})["id"]
    assert get(api, sib)["stage"] == "review" and get(api, sib)["companyId"] == "corvane"


def test_proposal_rules(api):
    p = get(api, "cedarbay")["proposals"][0]  # Budget 3 -> 4 conflicts with the confirmed 3
    assert post(api, f"/api/proposals/{p['id']}", {"action": "accept"}, ok=400)["code"] == "conflict_needs_reason"
    assert post(api, f"/api/proposals/{p['id']}", {"action": "edit", "value": 4}, ok=400)["code"] == "reason_required"
    assert post(api, f"/api/proposals/{p['id']}", {"action": "edit", "value": 7, "reason": "x"}, ok=422)
    r = post(api, f"/api/proposals/{p['id']}", {"action": "accept", "reason": "Approval confirmed in Maya's email"})
    assert r["change"] == "B 3 → 4: Q +5, P +3"
    o = get(api, "cedarbay")
    assert o["assessment"]["ql"] == 74 and o["assessment"]["readiness"] == "Ready"
    assert post(api, f"/api/proposals/{p['id']}", {"action": "reject", "reason": "x"}, ok=409)["code"] == "wrong_status"


def test_generic_notes_only_keep_real_quotes(api):
    text = "Our month-end close is a manual process with long delays. We would welcome outside help. The budget comes from the finance office."
    r = post(api, "/api/opportunities/alder/notes", {"text": text})
    assert r["proposals"] >= 3
    for p in get(api, "alder")["proposals"]:
        assert p["quote"] in text


def test_stale_pack_blocks_approval_until_resubmitted(api):
    pack = get(api, "northstar")["packs"][0]
    assert post(api, f"/api/packs/{pack['id']}/decision", {"decision": "approve"}, ok=409)["code"] == "stale_pack"
    new = post(api, "/api/opportunities/northstar/packs", {})["id"]
    o = get(api, "northstar")
    assert [p["status"] for p in o["packs"]] == ["superseded", "open"]
    # a change after resubmission makes the new pack out of date too
    post(api, "/api/opportunities/northstar/review", {"kind": "score", "key": "E", "value": 5, "reason": "Three stakeholders joined"})
    assert get(api, "northstar")["packs"][1]["staleReason"].startswith("Engagement changed")
    post(api, "/api/opportunities/northstar/packs", {})
    newest = get(api, "northstar")["packs"][-1]
    post(api, f"/api/packs/{newest['id']}/decision", {"decision": "approve"})
    assert get(api, "northstar")["stage"] == "qualification"


def test_handover_needs_acceptance_by_the_new_owner(api):
    sarah, quinn = {"X-User": "Sarah Patel"}, {"X-User": "Quinn Harper"}
    pid = post(api, "/api/opportunities/cedarbay/review", {"kind": "score", "key": "B", "value": 4, "reason": "Budget approved"}) and \
        post(api, "/api/opportunities/cedarbay/packs", {})["id"]
    body = {"decision": "approve", "role": "Another consultant", "new_owner": "Quinn Harper", "handover_accepted": True}
    assert post(api, f"/api/packs/{pid}/decision", body)["status"] == "handover_pending"  # Alex cannot accept for Quinn
    o = get(api, "cedarbay")
    assert o["stage"] == "approval" and o["owner"] == "Sarah Patel" and o["packs"][-1]["handoverTo"] == "Quinn Harper"
    assert post(api, f"/api/packs/{pid}/handover", {"accept": True}, ok=403)["code"] == "not_new_owner"
    assert post(api, f"/api/packs/{pid}/handover", {"accept": False}, ok=400, user=quinn)["code"] == "reason_required"
    assert post(api, f"/api/packs/{pid}/handover", {"accept": True}, user=quinn)["status"] == "approved"
    o = get(api, "cedarbay")
    assert o["owner"] == "Quinn Harper" and o["stage"] == "qualification"
    d = o["decisions"][-1]
    assert d["by"] == "Alex Morgan" and d["handoverFrom"] == "Sarah Patel" and d["handoverTo"] == "Quinn Harper"
    # the new owner approving accepts in one step; a declined handover leaves the pack open for a new decision
    pid = post(api, "/api/opportunities/northstar/packs", {})["id"]
    post(api, f"/api/packs/{pid}/decision", {"decision": "approve", "role": "Lead owner", "new_owner": "Sarah Patel"})
    assert post(api, f"/api/packs/{pid}/handover", {"accept": False, "reason": "Too busy this month"}, user=sarah)["status"] == "declined"
    o = get(api, "northstar")
    assert o["stage"] == "approval" and o["owner"] == "Alex Morgan" and o["packs"][-1]["handoverTo"] is None


def test_step8_role_must_fit_the_person(api):
    pid = post(api, "/api/opportunities/cedarbay/review", {"kind": "score", "key": "B", "value": 4, "reason": "Budget approved"},
               user={"X-User": "Sarah Patel"}) and post(api, "/api/opportunities/cedarbay/packs", {}, user={"X-User": "Sarah Patel"})["id"]
    assert post(api, f"/api/packs/{pid}/decision", {"decision": "approve", "role": "Lead owner"}, ok=403)["code"] == "wrong_role"
    assert post(api, f"/api/packs/{pid}/decision", {"decision": "approve", "role": "Another consultant"},
                ok=403, user={"X-User": "Sarah Patel"})["code"] == "wrong_role"
    from dataclasses import replace
    api.app.state.settings = replace(api.app.state.settings, directors=("Quinn Harper",))  # CGS_DIRECTORS=Quinn Harper
    assert post(api, f"/api/packs/{pid}/decision", {"decision": "approve", "role": "Director of Operations"}, ok=403)["code"] == "wrong_role"
    assert post(api, f"/api/packs/{pid}/decision", {"decision": "approve", "role": "Director of Operations"},
                user={"X-User": "Quinn Harper"})["status"] == "approved"


def test_bad_input_gets_a_clear_error_not_a_crash(api):
    r = post(api, "/api/opportunities/meridian/review", {"kind": "revenue", "value": 500000, "reason": "x"}, ok=400)
    assert r["code"] == "invalid_value"
    r = post(api, "/api/direction/proposals", {"field": "problem", "value": ["a", "b"], "rationale": "x"}, ok=400)
    assert r["code"] == "invalid_value"


def test_not_ready_needs_an_exception_reason(api):
    assert post(api, "/api/opportunities/meridian/packs", {}, ok=400)["code"] == "not_ready"
    pid = post(api, "/api/opportunities/meridian/packs", {"exception": "CFO sponsor confirmed verbally; written confirmation next week"})["id"]
    post(api, f"/api/packs/{pid}/decision", {"decision": "approve"})
    assert get(api, "meridian")["decisions"][-1]["decision"] == "Approved with exception"


def test_blocker_and_revenue_reviews(api):
    assert get(api, "atlas")["assessment"]["readiness"] == "Not ready"
    post(api, "/api/opportunities/atlas/review", {"kind": "blocker_clear", "reason": "Board lifted the freeze"})
    assert get(api, "atlas")["assessment"]["readiness"] == "More evidence needed"
    post(api, "/api/opportunities/alder/review", {"kind": "revenue", "value": {"low": 150000, "high": 250000}, "reason": "Two-phase scope"})
    assert get(api, "alder")["revenue"] == {"low": 150000, "high": 250000}
    assert post(api, "/api/opportunities/alder/review", {"kind": "revenue", "value": {"low": 9, "high": 1}, "reason": "x"}, ok=400)


def test_suggestions_become_tasks_only_when_confirmed(api):
    assert post(api, "/api/opportunities/alder/suggestions")["suggestions"] == 3
    o = get(api, "alder")
    assert len(o["suggestions"]) == 3 and o["tasks"] == []
    sid = o["suggestions"][0]["id"]
    tid = post(api, f"/api/suggestions/{sid}/task", {"side": "Client", "assignee": "Oliver Kim", "due": "2026-11-05"})["id"]
    before = get(api, "alder")["assessment"]
    assert post(api, f"/api/tasks/{tid}/toggle")["status"] == "done"
    assert get(api, "alder")["assessment"] == before  # finishing a task never changes a score


def test_learning_loop(api):
    # whitelist and base-version checks
    assert post(api, "/api/direction/proposals", {"field": "weights", "value": "x", "rationale": "y"}, ok=400)["code"] == "not_whitelisted"
    post(api, "/api/direction/proposals/p2/approve")  # drops IT Manager -> v4
    d = api.get("/api/direction").json()
    assert d["current"]["version"] == "v4" and "IT Manager" not in d["current"]["buyer_roles"]
    # a personal version applies to one person only
    pid = post(api, "/api/direction/proposals", {"field": "signals", "scope": "consultant", "value": "Merger or acquisition in the last 12 months", "rationale": "My focus"})["id"]
    v = post(api, f"/api/direction/proposals/{pid}/approve")["version"]
    assert v.startswith("v4-alex-morgan-p")
    assert api.get("/api/direction").json()["current"]["version"] == "v4"
    # analysis finds no repeated reason on v4 yet
    assert "No change proposed" in post(api, "/api/direction/analyze")["summary"]


def test_personal_direction_belongs_to_proposer_and_follows_shared_changes(api):
    from api import models as m
    sarah, quinn = {"X-User": "Sarah Patel"}, {"X-User": "Quinn Harper"}
    post(api, "/api/direction/proposals/p2/approve")  # shared v4
    merger = "Merger or acquisition in the last 12 months"
    pid = post(api, "/api/direction/proposals", {"field": "signals", "scope": "consultant", "value": merger, "rationale": "My focus"})["id"]
    assert post(api, f"/api/direction/proposals/{pid}/approve", user=sarah)["version"] == "v4-alex-morgan-p1"  # Alex's, though Sarah approved
    pid = post(api, "/api/direction/proposals", {"field": "problem", "scope": "consultant", "value": "Alex's own problem statement", "rationale": "x"})["id"]
    assert post(api, f"/api/direction/proposals/{pid}/approve")["version"] == "v4-alex-morgan-p2"  # the name does not grow
    # a shared change reaches Alex too, on top of his own changes
    excl = api.get("/api/direction").json()["current"]["exclusions"] + ["Public sector bodies"]
    pid = post(api, "/api/direction/proposals", {"field": "exclusions", "scope": "theme", "value": excl, "rationale": "CGS policy"}, user=sarah)["id"]
    assert post(api, f"/api/direction/proposals/{pid}/approve", user=quinn)["version"] == "v5"
    with api.app.state.db.session() as s:
        mine = s.get(m.DirectionVersion, "v4-alex-morgan-p2")
        assert mine.owner == "Alex Morgan" and mine.approved_by == "Alex Morgan"
        new = s.get(m.DirectionVersion, "v5-alex-morgan-p3")
        assert new and new.owner == "Alex Morgan" and new.signals == [merger]
        assert new.problem == "Alex's own problem statement" and "Public sector bodies" in new.exclusions
        assert not s.query(m.DirectionVersion).filter_by(owner="Sarah Patel").count()
    assert get(api, post(api, "/api/opportunities", {"company": "Fenwick Tooling"})["id"])["directionVersion"] == "v5-alex-morgan-p3"
    assert get(api, post(api, "/api/opportunities", {"company": "Garrow Plastics"}, user=sarah)["id"])["directionVersion"] == "v5"


def test_skip_with_competitor_reason_proposes_exclusion(api):
    post(api, "/api/candidates/c8/skip", {"reason": "Competitor or company we do not work with", "scope": "cgs"})
    assert any(p["target"] == "Northgate Chemical" for p in api.get("/api/direction").json()["proposals"])
    assert post(api, "/api/candidates/c8/pick", ok=409)["code"] == "wrong_status"


def test_research_controls(api):
    post(api, "/api/opportunities/marlow/research/stop")
    assert get(api, "marlow")["research"]["status"] == "paused"
    post(api, "/api/opportunities/marlow/research/resume")
    assert get(api, "marlow")["stage"] == "review"


def test_revision_guard(api):
    rev = get(api, "harbour")["revision"]
    post(api, "/api/opportunities/harbour/review", {"kind": "score", "key": "T", "value": 3, "reason": "New date", "revision": rev})
    assert post(api, "/api/opportunities/harbour/review", {"kind": "score", "key": "T", "value": 4, "reason": "x", "revision": rev}, ok=409)["code"] == "revision_conflict"


def test_live_mode_never_falls_back_to_demo_results(tmp_path):
    api = make(tmp_path, "live")
    api.app.state.research_bridge = object()  # never reached: research needs a website first
    # live mode starts empty; add one lead without a website: research fails visibly instead of inventing a brief
    oid = post(api, "/api/opportunities", {"company": "Northfield Robotics", "name": "Jamie Cross", "title": "VP Operations"})["id"]
    o = get(api, oid)
    assert o["stage"] == "researching" and o["research"]["status"] == "failed" and "website" in o["research"]["error"]
    runs = api.get("/api/agent-runs").json()
    assert runs[0]["status"] == "failed"
