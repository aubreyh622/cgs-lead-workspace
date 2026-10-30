"""Multi-agent step 5: the tool-using next-step agent (Step 7). Tools are called directly, or a fake AI plays the agent."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api import next_step_agent as NS
from api import agent_runtime as R
from api import autonomy as AU
from api import models as m
from api import services as S
from api.llm import make_provider
from api.main import create_app
from api.settings import Settings
from backend.rules import calculate
from agent_fakes import play

ME = {"X-User": "Alex Morgan"}


class AI:
    def __init__(self, actions=None):
        self.actions, self.turns = actions, 0

    def __call__(self, url, headers, payload, **kw):
        self.turns += 1
        return play(payload, actions=self.actions)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("WEB_MODE", raising=False)
    db = tmp_path / "a4.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db))
    app.state.ai = AI()
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=app.state.ai)
    return app


def agent(app, s, opp_id, why=""):
    c = AU.agent_ctx(app, s)
    o = s.get(m.Opportunity, opp_id)
    spec, record = NS.next_step_agent(c, o, calculate(S.rules_input(o)), NS.approver_question(c, o), why)
    return {t.name: t.run for t in spec.tools + [spec.finish]}, record


def test_suggestions_must_close_open_gaps_and_not_repeat_open_tasks(app):
    with app.state.db.session() as s:
        t, record = agent(app, s, "meridian", "No progress for 15 days")
        sit = t["situation"]({})
        assert [g["gap_key"] for g in sit["open_gaps"]] == ["A", "condition:commercial"] and sit["why_you_were_asked"].startswith("No progress")
        assert any(x["title"] == "Confirm the decision-making group" for x in sit["open_tasks"])
        with pytest.raises(R.ToolError, match="not an open gap"):
            t["suggest"]({"gap_key": "B", "title": "Confirm the budget", "reason": "", "question": "Is there a budget?"})
        with pytest.raises(R.ToolError, match="open task already covers"):
            t["suggest"]({"gap_key": "A", "title": "Confirm the decision-making group", "reason": "", "question": "Who decides?"})
        with pytest.raises(R.ToolError, match="fees"):
            t["suggest"]({"gap_key": "A", "title": "Offer a 10% discount to the CFO", "reason": "", "question": "Would that help?"})
        with pytest.raises(R.ToolError, match="at least one"):
            t["finish"]({"summary": "Nothing."})
        assert t["suggest"]({"gap_key": "A", "title": "Ask Emma Chen to introduce the CFO", "reason": "Approver unknown",
                             "question": "Who signs off on the budget?"})["accepted"]
        with pytest.raises(R.ToolError, match="One per gap"):
            t["suggest"]({"gap_key": "A", "title": "Meet the CFO", "reason": "", "question": "When can we meet?"})
        assert record["dropped"] == {"not_open": 1, "repeats_task": 1, "promise": 1, "duplicate": 1}
        assert t["finish"]({"summary": "One action."})


def test_a_quote_must_come_from_the_notes_word_for_word(app):
    with app.state.db.session() as s:
        t, record = agent(app, s, "cedarbay")
        note = t["notes"]({})["notes"][0]
        with pytest.raises(R.ToolError, match="word for word"):
            t["suggest"]({"gap_key": "Q", "title": "Confirm the scope", "reason": "", "question": "What is in scope?",
                          "quote": "The budget is fully approved for everything.", "note_id": note["id"]})
        quote = "Our project budget is approved and covers the initial scope."
        assert t["suggest"]({"gap_key": "Q", "title": "Confirm the scope", "reason": "Budget approved", "question": "What is in scope?",
                             "quote": quote, "note_id": note["id"]})["accepted"]
        assert quote in record["kept"][0]["reason"]


def test_a_returned_pack_puts_the_approvers_question_first(app):
    with app.state.db.session() as s:
        c = AU.agent_ctx(app, s)
        s.add(m.Pack(id="pk-test", opportunity_id="meridian", submitted_by="Alex Morgan", submitted_on=c.today - timedelta(days=2),
                     snapshot={}, rule_version="t", status="returned"))
        s.flush()
        s.add(m.Decision(opportunity_id="meridian", step=8, decision="Returned", by="Quinn Harper", role="Director",
                         note="Who in finance owns the 2027 budget?", pack_id="pk-test", on=c.today - timedelta(days=1)))
        s.flush()
        t, record = agent(app, s, "meridian")
        assert t["situation"]({})["approver_returned_the_pack_with"] == "Who in finance owns the 2027 budget?"
        with pytest.raises(R.ToolError, match="first action must answer"):
            t["suggest"]({"gap_key": "A", "title": "Ask Emma Chen about the CFO", "reason": "", "question": "Who is the CFO?"})
        assert t["suggest"]({"gap_key": "approver", "title": "Find the 2027 budget owner in finance", "reason": "Approver asked",
                             "question": "Who in finance owns the 2027 budget?"})["accepted"]
        assert t["suggest"]({"gap_key": "A", "title": "Ask Emma Chen about the CFO", "reason": "", "question": "Who is the CFO?"})["accepted"]


def test_live_suggestions_run_the_agent_and_save_its_steps(app):
    api = TestClient(app)
    assert api.post("/api/opportunities/meridian/suggestions", headers=ME).json()["suggestions"] == 2
    o = api.get("/api/opportunities/meridian").json()
    assert len(o["suggestions"]) == 2 and all(x["title"].startswith("Ask about") and x["question"] for x in o["suggestions"])
    run = next(r for r in api.get("/api/agent-runs").json() if r["agent"] == "Manager agent")
    assert run["prompt"] == "next-step-agent-1.0" and run["calls"] == app.state.ai.turns == 3
    assert [s["type"] for s in api.get(f"/api/agent-runs/{run['id']}").json()["trace"]][-1] == "finish"


def test_no_open_gaps_means_no_ai_request(app):
    api = TestClient(app)
    assert api.post("/api/opportunities/northstar/suggestions", headers=ME).json()["suggestions"] == 0
    assert app.state.ai.turns == 0


def test_an_agent_that_suggests_nothing_valid_is_a_visible_failure(app):
    app.state.ai = AI(actions=lambda sit: [{"gap_key": "nope", "title": "Something else", "reason": "", "question": "Anything?"}])
    r = TestClient(app).post("/api/opportunities/meridian/suggestions", headers=ME)
    assert r.status_code == 503 and r.json()["code"] == "ai_invalid_output"
    with app.state.db.session() as s:  # the earlier suggestions were not replaced
        assert not list(s.scalars(select(m.Suggestion).where(m.Suggestion.opportunity_id == "meridian", m.Suggestion.status == "open")))


def test_the_patrol_job_passes_its_reason_and_budget(app):
    seen = []

    def plan(sit):
        seen.append(sit["why_you_were_asked"])
        return [{"gap_key": sit["open_gaps"][0]["gap_key"], "title": "Ask Emma Chen who signs off", "reason": "Stalled", "question": "Who signs off?"}]

    app.state.ai = AI(actions=plan)
    api = TestClient(app)
    api.patch("/api/agents", headers=ME, json={"ai_budget": 100})
    with app.state.db.session() as s:
        c = AU.agent_ctx(app, s)
        job = AU.J.enqueue(s, c.today, "gap_actions", "meridian", "patrol", "No progress recorded for 15 days")
        status, text = AU.do_gap_actions(c, job, s.get(m.Opportunity, "meridian"))
        s.commit()
    assert status == "done" and text.startswith("1 suggestion") and seen == ["No progress recorded for 15 days"]


def test_an_amount_repeated_from_the_approver_or_the_notes_is_not_a_promise():
    """Found building the manager's triage: the approver asked "Who signs off above $250K?" and every suggestion that asked it
    back was refused as a price promise, so the next-step agent could never answer the approver."""
    from api.next_step_agent import promises
    q = "Who signs off above $250K?"
    assert not promises(q, (q,))
    assert not promises("Ask how the CFO reviews spend above $300K", ("the CFO would decide on anything above $300K.",))
    assert promises("Offer the work for $200K", (q,))           # a new amount is still refused
    assert promises("Offer a discount on phase one", (q,))
    assert promises("Promise a 15% saving", ())
