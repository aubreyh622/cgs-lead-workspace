"""Multi-agent step 3: the manager agent beside the rules, in shadow mode (decide and compare) and on mode (carry out).
A fake AI plays the manager, so these run offline."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api import autonomy as AU
from api.llm import make_provider
from api.main import create_app
from api.settings import Settings
from agent_fakes import play

ME = {"X-User": "Alex Morgan"}


def plan(board):
    """What the fake manager decides after reading the board."""
    ids = {o["id"]: o for o in board["opportunities"]}
    assert ids["bluegate"]["outreach"]["sent_days_ago"] >= 7 and not ids["bluegate"]["outreach"]["replied"]
    assert all(w["by"] not in ("patrol", "event") or w["on"] != board["today"] for o in ids.values() for w in o["recent_agent_work"])
    return [("delegate", {"kind": "followup_draft", "opportunity_id": "bluegate", "reason": "Email sent over a week ago, no reply yet."}),
            ("delegate", {"kind": "person_lookup", "opportunity_id": "meridian", "role": "CFO (signs the budget)",
                          "reason": "Notes say the CFO signs off, and only the VP Finance is a contact."}),
            ("delegate", {"kind": "gap_actions", "opportunity_id": "bluegate", "reason": "Wants next actions for this lead."}),
            ("note_for_owner", {"opportunity_id": "northstar", "text": "The submitted pack is out of date; resubmit it before the approver looks."})]


class ManagerAI:
    def __init__(self):
        self.manager_turns = 0

    def __call__(self, url, headers, payload, **kw):
        if "delegate" in {d["name"] for d in payload["tools"][0]["functionDeclarations"]}:
            self.manager_turns += 1
        return play(payload, manager=plan)


@pytest.fixture
def app(tmp_path):
    db = tmp_path / "mg.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=ManagerAI())
    return app


def review(app, mode):
    api = TestClient(app)
    assert api.patch("/api/agents", headers=ME, json={"manager_mode": mode}).json()["managerMode"] == mode
    api.post("/api/demo/advance-days?days=8", headers=ME)
    queued = api.post("/api/agents/patrol", headers=ME).json()["queued"]
    assert any(j["kind"] == "manager_review" for j in queued)
    ran = AU.run_pending(app)
    assert next(r for r in ran if r["kind"] == "manager_review")["status"] == "done"
    return api, ran


def test_shadow_mode_decides_and_compares_but_queues_nothing(app):
    api, ran = review(app, "shadow")
    board = api.get("/api/agents/jobs", params={"limit": 200}).json()
    assert not [j for j in board if j["trigger"] == "manager"]                 # nothing carried out
    assert not [i for i in api.get("/api/inbox?status=all").json() if i["kind"] == "advice"]
    cmp = api.get("/api/manager/compare").json()
    assert cmp["mode"] == "shadow"
    assert [(x["kind"], x["opportunityId"]) for x in cmp["agree"]] == [("followup_draft", "bluegate")]
    assert [(x["kind"], x["opportunityId"]) for x in cmp["managerOnly"]] == [("person_lookup", "meridian")]
    assert ("followup_draft", "summit") in [(x["kind"], x["opportunityId"]) for x in cmp["rulesOnly"]]
    assert cmp["refused"][0]["kind"] == "gap_actions" and "stage contacted" in cmp["refused"][0]["refusal"]
    assert cmp["notes"][0]["opportunityId"] == "northstar"
    run = next(r for r in api.get("/api/agent-runs").json() if r["agent"] == "Manager agent" and r["prompt"].startswith("manager-agent"))
    assert run["prompt"] == "manager-agent-1.2" and run["steps"]


def test_on_mode_carries_out_its_decisions_beside_the_rules(app):
    api, ran = review(app, "on")
    board = api.get("/api/agents/jobs", params={"limit": 200}).json()
    mine = [j for j in board if j["trigger"] == "manager"]
    assert [(j["kind"], j["opportunityId"]) for j in mine] == [("person_lookup", "meridian")]   # the follow-up was already the rules' job
    assert any(r["kind"] == "person_lookup" and r["opportunityId"] == "meridian" for r in ran)    # and the research agent did it
    decisions = {(d["kind"], d["opportunityId"]): d["outcome"] for d in api.get("/api/manager/decisions").json()}
    assert decisions[("followup_draft", "bluegate")] == "already_queued" and decisions[("person_lookup", "meridian")] == "queued"
    assert decisions[("gap_actions", "bluegate")] == "refused" and decisions[("note", "northstar")] == "sent"
    advice = [i for i in api.get("/api/inbox?status=all").json() if i["kind"] == "advice"]
    assert advice and advice[0]["opportunityId"] == "northstar" and "out of date" in advice[0]["body"]
    lookup = next(j for j in mine if j["kind"] == "person_lookup")
    assert lookup["requestedBy"] == "Manager agent"


def test_the_manager_does_not_run_when_off_or_in_demo_mode(app, tmp_path):
    api = TestClient(app)
    api.post("/api/demo/advance-days?days=8", headers=ME)
    assert not [j for j in api.post("/api/agents/patrol", headers=ME).json()["queued"] if j["kind"] == "manager_review"]  # off by default
    demo = TestClient(create_app(Settings(app_mode="demo", db_path=tmp_path / "d.sqlite3")))
    demo.patch("/api/agents", headers=ME, json={"manager_mode": "on"})
    demo.post("/api/demo/advance-days?days=8", headers=ME)
    assert not [j for j in demo.post("/api/agents/patrol", headers=ME).json()["queued"] if j["kind"] == "manager_review"]
    assert TestClient(app).get("/api/manager/compare").json()["day"] is None


def test_the_manager_decides_before_the_rules_work_and_does_not_repeat_notes(app):
    api, ran = review(app, "shadow")
    assert ran[0]["kind"] == "manager_review"  # it decides before the rules' work of the day runs, so the comparison is fair
    api.post("/api/demo/advance-days?days=1", headers=ME)
    api.post("/api/agents/patrol", headers=ME)
    AU.run_pending(app)
    second = [d for d in api.get("/api/manager/decisions").json() if d["kind"] == "note" and d["opportunityId"] == "northstar"]
    assert [d["outcome"] for d in second] == ["refused", "recorded"]  # newest first: nothing changed, so the next day's note was refused
    # once the owner makes progress, the manager may tell them something new
    api.post("/api/demo/advance-days?days=1", headers=ME)
    api.post("/api/opportunities/northstar/notes", headers=ME, json={"text": "Called the client; pack to be refreshed.", "analyze": False})
    api.post("/api/demo/advance-days?days=1", headers=ME)
    api.post("/api/agents/patrol", headers=ME)
    AU.run_pending(app)
    third = [d["outcome"] for d in api.get("/api/manager/decisions").json() if d["kind"] == "note" and d["opportunityId"] == "northstar"]
    assert third[0] == "recorded"


def test_on_mode_never_redoes_work_finished_in_the_last_two_weeks(app):
    """Found in the on-mode dry run: the manager asked for a second Bluegate follow-up a week after the first."""
    api, _ = review(app, "on")
    api.post("/api/demo/advance-days?days=7", headers=ME)
    api.post("/api/agents/patrol", headers=ME)
    AU.run_pending(app)
    board = api.get("/api/agents/jobs", params={"limit": 500}).json()
    assert len([j for j in board if j["kind"] == "followup_draft" and j["opportunityId"] == "bluegate"]) == 1
    second = [d for d in api.get("/api/manager/decisions").json() if d["kind"] == "followup_draft" and d["opportunityId"] == "bluegate"]
    assert second[0]["outcome"] == "refused" and "already done" in second[0]["detail"]


def test_new_agent_findings_count_as_a_change_for_notes(app):
    """Found in the on-mode run with Claude: a note about the research agent's new result was refused as 'nothing changed'."""
    from datetime import timedelta
    from api import manager_agent as MA
    from api import models as m
    api, _ = review(app, "on")  # today: a note about Northstar, and the research agent looks for Meridian's CFO
    with app.state.db.session() as s:
        c = AU.agent_ctx(app, s)
        s.add(m.ManagerDecision(on=c.today - timedelta(days=1), mode="on", kind="note", opportunity_id="meridian",
                                reason="Earlier note", outcome="sent"))  # the owner was told yesterday, before the lookup ran
        for j in s.scalars(select(m.AgentJob).where(m.AgentJob.kind.in_(("gap_actions", "manager_triage")), m.AgentJob.opportunity_id == "meridian")):
            j.created_on = c.today - timedelta(days=3)  # keep today's next-action work out of this check (tested separately)
        s.flush()
        spec, _ = MA.manager_agent(c, "on")
        note = next(t for t in spec.tools if t.name == "note_for_owner")
        assert note.run({"opportunity_id": "meridian", "text": "The research agent finished looking for the CFO; see its result."})["outcome"]
        with pytest.raises(Exception, match="nothing has changed"):  # Northstar: told today, nothing new since
            note.run({"opportunity_id": "northstar", "text": "Still out of date, please resubmit the pack soon."})


