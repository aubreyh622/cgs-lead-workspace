"""The evidence agent accounts for every dimension and condition after reading notes: proposed, already confirmed, not in
these notes, or unclear, each with one factual sentence. Code checks the account (every key once, agreeing with what was
proposed) and keeps it with the note, so a reviewer can tell "read the notes and found nothing" from "never looked"."""
import pytest

from api import agent_runtime as R
from api import evidence_agent as EA
from api import models as m
from agent_fakes import turn
from test_live_agents import ME, NOTE, FakeAI, live_app, prop

KEYS = EA.CHECKED


def lines(**status):
    return [{"key": k, "status": status.get(k, "not_in_notes"), "reason": f"About {k}."} for k in KEYS]


def opp(scores=None, conditions=None):
    return m.Opportunity(scores=scores or {}, conditions=conditions or {})


def test_every_key_once_and_nothing_else():
    assert KEYS == ["B", "A", "N", "T", "H", "E", "problem", "fit", "external", "next", "commercial"]  # conflict is CGS's call
    assert [x["key"] for x in EA.check_checked(lines(), opp(), set())] == KEYS
    with pytest.raises(R.ToolError, match="missing B"):
        EA.check_checked(lines()[1:], opp(), set())
    with pytest.raises(R.ToolError, match="twice"):
        EA.check_checked(lines() + lines()[:1], opp(), set())
    with pytest.raises(R.ToolError, match="Unknown key 'conflict'"):
        EA.check_checked(lines() + [{"key": "conflict", "status": "not_in_notes", "reason": "x"}], opp(), set())
    with pytest.raises(R.ToolError, match="one of"):
        EA.check_checked(lines(B="maybe"), opp(), set())
    with pytest.raises(R.ToolError, match="short factual sentence"):
        EA.check_checked([{**x, "reason": " "} if x["key"] == "T" else x for x in lines()], opp(), set())


def test_the_account_must_agree_with_what_was_proposed_and_confirmed():
    with pytest.raises(R.ToolError, match="marked B proposed, but no proposal"):
        EA.check_checked(lines(B="proposed"), opp(), set())
    with pytest.raises(R.ToolError, match="You proposed N; mark it proposed"):
        EA.check_checked(lines(), opp(), {"N"})
    with pytest.raises(R.ToolError, match="A is not confirmed yet"):
        EA.check_checked(lines(A="already_confirmed"), opp(), set())
    ok = EA.check_checked(lines(N="proposed", A="already_confirmed", fit="unclear"), opp({"A": 3}), {"N"})
    assert {x["key"]: x["status"] for x in ok}["A"] == "already_confirmed"


def test_code_fills_the_account_when_the_agent_gave_none_and_open_lines_say_what_to_ask():
    rows = {x["key"]: x for x in EA.coverage(opp({"B": 4}, {"external": True}), {"N"})}
    assert rows["N"]["status"] == "proposed" and "ask" not in rows["N"]
    assert rows["B"]["status"] == rows["external"]["status"] == "already_confirmed"
    assert rows["A"]["status"] == "not_in_notes" and rows["A"]["ask"].startswith("Who approves")
    assert rows["next"]["label"] == "Agreed next step"
    # proposed by the agent, but a later check dropped it before review: it is not shown as proposed
    rows = {x["key"]: x for x in EA.coverage(opp(), set(), EA.check_checked(lines(T="proposed"), opp(), {"T"}))}
    assert rows["T"]["status"] == "unclear" and "did not pass" in rows["T"]["reason"] and rows["T"]["ask"]


def test_a_refused_account_goes_back_to_the_agent_and_the_corrected_one_is_kept(tmp_path):
    good = {**prop("cond", "problem", "true", "Our month-end close takes twelve days because of manual reconciliations."), "source": "notes"}
    fixed = [{**x, "reason": "No one says who approves the spend."} if x["key"] == "A" else x for x in lines(problem="proposed")]
    fake = FakeAI(turn(("notes", {})), turn(("propose", good)),
                  turn(("finish", {"summary": "One item.", "checked": lines(problem="proposed")[:5]})),  # incomplete: refused
                  turn(("finish", {"summary": "One item.", "checked": fixed})))
    api = live_app(tmp_path, fake)
    r = api.post("/api/opportunities/alder/notes", json={"text": NOTE}, headers=ME).json()
    rows = {x["key"]: x for x in r["checked"]}
    assert rows["problem"]["status"] == "proposed" and rows["A"]["reason"] == "No one says who approves the spend."
    assert "checked is missing" in str(fake.requests[3])  # the refusal and its reason went back to the agent


def test_an_agent_that_never_gives_a_valid_account_still_leaves_its_proposals_and_a_code_account(tmp_path):
    good = {**prop("cond", "problem", "true", "Our month-end close takes twelve days because of manual reconciliations."), "source": "notes"}
    fake = FakeAI(turn(("notes", {})), turn(("propose", good)), turn(("finish", {"summary": "One item."})))
    api = live_app(tmp_path, fake)
    r = api.post("/api/opportunities/alder/notes", json={"text": NOTE}, headers=ME).json()
    assert r["proposals"] == 1
    note = next(n for n in api.get("/api/opportunities/alder", headers=ME).json()["notes"] if n["text"] == NOTE)
    assert note["analysis"]["checkedBy"] == "code" and len(note["analysis"]["checked"]) == len(KEYS)
    assert {x["key"]: x["status"] for x in note["analysis"]["checked"]}["problem"] == "proposed"


def test_demo_mode_keeps_an_account_too(tmp_path):
    from fastapi.testclient import TestClient
    from api.main import create_app
    from api.settings import Settings
    api = TestClient(create_app(Settings(db_path=tmp_path / "demo.sqlite3")))
    r = api.post("/api/opportunities/alder/notes", headers=ME, json={"text": NOTE}).json()
    o = api.get("/api/opportunities/alder", headers=ME).json()
    proposed = {p["key"] for p in o["proposals"] if p["status"] == "pending" and p["kind"] in ("score", "cond")}
    rows = {x["key"]: x["status"] for x in r["checked"]}
    assert set(rows) == set(KEYS) and {k for k, v in rows.items() if v == "proposed"} <= proposed
