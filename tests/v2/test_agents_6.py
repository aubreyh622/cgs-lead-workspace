"""Step 6a/6b checks: the AI exam grader, outreach drafts (A1), next actions (A4), the feedback agent and the progress monitor."""
import json

import pytest
from fastapi.testclient import TestClient

from api.ai_exam import EXAM, grade, run
from api.live_agents import check_draft
from api.llm import LLMError, make_provider
from api.main import create_app
from api.settings import Settings
from agent_fakes import turn

ME = {"X-User": "Alex Morgan"}
CASES = json.loads(EXAM.read_text(encoding="utf-8"))["cases"]


class FakeAI:
    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, url, headers, payload, **kw):
        self.requests.append(payload)
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]  # the last answer repeats
        if "candidates" in a and "usageMetadata" in a:  # already a full response (a tool-using agent's turn)
            return a
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(a)}]}}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}


def live(tmp_path, fake):
    create_app(Settings(app_mode="demo", db_path=tmp_path / "x.sqlite3")).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=tmp_path / "x.sqlite3"))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=fake)
    return TestClient(app)


def p(kind, key, value):
    return {"kind": kind, "key": key, "value": value}


# ---------- the exam grader ----------
def test_exam_cases_are_gradable():
    assert [c["id"] for c in CASES] == ["corvane-call", "meridian-partial", "bluegate-decline", "summit-courtesy", "harrow-shared", "dunmore-defer"]
    g = grade(CASES[5], [p("blocker", "NO_EXTERNAL_SUPPORT", "NO_EXTERNAL_SUPPORT"), p("cond", "external", False)])
    assert g["over"] == ["NO_EXTERNAL_SUPPORT=NO_EXTERNAL_SUPPORT", "external=False"] and g["missed"] == ["PROJECT_DEFERRED"]


def test_exam_checks_completed_actions_and_support_scope():
    harrow = CASES[4]
    g = grade(harrow, [p("score", "E", 2), p("score", "N", 4), p("cond", "commercial", True)])
    assert "E=2 (low)" in g["missed"] and "N=4" in g["hit"] and g["over"] == ["commercial=True"]
    assert grade(harrow, [p("score", "E", 4)])["over"] == ["E=4 (max 3)"]


def test_grader_rewards_caution_and_flags_over_claims():
    corvane = CASES[0]
    g = grade(corvane, [p("score", "B", 3), p("score", "E", 4), p("cond", "problem", True), p("score", "N", 2), p("cond", "fit", True)])
    assert g["hit"] == ["B=3", "problem=True", "fit=True"]
    assert g["over"] == ["E=4 (max 3)"]
    assert "N=2 (low)" in g["missed"] and "A" in g["missed"]
    assert g["extra"] == []
    courtesy = CASES[3]
    g = grade(courtesy, [p("cond", "next", True), p("score", "E", 1)])
    assert g["over"] == ["next=True"] and g["extra"] == ["E=1"]
    decline = CASES[2]
    g = grade(decline, [p("blocker", "NO_EXTERNAL_SUPPORT", "NO_EXTERNAL_SUPPORT"), p("cond", "external", True)])
    assert g["hit"] == ["NO_EXTERNAL_SUPPORT"] and g["wrong"] == ["external=True"] and g["over"] == ["external=True"]


def test_exam_runs_against_a_provider():
    answer = {"proposals": [{"kind": "cond", "key": "problem", "value": "true", "quote": "We have decided to run the ERP consolidation internally", "why": "", "evidence_type": "client_statement"}]}
    fake = FakeAI(*[answer] * len(CASES))
    out = run(make_provider("gemini", "m", key="k", transport=fake), CASES)
    assert out["totals"]["errors"] == 0 and len(fake.requests) == len(CASES)
    # the quote only exists in the Bluegate notes; elsewhere the code checks drop it
    assert out["results"][2]["grade"]["over"] == []


# ---------- A1 ----------
def test_draft_checks():
    ids, facts = {"f1", "f2"}, "Corvane completed its acquisition of Arden Controls."
    check_draft("Hello", "Hi Elena, [[f1|after the Arden acquisition]], open to a call? Alex, CGS Advisors", ids, facts)
    with pytest.raises(LLMError, match="does not cite"):
        check_draft("Hello", "Hi Elena, open to a call?", ids, facts)
    with pytest.raises(LLMError, match="not in the brief"):
        check_draft("Hello", "Hi [[f1|after Arden]] and [[f9|something]]", ids, facts)
    with pytest.raises(LLMError, match="promise"):
        check_draft("Hello", "Hi [[f1|after Arden]], we guarantee a 30% saving for $50K.", ids, facts)


