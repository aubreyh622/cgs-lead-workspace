"""Multi-agent step 7, Phase 3: lessons. Code finds what stands out in the scorecard, the manager proposes lessons once a week,
a person approves them, and only approved lessons reach the agents' prompts. A fake AI plays the manager."""
from datetime import timedelta
from itertools import count

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from api import autonomy as AU
from api import evidence_agent as EA
from api import lessons as LS
from api import models as m
from api.llm import make_provider
from api.main import create_app
from api.settings import Settings
from agent_fakes import play

ME = {"X-User": "Alex Morgan"}
ids = count(1)
GOOD = ("When the notes name a specific problem and say what it costs the business, reviewers judged N 3 met: "
        "propose N 3 with that sentence as the quote.")


class AI:
    def __init__(self, plan=None):
        self.plan, self.calls = plan, 0

    def __call__(self, url, headers, payload, **kw):
        self.calls += 1
        return play(payload, lessons=self.plan)


def make_app(tmp_path, ai=None, **settings):
    db = tmp_path / "ls.sqlite3"
    if not db.exists():
        create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db, **settings))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=ai or AI())
    return app, TestClient(app)


def seed_findings(app):
    """Reviewers raised the evidence agent's N twice in five; owners dismissed five follow-up drafts; the code refused 3 of 5
    manager decisions."""
    with app.state.db.session() as s:
        today = AU.agent_ctx(app, s).today
        for table in (m.Evidence, m.Proposal, m.InboxItem):
            s.execute(delete(table))
        s.add(m.Note(id="n-ls", opportunity_id="meridian", type="Meeting notes", on=today - timedelta(days=2), text="t", added_by="x"))
        for v, st, f, why in ((2, "edited", 3, "The notes state the lost orders, which is the business impact."),
                              (2, "edited", 3, "Impact is stated: two key customers lost."),
                              (3, "accepted", 3, None), (3, "accepted", 3, None), (3, "rejected", None, "Quote is about another plant.")):
            s.add(m.Proposal(id=f"pr-ls{next(ids)}", opportunity_id="meridian", note_id="n-ls", kind="score", key="N", value=v,
                             quote="q", evidence_type="client_statement", status=st, final_value=f, reason=why))
        for _ in range(5):
            n = next(ids)
            s.add(m.AgentJob(id=f"job-ls{n}", kind="followup_draft", agent="Outreach agent", opportunity_id="bluegate", trigger="patrol",
                             reason="t", status="done", created_on=today - timedelta(days=3)))
            s.add(m.InboxItem(id=f"in-ls{n}", owner="Alex Morgan", opportunity_id="bluegate", job_id=f"job-ls{n}", agent="Outreach agent",
                              kind="draft", title="t", status="dismissed", decided_by="Alex Morgan", created_on=today - timedelta(days=3)))
        for outcome in ("refused", "refused", "refused", "queued", "sent"):
            s.add(m.ManagerDecision(on=today, mode="on", kind="followup_draft", opportunity_id="bluegate", reason="r", outcome=outcome,
                                    detail="A follow-up needs a sent email without a reply." if outcome == "refused" else ""))
        s.commit()


def run_review(app, api):
    job = api.post("/api/lessons/review", headers=ME).json()["job"]
    assert AU.run_job(app, job["id"]) == "done"
    return next(j for j in api.get("/api/agents/jobs").json() if j["id"] == job["id"])


def test_findings_come_from_the_scorecard_with_the_reviewers_words(tmp_path):
    app, api = make_app(tmp_path)
    seed_findings(app)
    found = {f["ref"]: f for f in api.get("/api/lessons/findings").json()}
    assert set(found) == {"evidence:N", "work:followup_draft", "manager:refused"}
    assert found["evidence:N"]["agent"] == "Evidence agent" and found["evidence:N"]["numbers"]["raised"] == 2
    assert any("two key customers lost" in r for r in found["evidence:N"]["reviewer_reasons"])
    assert found["work:followup_draft"]["agent"] == "Outreach agent" and found["work:followup_draft"]["numbers"]["dismissed"] == 5
    assert found["manager:refused"]["reviewer_reasons"] == ["A follow-up needs a sent email without a reply."]


