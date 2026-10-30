"""Discovery memory: which searches and signals led to candidates people picked and accepted (api/discovery_memory.py)."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api import discovery_memory as DM
from api import models as m
from test_discovery_agent import DIRECTION, TODAY, Search, nominate
from api import discovery_agent as D

Q_GOOD, Q_BAD = "foundry completes acquisition Ohio", "manufacturer digital strategy news"


def db():
    engine = create_engine("sqlite://")
    m.Base.metadata.create_all(engine)
    return Session(engine)


def cand(s, i, query, signal, status="sent", stage=None, reason=None):
    s.add(m.Candidate(id=f"r1-{i}", round=1, company=f"Company {i}", signal=signal, signal_date="2026-10-01", why="x",
                      status=status, skip_reason=reason, source_query=query))
    if stage:
        s.add(m.Company(id=f"co{i}", name=f"Company {i}"))
        s.add(m.Opportunity(id=f"o{i}", company_id=f"co{i}", title="t", stage=stage, candidate_id=f"r1-{i}"))


def test_counts_what_people_picked_accepted_and_skipped():
    s = db()
    cand(s, 1, Q_GOOD, "Acquisition", stage="contacted")
    cand(s, 2, Q_GOOD, "acquisition ", stage="identified")
    cand(s, 3, Q_GOOD, "Acquisition", stage="rejected")
    cand(s, 4, Q_BAD, "Digital strategy", status="skipped", reason="Too large")
    cand(s, 5, Q_BAD, "Digital strategy", status="skipped", reason="Too large")
    cand(s, 6, Q_BAD, "New CIO", stage="review")   # picked, not yet decided at Step 4
    cand(s, 7, Q_BAD, "New CIO", status="new")     # nobody looked yet: not counted
    s.flush()
    sm = DM.summary(s)
    assert sm["candidates_counted"] == 6 and sm["decided"] == 5
    assert sm["outcomes"] == {"accepted": 2, "rejected": 1, "skipped": 2, "picked": 1}
    good = sm["queries_that_led_to_accepted_leads"]
    assert [q["query"] for q in good] == [Q_GOOD] and good[0]["accepted"] == 2 and good[0]["rejected_at_step_4"] == 1
    assert sm["queries_that_led_to_nothing"] == []  # Q_BAD led to one pick, so it is not "nothing"
    sig = {x["signal"].lower(): x for x in sm["signals"]}
    assert sig["acquisition"]["nominated"] == 3 and sig["digital strategy"]["skipped"] == 2  # labels grouped case-blind
    assert sm["common_skip_reasons"] == [{"reason": "too large", "times": 2}]
    assert DM.for_agent(s)["queries_that_led_to_accepted_leads"][0]["query"] == Q_GOOD


def test_no_hints_until_enough_decisions_and_dead_queries_are_named():
    s = db()
    cand(s, 1, Q_BAD, "Digital strategy", status="skipped", reason="No signal")
    cand(s, 2, Q_BAD, "Digital strategy", status="skipped", reason="No signal")
    s.flush()
    assert DM.for_agent(s) is None  # two decisions are too few
    cand(s, 3, Q_GOOD, "Acquisition", stage="identified")
    s.flush()
    hints = DM.for_agent(s)
    assert [q["query"] for q in hints["queries_that_led_to_nothing"]] == [Q_BAD]


def test_the_agent_sees_the_hints_and_records_which_search_found_each_nomination():
    memory = {"note": "hints", "queries_that_led_to_nothing": [{"query": Q_BAD}]}
    spec, record = D.discovery_agent(Search(), DIRECTION, "", set(), TODAY, memory=memory)
    t = {x.name: x.run for x in spec.tools}
    assert t["direction"]({})["what_worked_before"] == memory
    t["search_news"]({"query": Q_GOOD})
    t["search_news"]({"query": "another search"})  # finds the same pages again: the first search keeps the credit
    t["nominate"](nominate("Ardent Castings", "Ardent Castings to acquire Hollow Pine Foundry."))
    assert record["kept"][0]["source_query"] == Q_GOOD
    spec, _ = D.discovery_agent(Search(), DIRECTION, "", set(), TODAY)
    assert "what_worked_before" not in {x.name: x.run for x in spec.tools}["direction"]({})


def test_picking_links_the_opportunity_to_its_candidate(tmp_path):
    from fastapi.testclient import TestClient
    from api.main import create_app
    from api.settings import Settings
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "w.sqlite3"))
    api = TestClient(app)
    me = {"X-User": "Alex Morgan"}
    cid = next(c["id"] for c in api.get("/api/candidates", headers=me).json() if c["status"] == "new")
    oid = api.post(f"/api/candidates/{cid}/pick", headers=me).json()["opportunities"][0]
    with app.state.db.session() as s:
        assert s.get(m.Opportunity, oid).candidate_id == cid
    mem = api.get("/api/discovery/memory", headers=me).json()
    assert mem["outcomes"].get("picked", 0) + mem["outcomes"].get("accepted", 0) >= 1 and "agentSees" in mem
