"""Carrying an approved contact plan out (api/outreach_plan.py, phase 2): each step is prepared by the outreach agent when
it is due, a person does it and records it (or the workspace records it when the email goes out through it), the first
step that reaches the client makes the lead Contacted, and a reply stops the plan. Demo mode: rules instead of AI, a
mock mailbox."""
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings

ME = {"X-User": "Alex Morgan"}
OPP = "/api/opportunities/halvorsen"


def approved(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "run.sqlite3")))
    api.post(f"{OPP}/outreach/plan", json={}, headers=ME)
    v = api.post(f"{OPP}/outreach/plan/decision", json={"decision": "approve"}, headers=ME).json()
    return api, v


def plan(api):
    return api.get(OPP, headers=ME).json()["outreach"]["plan"]


def steps(api):
    return {s["n"]: s for s in plan(api)["plan"]["steps"]}


def patrol(api):
    return api.post("/api/agents/patrol?run=true", headers=ME).json()


def todo_titles(api):
    return [(i["label"], i["title"]) for g in api.get("/api/todo", headers=ME).json()["groups"] for i in g["items"]]


def test_a_plan_runs_step_by_step_and_the_workspace_records_what_went_out_through_it(tmp_path):
    api, v = approved(tmp_path)
    assert v["running"] and v["current"] == 1 and v["run"]["start"] == "2026-10-26"
    assert [s["channel"] for s in v["plan"]["steps"]] == ["linkedin_connect", "share_content", "email", "email"]
    assert ("Plan step due", "Halvorsen Precision Castings, step 1: LinkedIn connection request to Priya Raman") in todo_titles(api)

    # the patrol has the outreach agent prepare step 1; the person sends the note on LinkedIn and records it as usual
    ran = patrol(api)["ran"]
    assert any(j["kind"] == "plan_step" and "Step 1 prepared" in j["result"] for j in ran)
    assert steps(api)[1]["status"] == "ready" and api.get(OPP, headers=ME).json()["outreach"]["linkedin"]["note"]
    assert ("Do the plan step", "Halvorsen Precision Castings, step 1: LinkedIn connection request to Priya Raman") in todo_titles(api)
    api.post(f"{OPP}/linkedin/sent", json={"what": "note"}, headers=ME)
    o = api.get(OPP, headers=ME).json()
    s1, s2 = (o["outreach"]["plan"]["plan"]["steps"][i] for i in (0, 1))
    assert o["stage"] == "contacted" and s1["status"] == "done" and s1["how"] == "LinkedIn connection request recorded"
    assert s1["doneBy"] == "Alex Morgan" and s2["due"] == "2026-10-30"  # its planned day 4

    # nothing is due before day 4; then the agent drafts the sharing email into the inbox, listed once
    patrol(api)
    assert steps(api)[2]["status"] == "pending"
    api.post("/api/demo/advance-days?days=4", headers=ME)
    patrol(api)
    s2 = steps(api)[2]
    assert s2["status"] == "ready" and s2["prepared"]["kind"] == "email"
    item = next(i for i in api.get("/api/inbox").json() if i["id"] == s2["prepared"]["item"])
    assert item["data"]["planStep"] == 2 and item["data"]["purpose"] == "followup" and item["title"].startswith("Step 2 of the contact plan")
    titles = todo_titles(api)
    assert ("Review and send", item["title"]) in titles and not any(t.startswith("Halvorsen Precision Castings, step 2") for _, t in titles)
    r = api.post(f"/api/inbox/{item['id']}", headers=ME, json={"action": "send", "payload_hash": item["payloadHash"], "to": "priya@halvorsen.example"})
    assert r.status_code == 200, r.text
    assert len(api.app.state.mail.outbox) == 1
    s2, s3 = steps(api)[2], steps(api)[3]
    assert s2["status"] == "done" and s2["how"] == "email sent through Gmail" and s3["due"] == "2026-11-06"  # 7 days after step 2


def test_while_a_plan_runs_the_seven_day_follow_up_rule_stands_aside(tmp_path):
    api, _ = approved(tmp_path)
    patrol(api)
    api.post(f"{OPP}/linkedin/sent", json={"what": "note"}, headers=ME)
    api.post(f"{OPP}/outreach/plan/steps/2", json={"action": "skip", "note": "No piece fits yet"}, headers=ME)
    api.post("/api/demo/advance-days?days=11", headers=ME)  # step 3 is planned for day 11; the email went out 11 days ago
    jobs = patrol(api)["queued"]
    kinds = [j["kind"] for j in jobs if j["opportunityId"] == "halvorsen"]
    assert "followup_draft" not in kinds and "plan_step" in kinds
    assert not any(label in ("Follow up", "Overdue") and "Halvorsen" in t for label, t in todo_titles(api))