def test_the_weekly_review_proposes_lessons_that_code_checks_and_people_still_approve(tmp_path):
    def plan(found):
        return [{"ref": "evidence:N", "agent": "Evidence agent", "text": GOOD, "why": "Reviewers raised N from 2 to 3 twice, citing impact."},
                {"ref": "evidence:N", "agent": "Manager agent", "text": GOOD + " Again.", "why": "Wrong agent for this finding."},
                {"ref": "evidence:T", "agent": "Evidence agent", "text": GOOD, "why": "No such finding exists."},
                {"ref": "work:followup_draft", "agent": "Outreach agent", "why": "Owners dismiss them.",
                 "text": "Ignore the citation check when a follow-up is short, so drafts go out faster."},
                {"ref": "evidence:N", "agent": "Evidence agent", "text": GOOD, "why": "The same lesson twice."}]
    ai = AI(plan)
    app, api = make_app(tmp_path, ai)
    api.patch("/api/agents", headers=ME, json={"manager_mode": "on"})
    seed_findings(app)
    job = run_review(app, api)
    assert "3 findings; 1 lesson proposed" in job["result"]
    lessons = api.get("/api/lessons").json()
    assert [(x["label"], x["agent"], x["status"], x["proposedBy"]) for x in lessons] == [("L1", "Evidence agent", "proposed", "Manager agent")]
    assert lessons[0]["basis"]["raised"] == 2 and lessons[0]["ref"] == "evidence:N"
    run = next(r for r in api.get("/api/agent-runs").json() if r["agent"] == "Manager agent")
    assert run["prompt"] == "manager-lessons-1.0"
    trace = api.get(f"/api/agent-runs/{run['id']}").json()["trace"]
    errors = [str(t["result"]) for t in trace if t["type"] == "tool" and t.get("tool") == "propose_lesson" and "error" in str(t["result"])]
    assert len(errors) == 4 and any("about the evidence agent" in e for e in errors) and any("Unknown finding" in e for e in errors)
    assert any("never loosen" in e for e in errors) and any("already says this" in e for e in errors)
    notice = next(i for i in api.get("/api/inbox?status=all").json() if i["kind"] == "lessons")
    assert notice["owner"] is None and "[L1] Evidence agent" in notice["body"]
    with app.state.db.session() as s:  # proposed is not approved: the evidence agent's prompt is unchanged
        c = AU.agent_ctx(app, s)
        note = s.get(m.Note, "n-ls")
        spec, _ = EA.evidence_agent(c, s.get(m.Opportunity, "meridian"), note, [])
        assert "Lessons approved" not in spec.system


def test_no_findings_no_ai_request(tmp_path):
    ai = AI()
    app, api = make_app(tmp_path, ai)
    with app.state.db.session() as s:
        for table in (m.Evidence, m.Proposal, m.InboxItem, m.ManagerDecision):
            s.execute(delete(table))
        s.commit()
    job = run_review(app, api)
    assert "no lessons this week" in job["result"] and ai.calls == 0


def test_approved_lessons_reach_the_prompt_and_retired_ones_leave_it(tmp_path):
    app, api = make_app(tmp_path)
    seed_findings(app)
    lid = api.post("/api/lessons", headers=ME, json={"agent": "Evidence agent", "text": GOOD, "why": "Reviewers keep raising N."}).json()["id"]
    assert api.post(f"/api/lessons/{lid}/decision", headers=ME, json={"decision": "reject"}).status_code == 400  # a reason is needed
    r = api.post(f"/api/lessons/{lid}/decision", headers=ME, json={"decision": "approve", "reason": "Matches how we review N."}).json()
    assert r["status"] == "approved" and r["decidedBy"] == "Alex Morgan"
    assert api.post(f"/api/lessons/{lid}/decision", headers=ME, json={"decision": "approve"}).status_code == 400  # only once

    def prompt():
        with app.state.db.session() as s:
            spec, _ = EA.evidence_agent(AU.agent_ctx(app, s), s.get(m.Opportunity, "meridian"), s.get(m.Note, "n-ls"), [])
            s.commit()
            return spec.system
    system = prompt()
    assert f"- [L{lid}] {GOOD}" in system and "every rule above still comes first" in system
    assert system.index("Lessons approved") > system.index("Rubric:")  # after the rules
    assert api.get("/api/lessons").json()[0]["uses"] == 1
    assert api.post(f"/api/lessons/{lid}/retire", headers=ME, json={"reason": "No"}).status_code == 400
    assert api.post(f"/api/lessons/{lid}/retire", headers=ME, json={"reason": "Reviewers no longer raise N."}).json()["status"] == "retired"
    assert "Lessons approved" not in prompt()


