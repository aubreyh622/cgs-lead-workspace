"""Multi-agent step 7, Phase 2: agent scorecards from the records the workspace already keeps, and the manager's caution
when it hands an owner work they usually dismiss. No AI in the scorecard; a fake AI plays the manager."""
from datetime import timedelta
from itertools import count

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from api import autonomy as AU
from api import manager_agent as MA
from api import models as m
from api import scorecard as SC
from api.llm import make_provider
from api.main import create_app
from api.settings import Settings
from agent_fakes import default_triage, play

ME = {"X-User": "Alex Morgan"}
ids = count(1)


@pytest.fixture
def app(tmp_path):
    db = tmp_path / "sc.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    return create_app(Settings(app_mode="live", db_path=db))


def history(s, today, owner, kind, outcomes, opp="meridian", days_ago=3, agent="Research agent"):
    """Inbox items an agent prepared for an owner from jobs of one kind, and what happened to each."""
    how = {"used": ("applied", owner), "done": ("done", owner), "dismissed": ("dismissed", owner),
           "ignored": ("dismissed", "Expired after 14 days unread"), "replaced": ("dismissed", "Replaced by a newer item"), "waiting": ("new", None)}
    for o in outcomes:
        n = next(ids)
        s.add(m.AgentJob(id=f"job-t{n}", kind=kind, agent=agent, opportunity_id=opp, trigger="patrol", reason="test", status="done",
                         created_on=today - timedelta(days=days_ago)))
        status, by = how[o]
        s.add(m.InboxItem(id=f"in-t{n}", owner=owner, opportunity_id=opp, job_id=f"job-t{n}", agent=agent, kind="facts", title="t",
                          status=status, decided_by=by, created_on=today - timedelta(days=days_ago)))


def clear(s):
    for table in (m.Evidence, m.Proposal, m.InboxItem):
        s.execute(delete(table))


def test_work_rows_count_what_people_did_and_judge_only_with_enough_decisions(app):
    with app.state.db.session() as s:
        today = AU.agent_ctx(app, s).today
        clear(s)
        history(s, today, "Alex Morgan", "research_refresh", ["used", "dismissed", "dismissed", "dismissed", "dismissed", "ignored", "replaced", "waiting"])
        history(s, today, "Alex Morgan", "research_refresh", ["used"] * 4, days_ago=90)          # outside the 60-day window
        history(s, today, "Sarah Patel", "research_refresh", ["used", "used", "done", "used", "dismissed"], opp="cedarbay")
        history(s, today, "Sarah Patel", "followup_draft", ["dismissed", "dismissed"], opp="cedarbay", agent="Outreach agent")
        s.flush()
        alex = next(r for r in SC.work(s, today, by_owner=True) if r["owner"] == "Alex Morgan")
        assert (alex["work"], alex["prepared"], alex["used"], alex["dismissed"], alex["ignored"], alex["replaced"], alex["waiting"]) == \
            ("research_refresh", 8, 1, 4, 1, 1, 1)
        assert alex["judged"] == 6 and alex["usefulRate"] == 0.17 and alex["ignoredRate"] == 0.17 and alex["enough"]
        team = {r["work"]: r for r in SC.work(s, today)}
        assert team["research_refresh"]["judged"] == 11 and team["research_refresh"]["used"] == 5
        # a caution only where the owner usually dismisses or ignores the work, and only with enough decisions
        assert "Alex Morgan used 1 of 6 research_refresh results (4 dismissed, 1 left unread" in SC.caution(s, today, "Alex Morgan", "research_refresh")
        assert SC.caution(s, today, "Sarah Patel", "research_refresh") is None   # she uses it
        assert SC.caution(s, today, "Sarah Patel", "followup_draft") is None     # 2 decisions are too few to judge
        assert SC.caution(s, today, None, "research_refresh") is None
        assert [r["work"] for r in SC.for_owner(s, today, "Sarah Patel")] == ["research_refresh"]