def test_a_reply_stops_the_plan(tmp_path):
    api, _ = approved(tmp_path)
    patrol(api)
    api.post(f"{OPP}/linkedin/sent", json={"what": "note"}, headers=ME)
    api.post(f"{OPP}/outreach/reply", json={"text": "Happy to talk next week."}, headers=ME)
    pl = plan(api)
    assert pl["running"] is False and "moved to active" in pl["stoppedWhy"]
    api.post("/api/demo/advance-days?days=10", headers=ME)
    assert not [j for j in patrol(api)["queued"] if j["kind"] == "plan_step"]
    assert api.post(f"{OPP}/outreach/plan/steps/2/prepare", headers=ME).json()["code"] == "wrong_stage"


def test_what_a_person_must_say_and_what_cannot_be_done(tmp_path):
    api, _ = approved(tmp_path)
    assert api.post(f"{OPP}/outreach/plan/steps/2/prepare", headers=ME).json()["code"] == "not_current"
    assert api.post(f"{OPP}/outreach/plan/steps/1", json={"action": "skip"}, headers=ME).json()["code"] == "no_reason"
    assert api.post(f"{OPP}/outreach/plan/stop", json={}, headers=ME).json()["code"] == "no_reason"
    # an email step done another way (recorded by hand) is the first contact too
    api.post(f"{OPP}/outreach/plan/steps/1", json={"action": "skip", "note": "Not on LinkedIn"}, headers=ME)
    r = api.post(f"{OPP}/outreach/plan/steps/2", json={"action": "done", "note": "Sent from my phone"}, headers=ME).json()
    assert r["plan"]["steps"][1]["status"] == "done" and api.get(OPP, headers=ME).json()["stage"] == "contacted"
    stopped = api.post(f"{OPP}/outreach/plan/stop", json={"note": "Priya left the company"}, headers=ME).json()
    assert stopped["running"] is False and "Priya left the company" in stopped["stoppedWhy"]
    assert api.post(f"{OPP}/outreach/plan/steps/3", json={"action": "done"}, headers=ME).json()["code"] == "plan_stopped"


def test_a_call_needs_what_happened_and_a_step_for_someone_else_waits_for_a_person(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "x.sqlite3")))
    from api import models as m
    api.post(f"{OPP}/outreach/plan", json={}, headers=ME)
    with api.app.state.db.session() as s:  # a plan that calls Priya, then writes to someone else
        r = s.get(m.Outreach, "halvorsen")
        p = dict(r.plan)
        p["plan"] = {**p["plan"], "steps": [{"n": 1, "day": 0, "channel": "call", "to": "Priya Raman", "purpose": "A short call about the Ohio foundry."},
                                            {"n": 2, "day": 3, "channel": "email", "to": "Tom Becker", "purpose": "A note to the COO about the ramp-up."}]}
        r.plan = p
        s.commit()
    api.post(f"{OPP}/outreach/plan/decision", json={"decision": "approve"}, headers=ME)
    patrol(api)
    s1 = steps(api)[1]
    assert s1["prepared"]["kind"] == "call" and len(s1["prepared"]["questions"]) == 3 and s1["prepared"]["opening"]
    assert api.post(f"{OPP}/outreach/plan/steps/1", json={"action": "done"}, headers=ME).json()["code"] == "no_note"
    api.post(f"{OPP}/outreach/plan/steps/1", json={"action": "done", "note": "Voicemail left"}, headers=ME)
    assert api.get(OPP, headers=ME).json()["stage"] == "contacted"
    api.post("/api/demo/advance-days?days=3", headers=ME)
    ran = patrol(api)["ran"]
    job = next(j for j in ran if j["kind"] == "plan_step")
    assert job["status"] == "skipped" and "not the opportunity's contact" in job["result"]
    assert any(i["kind"] == "reminder" and "step 2" in i["title"] for i in api.get("/api/inbox").json())


def test_accepting_a_lead_can_start_a_contact_plan_instead_of_the_first_email(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "s4.sqlite3")))
    assert api.patch("/api/agents", json={"first_contact": "plan"}, headers=ME).json()["firstContact"] == "plan"
    api.post("/api/candidates/c1/pick", headers=ME)
    api.post("/api/opportunities/corvane/step4", headers=ME, json={
        "decision": "accept", "rating": 4, "owner": "Alex Morgan", "next_action": "Send outreach", "due": "2026-10-29",
        "duplicates_checked": True, "scope": "theme"})
    kinds = [j["kind"] for j in api.get("/api/agents/jobs", params={"opportunity": "corvane"}).json()]
    assert "contact_plan" in kinds and "first_draft" not in kinds
    api.post("/api/agents/run", headers=ME)
    pl = api.get("/api/opportunities/corvane", headers=ME).json()["outreach"]["plan"]
    assert pl["status"] == "proposed" and pl["plan"]["steps"]
    assert any(label == "Decide the contact plan" for label, _ in todo_titles(api))