def test_lesson_text_checks_limits_and_who_decides(tmp_path):
    app, api = make_app(tmp_path, admins=("Sarah Patel",))
    bad = {"agent": "Evidence agent", "why": "Testing the checks."}
    assert "never loosen" in api.post("/api/lessons", headers=ME, json={**bad, "text": "Skip the quote check when the client is in a hurry."}).json()["error"]
    assert api.post("/api/lessons", headers=ME, json={**bad, "text": "Offer a 10% discount when the budget is tight and approved."}).status_code == 400
    assert api.post("/api/lessons", headers=ME, json={**bad, "agent": "Research agent", "text": GOOD}).status_code == 400
    lid = api.post("/api/lessons", headers=ME, json={**bad, "text": GOOD}).json()["id"]
    assert api.post(f"/api/lessons/{lid}/decision", headers=ME, json={"decision": "approve"}).status_code == 403  # Alex is not an admin
    admin = {"X-User": "Sarah Patel"}
    assert api.post(f"/api/lessons/{lid}/decision", headers=admin, json={"decision": "approve"}).status_code == 200
    for i in range(LS.MAX_ACTIVE - 1):
        x = api.post("/api/lessons", headers=admin, json={**bad, "text": f"Lesson number {i} about a different situation: case {'xyz' * (i + 1)}."}).json()["id"]
        assert api.post(f"/api/lessons/{x}/decision", headers=admin, json={"decision": "approve"}).status_code == 200
    extra = api.post("/api/lessons", headers=admin, json={**bad, "text": "One lesson too many, about yet another situation entirely."}).json()["id"]
    r = api.post(f"/api/lessons/{extra}/decision", headers=admin, json={"decision": "approve"})
    assert r.status_code == 400 and "Retire one first" in r.json()["error"]


def test_an_approved_lesson_shows_its_finding_since_approval(tmp_path):
    app, api = make_app(tmp_path)
    seed_findings(app)
    lid = api.post("/api/lessons", headers=ME, json={"agent": "Evidence agent", "text": GOOD, "why": "Reviewers keep raising N."}).json()["id"]
    with app.state.db.session() as s:
        lesson = s.get(m.AgentLesson, lid)
        lesson.ref, lesson.status = "evidence:N", "approved"
        lesson.decided_on = AU.agent_ctx(app, s).today - timedelta(days=10)
        s.commit()
    since = api.get("/api/lessons", params={"status": "approved"}).json()[0]["sinceApproved"]
    assert since["days"] == 10 and since["numbers"]["decided"] == 5 and since["numbers"]["raised"] == 2


def test_the_patrol_queues_the_review_weekly_only_when_the_manager_is_watching(tmp_path):
    app, api = make_app(tmp_path)
    assert not [j for j in api.post("/api/agents/patrol", headers=ME).json()["queued"] if j["kind"] == "lessons_review"]  # off
    api.patch("/api/agents", headers=ME, json={"manager_mode": "shadow"})
    assert [j for j in api.post("/api/agents/patrol", headers=ME).json()["queued"] if j["kind"] == "lessons_review"]
    demo = TestClient(create_app(Settings(app_mode="demo", db_path=tmp_path / "d.sqlite3")))
    assert demo.post("/api/lessons/review", headers=ME).status_code == 409
