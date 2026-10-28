"""Problem-based research direction: the direction agent turns CGS's solutions into problems to watch for; a person approves;
discovery searches problem by problem and tags each candidate; research treats the problem as a hypothesis to check."""
import pytest
from fastapi.testclient import TestClient

from api import agent_runtime as R
from api import direction_agent as DA
from api.knowledge import library
from api.llm import make_provider
from api.main import create_app
from api.settings import Settings
from agent_fakes import play, results, turn

ME = {"X-User": "Alex Morgan"}
PMI = "sol-post-merger-integration-planning"
PMI_QUOTE = ("Clients usually call us when two businesses that now share one owner still run separate ERP systems, separate engineering "
             "processes and separate quality standards, and leadership has promised the board savings that depend on bringing them together.")
GOOD = {"problem": "After a merger the combined company runs two ERP systems and must decide what to integrate first.",
        "signals": ["completes acquisition", "names integration lead"], "buyer_roles": ["Head of Transformation", "COO"],
        "passage_id": f"{PMI}#p2", "quote": PMI_QUOTE}


def tools():
    spec, record = DA.direction_agent({"problem": "x", "signals": [], "buyer_roles": [], "problems": None})
    return {t.name: t.run for t in spec.tools + [spec.finish]}, record


def test_the_solutions_document_is_in_the_library_as_quotable_passages():
    sols = {p.doc_id for p in library().passages if p.type == "solution"}
    assert PMI in sols and len(sols) == 6
    assert all(p.synthetic for p in library().passages if p.type == "solution")


def test_each_problem_must_quote_a_passage_it_read_and_name_observable_signals():
    t, record = tools()
    assert t["library_contents"]({})["documents"][0]["type"] == "solution"  # solutions first
    with pytest.raises(R.ToolError, match="passage you read"):
        t["add_problem"](GOOD)
    t["read_document"]({"doc_id": PMI})
    with pytest.raises(R.ToolError, match="word for word"):
        t["add_problem"]({**GOOD, "quote": "Clients call us when they have two ERP systems after a merger."})
    with pytest.raises(R.ToolError, match="2 to 5 signals"):
        t["add_problem"]({**GOOD, "signals": ["merger"]})
    with pytest.raises(R.ToolError, match="2 to 5 signals"):  # one word is not an event a news article shows
        t["add_problem"]({**GOOD, "signals": ["integration", "acquisition"]})
    assert t["add_problem"](GOOD)["id"] == "p1"
    with pytest.raises(R.ToolError, match="repeats problem p1"):
        t["add_problem"]({**GOOD, "problem": "After a merger the combined company runs two ERP systems and must decide what to integrate."})
    with pytest.raises(R.ToolError, match="at least 2 problems"):
        t["finish"]({"summary": "one"})
    assert record["problems"][0]["solution"] == "Post-merger integration planning"
    assert record["dropped"] == {"not_read": 1, "quote_not_found": 1, "signals": 2, "duplicate": 1}


def test_demo_mode_proposes_problems_and_a_person_approves_them(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "d.sqlite3")))
    r = api.post("/api/direction/problems", headers=ME, json={}).json()
    assert [p["id"] for p in r["problems"]] == ["p1", "p2", "p3"] and all(p["quote"] for p in r["problems"])
    assert api.get("/api/direction").json()["current"]["problems"] == []  # nothing changes until a person approves
    assert api.post("/api/direction/problems", headers=ME, json={}).status_code == 409  # one waiting proposal at a time
    api.post(f"/api/direction/proposals/{r['proposalId']}/approve", headers=ME)
    current = api.get("/api/direction").json()["current"]
    assert [p["solution"] for p in current["problems"]][0] == "Post-merger integration planning"


def test_live_mode_runs_the_direction_agent(tmp_path):
    db = tmp_path / "l.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=lambda u, h, payload, **k: play(payload))
    api = TestClient(app)
    r = api.post("/api/direction/problems", headers=ME, json={}).json()
    assert len(r["problems"]) == 2 and r["problems"][0]["passage_id"].startswith("sol-")
    run = next(x for x in api.get("/api/agent-runs").json() if x["agent"] == "Direction agent")
    assert run["prompt"] == DA.VERSION and run["steps"]


def test_problems_can_be_edited_by_hand_too(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "h.sqlite3")))
    bad = api.post("/api/direction/proposals", headers=ME, json={"field": "problems", "value": [{"problem": "x"}], "rationale": "test"})
    assert bad.status_code == 400
    pid = api.post("/api/direction/proposals", headers=ME, json={"field": "problems", "rationale": "CGS focus for Q1",
                   "value": [{"problem": "Plants run the same work in different ways after a consolidation.",
                              "signals": ["announces regional consolidation"], "buyer_roles": ["COO"]}]}).json()["id"]
    api.post(f"/api/direction/proposals/{pid}/approve", headers=ME)
    assert api.get("/api/direction").json()["current"]["problems"][0]["id"] == "p1"


def test_discovery_tags_each_candidate_with_its_problem_and_research_checks_it(tmp_path, monkeypatch):
    """Approved problems reach discovery; each nomination names its problem; a picked candidate's research is told to check it."""
    monkeypatch.delenv("WEB_MODE", raising=False)
    db = tmp_path / "p.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    tasks = []

    def pick(direction, found):
        assert [p["id"] for p in direction["problems"]] == ["p1", "p2", "p3"]
        r = next(x for x in found if "Arcwell" in x["title"] or "Kessler" in x["title"])
        quote = r["preview"].split(". ")[0]
        return [{"company": "Kessler Motion Controls", "url": r["url"], "why": "Merger completed.", "signal": "Merger", "quote": quote},
                {"company": "Kessler Motion Controls", "url": r["url"], "why": "Merger completed.", "signal": "Merger", "quote": quote,
                 "problem_id": "p1"}]

    def ai(url, headers, payload, **kw):
        tasks.append(payload["contents"][0]["parts"][0]["text"])
        return play(payload, discovery=pick)

    app = create_app(Settings(app_mode="live", db_path=db))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=ai)
    demo = TestClient(create_app(Settings(app_mode="demo", db_path=db)))  # demo mode writes the problems proposal without AI
    pid = demo.post("/api/direction/problems", headers=ME, json={}).json()["proposalId"]
    demo.post(f"/api/direction/proposals/{pid}/approve", headers=ME)
    api = TestClient(app)
    monkeypatch.setattr("api.discovery_agent.MAX_AGENT_SEARCHES", 6)
    r = api.post("/api/candidates/recommend", headers=ME, json={"intent": "merger integration"}).json()
    assert r["checks"]["dropped_no_problem"] == 1 and r["candidates"] == 1  # the first nomination named no problem
    cand = next(c for c in api.get("/api/candidates", params={"status": "new"}).json() if c["round"] == r["round"])
    assert cand["problemId"] == "p1" and "integrate first" in cand["problem"]
    oid = api.post(f"/api/candidates/{cand['id']}/pick", headers=ME).json()["opportunities"][0]
    assert "integrate first" in api.get(f"/api/opportunities/{oid}").json()["targetProblem"]
    assert any("Discovery found it for this problem" in t and "integrate first" in t for t in tasks)