def test_demo_and_live_drafts_through_the_api(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "d.sqlite3")))
    d = api.post("/api/opportunities/halvorsen/outreach/generate", json={"tone": "brief"}, headers=ME).json()
    assert "[[f1|" in d["body"] and api.get("/api/opportunities/halvorsen").json()["outreach"]["status"] == "draft"

    good = {"subject": "Your new Ohio foundry", "body": "Hi Priya, I saw that [[f1|Halvorsen opened a third foundry in Ohio]]. Open to 20 minutes? Alex Morgan, CGS Advisors"}
    bad = {"subject": "x", "body": "Hi Priya, we can save you 20% for a $40K fee."}
    fake = FakeAI(turn(("brief", {}), ("history", {})), turn(("submit_draft", bad)), turn(("submit_draft", good)))
    api2 = live(tmp_path / "l", fake)
    r = api2.post("/api/opportunities/halvorsen/outreach/generate", json={}, headers=ME)
    assert r.status_code == 200 and r.json()["subject"] == "Your new Ohio foundry"  # the rejected draft went back and was fixed
    assert "does not cite any fact" in json.dumps(fake.requests[2]["contents"][-1])
    api3 = live(tmp_path / "f", FakeAI(turn(("brief", {})), turn(("submit_draft", bad))))  # never fixes it
    r = api3.post("/api/opportunities/halvorsen/outreach/generate", json={}, headers=ME)
    assert r.status_code == 503 and r.json()["code"] == "ai_invalid_output" and "does not cite any fact" in r.json()["error"]


# ---------- A4 ----------
def test_live_next_actions_keep_only_open_gaps(tmp_path):
    answer = {"actions": [
        {"gap_key": "A", "title": "Ask who signs off above Oliver", "reason": "Authority pending", "question": "Who approves?"},
        {"gap_key": "A", "title": "Duplicate gap", "reason": "", "question": ""},
        {"gap_key": "B", "title": "Confirm the funding path", "reason": "Budget pending", "question": "Is there budget?"},
    ]}
    api = live(tmp_path, FakeAI(answer))
    assert api.post("/api/opportunities/alder/suggestions", headers=ME).json()["suggestions"] == 2
    titles = [s["title"] for s in api.get("/api/opportunities/alder").json()["suggestions"]]
    assert sorted(titles) == ["Ask who signs off above Oliver", "Confirm the funding path"]


# ---------- feedback agent ----------
def test_live_feedback_agent_needs_three_supporting_records(tmp_path):
    weak = {"summary": "Two rejections mention authority.", "propose": True, "field": "buyer_roles", "after_list": ["Chief Digital Officer"],
            "after_text": "", "title": "Narrow roles", "rationale": "x", "feedback_ids": ["fb4", "fb5"]}
    strong = dict(weak, feedback_ids=["fb4", "fb5", "fb6"], after_list=["Chief Digital Officer", "Chief Information Officer", "Chief Strategy Officer",
                                                                          "Head of Transformation", "VP Operations"], title="Drop IT Manager")
    api = live(tmp_path, FakeAI(weak, strong))
    api.post("/api/direction/proposals/p2/reject", headers=ME)  # clear the seeded pending proposal so a new one can be created
    r = api.post("/api/direction/analyze", headers=ME).json()
    assert r["proposalId"] is None and "discarded" in r["summary"]
    r = api.post("/api/direction/analyze", headers=ME).json()
    assert r["proposalId"]
    prop = next(x for x in api.get("/api/direction").json()["proposals"] if x["id"] == r["proposalId"])
    assert prop["origin"] == "ai" and prop["feedbackIds"] == ["fb4", "fb5", "fb6"] and prop["status"] == "pending"


# ---------- progress monitor ----------
def test_alerts(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "a.sqlite3")))
    alerts = api.get("/api/alerts", headers=ME).json()
    kinds = {(a["opportunityId"], a["type"]) for a in alerts}
    assert ("summit", "follow_up_due") in kinds and ("summit", "overdue") in kinds
    assert ("northstar", "stale_pack") in kinds and ("alder", "stalled") in kinds
    api.post("/api/opportunities/alder/snooze", headers=ME)
    assert ("alder", "stalled") not in {(a["opportunityId"], a["type"]) for a in api.get("/api/alerts", headers=ME).json()}
