"""Team instructions (api/team_text.py): CGS staff write how CGS works in their own words; each section joins the prompts
of the agents that read it. Every save is a new revision, a stale save is refused, only admins may save, and every
agent run records the revision it read."""
from datetime import date

from fastapi.testclient import TestClient

from api import lessons as LS
from api.main import create_app
from api.settings import Settings
from agent_fakes import turn
from test_evidence_checklist import lines
from test_live_agents import NOTE, FakeAI, live_app, prop

ME = {"X-User": "Alex Morgan"}
SARAH = {"X-User": "Sarah Patel"}
WRITING = "Sign off as 'The CGS team'. Never promise a savings figure."
QUALIFYING = "A budget counts only when the client names who holds it."


def demo(tmp_path, **settings):
    app = create_app(Settings(db_path=tmp_path / "team.sqlite3", **settings))
    return app, TestClient(app)


def save(api, sections, base, who=ME, ok=200, reason=""):
    r = api.put("/api/team-text", headers=who, json={"sections": sections, "baseRevision": base, "reason": reason})
    assert r.status_code == ok, r.text
    return r.json()


def test_every_save_is_a_new_revision_and_old_ones_can_come_back(tmp_path):
    app, api = demo(tmp_path)
    first = api.get("/api/team-text", headers=ME).json()
    assert first["current"] is None and first["canChange"] and [s["key"] for s in first["sectionsMeta"]] == ["writing", "qualifying", "researching"]
    assert save(api, {}, None, ok=400)["code"] == "invalid"  # nothing written
    r1 = save(api, {"writing": WRITING}, None, reason="First version")
    assert r1["revision"] == 1 and r1["sections"] == {"writing": WRITING, "qualifying": "", "researching": ""} and r1["savedBy"] == "Alex Morgan"
    assert save(api, {"writing": WRITING + "  \r\n"}, 1)["revision"] == 1  # the same text again: no new revision
    r2 = save(api, {"writing": WRITING, "qualifying": QUALIFYING}, 1)
    assert r2["revision"] == 2 and r2["baseRevision"] == 1
    back = api.post("/api/team-text/1/restore", headers=ME, json={"baseRevision": 2}).json()
    assert back["revision"] == 3 and back["origin"] == "restore" and back["sections"]["qualifying"] == ""
    hist = api.get("/api/team-text/history", headers=ME).json()
    assert [h["revision"] for h in hist] == [3, 2, 1] and hist[2]["reason"] == "First version"  # nothing is ever changed


def test_a_save_over_someone_elses_newer_revision_is_refused(tmp_path):
    app, api = demo(tmp_path)
    save(api, {"writing": WRITING}, None)
    save(api, {"writing": WRITING + " Keep it short."}, 1, who=SARAH)
    r = save(api, {"writing": "Mine"}, 1, ok=409)
    assert r["code"] == "stale" and "Sarah Patel saved revision 2 since you started from revision 1" in r["error"]
    assert api.get("/api/team-text", headers=ME).json()["current"]["sections"]["writing"].endswith("Keep it short.")


def test_bad_text_is_refused(tmp_path):
    app, api = demo(tmp_path)
    assert "Unknown section tone" in save(api, {"tone": "x"}, None, ok=400)["error"]
    assert "limit is 2000" in save(api, {"writing": "x" * 2001}, None, ok=400)["error"]


def test_only_admins_may_save_when_admins_are_named(tmp_path):
    app, api = demo(tmp_path, admins=("Sarah Patel",))
    assert api.get("/api/team-text", headers=ME).json()["canChange"] is False
    assert save(api, {"writing": WRITING}, None, ok=403)["code"] == "forbidden"
    assert save(api, {"writing": WRITING}, None, who=SARAH)["revision"] == 1


def test_each_agent_reads_only_its_sections_before_its_lessons(tmp_path):
    app, api = demo(tmp_path)
    save(api, {"writing": WRITING, "qualifying": QUALIFYING, "researching": "Trust company filings over news."}, None)
    with app.state.db.session() as s:
        outreach, evidence, manager = (LS.block(s, a) for a in ("Outreach agent", "Evidence agent", "Manager agent"))
        research, discovery, direction = (LS.block(s, a) for a in ("Research agent", "Discovery agent", "Direction agent"))
    assert WRITING in outreach and QUALIFYING not in outreach
    assert QUALIFYING in evidence and QUALIFYING in manager and WRITING not in evidence
    assert "filings" in research and "filings" in discovery and direction == ""
    assert "revision 1" in outreach and "never change the hard rules" in outreach  # style yes, the hard rules and the code no
    with app.state.db.session() as s:
        lesson = LS.propose(s, date(2026, 10, 26), "Outreach agent", "Mention the client's own words.", "person", "Seen in reviews.", "Alex Morgan")
        lesson.status = "approved"
        both = LS.block(s, "Outreach agent")
    assert both.index(WRITING) < both.index("Mention the client's own words.")  # team text first, then lessons


def test_every_agent_run_records_the_revision_it_read(tmp_path):
    app, api = demo(tmp_path)
    save(api, {"qualifying": QUALIFYING}, None)
    api.post("/api/opportunities/alder/notes", headers=ME, json={"text": NOTE})
    run = api.get("/api/agent-runs", headers=ME).json()[0]
    assert run["agent"] == "Evidence agent" and run["teamRevision"] == 1


def test_the_live_evidence_agent_gets_the_text_in_its_prompt(tmp_path):
    good = {**prop("cond", "problem", "true", "Our month-end close takes twelve days because of manual reconciliations."), "source": "notes"}
    fake = FakeAI(turn(("notes", {})), turn(("propose", good)), turn(("finish", {"summary": "One item.", "checked": lines(problem="proposed")})))
    api = live_app(tmp_path, fake)
    save(api, {"qualifying": QUALIFYING, "writing": WRITING}, None)
    api.post("/api/opportunities/alder/notes", json={"text": NOTE}, headers=ME)
    system = fake.requests[0][2]["systemInstruction"]["parts"][0]["text"]
    assert QUALIFYING in system and WRITING not in system and "Instructions from the CGS team (revision 1)" in system