def test_one_voice_per_opportunity_in_on_mode_the_triage_writes_and_hears_both_reasons():
    """Meridian has stalled. With the manager on, the patrol gives it to the manager's triage, not to the next-step agent.
    The daily review may not write a separate note, and its reason for wanting next actions is added to that triage,
    which sends the owner one message."""
    def plan2(board):
        return [("note_for_owner", {"opportunity_id": "meridian", "text": "Confirm who approves; the decision group task is overdue."}),
                ("delegate", {"kind": "gap_actions", "opportunity_id": "meridian",
                              "reason": "The overdue decision-group task: ask Emma Chen who signs off."})]

    seen, next_step_runs = [], []

    def triage(case):
        seen.extend(e["why"] for e in case["what_happened"])
        from agent_fakes import default_triage
        return default_triage(case)

    def ai(url, headers, payload, **kw):
        if "suggest" in {d["name"] for d in payload["tools"][0]["functionDeclarations"]}:
            next_step_runs.append(1)
        return play(payload, manager=plan2, triage=triage)

    import tempfile
    from pathlib import Path
    db = Path(tempfile.mkdtemp()) / "one.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=ai)
    api, ran = review(app, "on")
    decisions = {(d["kind"], d["opportunityId"]): d for d in api.get("/api/manager/decisions", params={"scope": "review"}).json()}
    assert decisions[("note", "meridian")]["outcome"] == "refused" and "triage" in decisions[("note", "meridian")]["detail"]
    assert decisions[("gap_actions", "meridian")]["outcome"] == "already_queued"
    assert any(r["kind"] == "manager_triage" and r["opportunityId"] == "meridian" and r["status"] == "done" for r in ran)
    assert not next_step_runs  # the next-step agent did not run: the triage planned the next actions
    assert any("No progress recorded" in w for w in seen) and any("The manager adds: The overdue decision-group task" in w for w in seen)
    msgs = [i for i in api.get("/api/inbox?status=all").json() if i["kind"] in ("advice", "actions") and i["opportunityId"] == "meridian"]
    assert len(msgs) == 1 and msgs[0]["agent"] == "Manager agent" and msgs[0]["kind"] == "actions"  # one voice