def test_evidence_calibration_shows_which_way_reviewers_move_the_agent(app):
    with app.state.db.session() as s:
        today = AU.agent_ctx(app, s).today
        clear(s)
        s.add(m.Note(id="n-cal", opportunity_id="meridian", type="Meeting notes", on=today - timedelta(days=2), text="t", added_by="x"))
        s.add(m.Note(id="n-old", opportunity_id="meridian", type="Meeting notes", on=today - timedelta(days=200), text="t", added_by="x"))

        def prop(key, kind, value, status, final=None, note="n-cal"):
            n = next(ids)
            s.add(m.Proposal(id=f"pr-t{n}", opportunity_id="meridian", note_id=note, kind=kind, key=key, value=value, quote="q",
                             evidence_type="client_statement", status=status, final_value=final))
        for v, st, f in ((2, "edited", 3), (2, "edited", 3), (3, "accepted", 3), (3, "accepted", 3), (3, "rejected", None)):
            prop("N", "score", v, st, f)
        prop("N", "score", 2, "edited", 4, note="n-old")       # outside the window
        prop("N", "score", 2, "pending")                        # not decided yet
        prop("external", "cond", True, "edited", False)         # a condition turned off: lowered
        s.add(m.Evidence(opportunity_id="meridian", kind="score", key="T", value=3, how="manual review", reason="r", by="x", on=today))
        s.add(m.Evidence(opportunity_id="meridian", kind="score", key="N", value=3, how="manual review", reason="r", by="x", on=today))  # proposed before
        s.flush()
        cal = {r["key"]: r for r in SC.evidence(s, today)["keys"]}
        n = cal["N"]
        assert (n["decided"], n["accepted"], n["raised"], n["lowered"], n["rejected"]) == (5, 2, 2, 0, 1) and n["enough"]
        assert any("lower than people do" in f for f in n["findings"])            # 2 of 5 raised
        assert not any("claims more" in f for f in n["findings"])                 # 1 of 5 rejected is below the bar
        assert cal["external"]["lowered"] == 1 and not cal["external"]["enough"]
        assert cal["T"]["byHandNeverProposed"] == 1 and cal["T"]["decided"] == 0
        assert n["byHandNeverProposed"] == 0  # N was entered by hand, but the agent had proposed it


def test_the_scorecard_endpoint_and_reliability(app):
    api = TestClient(app)
    with app.state.db.session() as s:
        today = AU.agent_ctx(app, s).today
        clear(s)
        history(s, today, "Alex Morgan", "research_refresh", ["dismissed"] * 5)
        history(s, today, "Sarah Patel", "research_refresh", ["used"] * 5, opp="cedarbay")
        s.add(m.AgentRun(id="run-t1", agent="Research agent", model="claude:x", prompt_version="v", status="failed", calls=2, on=today))
        s.add(m.AgentRun(id="run-t2", agent="Research agent", model="claude:x", prompt_version="v", status="completed", calls=3, on=today))
        s.commit()
    card = api.get("/api/manager/scorecard", params={"owner": "Alex Morgan"}).json()
    assert card["days"] == 60 and card["minSample"] == 5
    assert [(r["owner"], r["usefulRate"]) for r in card["owners"]] == [("Alex Morgan", 0.0)]
    runs = {r["agent"]: r for r in card["reliability"]["runs"]}
    assert runs["Research agent"]["failed"] == 1 and runs["Research agent"]["aiCalls"] == 5  # demo runs count as runs, not AI calls
    assert {j["kind"] for j in card["reliability"]["jobs"]} >= {"research_refresh"}
    assert api.get("/api/manager/scorecard", params={"days": 0}).status_code == 422


def test_the_manager_sees_the_scorecard_and_is_cautioned_not_refused(app):
    seen = {}

    def triage(case):
        seen["reception"] = case["how_the_owner_received_agent_work"]
        return default_triage(case) + [("delegate", {"kind": "research_refresh", "reason": "Check for news before the workshop."})]

    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=lambda u, h, p, **k: play(p, triage=triage))
    api = TestClient(app)
    api.patch("/api/agents", headers=ME, json={"manager_mode": "on"})
    with app.state.db.session() as s:
        today = AU.agent_ctx(app, s).today
        clear(s)
        # on another of Alex's leads: a refresh done for Meridian itself in the last two weeks would be refused as repeated work
        history(s, today, "Alex Morgan", "research_refresh", ["dismissed", "dismissed", "dismissed", "ignored", "used"], opp="halvorsen")
        s.commit()
    api.post("/api/opportunities/meridian/notes", headers=ME, json={"text": "Short call with Emma Chen; nothing new on the budget.", "analyze": False})
    AU.run_pending(app)
    assert seen["reception"] == [{"work": "research_refresh", "used": 1, "dismissed": 3, "ignored": 1, "useful_rate": 0.2}]
    d = next(d for d in api.get("/api/manager/decisions", params={"scope": "triage"}).json() if d["kind"] == "research_refresh")
    assert d["outcome"] == "queued" and d["detail"].startswith("Caution: In the last 60 days Alex Morgan used 1 of 5")  # cautioned, still its call
    with app.state.db.session() as s:  # the daily review has the same picture
        spec, _ = MA.manager_agent(AU.agent_ctx(app, s), "shadow")
        rows = next(t for t in spec.tools if t.name == "scorecard").run({})["by_owner"]
        assert rows == [{"owner": "Alex Morgan", "work": "research_refresh", "used": 1, "dismissed": 3, "ignored": 1, "useful_rate": 0.2}]
