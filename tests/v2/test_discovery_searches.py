"""B2: automatic searches. The main search replaces the fixed weekly round; people add their own, each with how often
and how many companies a run may find. The daily check runs whichever are due. Demo mode: the prepared candidate pool."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from api import autonomy as AU
from api import discovery_agent as D
from api import discovery_searches as DS
from api import models as m
from api.main import create_app
from api.settings import Settings
from test_discovery_agent import DIRECTION, TODAY, Search, nominate

ME = {"X-User": "Alex Morgan"}


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(app_mode="demo", db_path=tmp_path / "ds.sqlite3"))


def patrol_kinds(api):
    return [j for j in api.post("/api/agents/patrol", headers=ME).json()["queued"] if j["kind"] == "discovery_round"]


def test_the_main_search_replaces_the_weekly_round(app):
    api = TestClient(app)
    main = api.get("/api/discovery/searches").json()
    assert [(x["id"], x["main"], x["freq"], x["perRun"], x["on"]) for x in main] == [("main", True, "week", 10, True)]
    queued = patrol_kinds(api)
    assert len(queued) == 1 and "What we look for (main search)" in queued[0]["reason"]
    ran = AU.run_pending(app)
    job = next(r for r in ran if r["kind"] == "discovery_round")
    assert job["status"] == "done" and "candidates" in job["result"]
    main = api.get("/api/discovery/searches").json()[0]
    assert main["lastRunOn"] == main["lastRound"]["on"] and main["found"] == main["lastRound"]["found"] > 0
    assert main["nextRunOn"] == (date.fromisoformat(main["lastRunOn"]) + timedelta(days=7)).isoformat()
    api.post("/api/demo/advance-days?days=3", headers=ME)
    assert patrol_kinds(api) == []  # not due again for a week
    api.post("/api/demo/advance-days?days=4", headers=ME)
    assert len(patrol_kinds(api)) == 1


def test_people_add_edit_switch_off_and_delete_searches(app):
    api = TestClient(app)
    bad = api.post("/api/discovery/searches", headers=ME, json={"name": "CDOs", "query": "new CDO", "freq": "week"})
    assert bad.status_code == 400 and "researcher" in bad.json()["error"]
    assert api.post("/api/discovery/searches", headers=ME, json={"name": "Daily", "query": "Manufacturers with a new CIO this year",
                                                                  "freq": "day", "weekday": 2}).status_code == 400  # no weekday for daily
    assert api.post("/api/discovery/searches", json={"name": "No one", "query": "Manufacturers with a new CIO"}).status_code == 401
    x = api.post("/api/discovery/searches", headers=ME, json={"name": "New Chief Digital Officers", "freq": "2weeks", "weekday": 2,
                                                               "query": "Industrial companies that named a new CDO or CIO in the last 90 days",
                                                               "per_run": 3}).json()
    assert x["createdBy"] == "Alex Morgan" and x["weekdayLabel"] == "Wednesday" and x["perRun"] == 3
    nxt = date.fromisoformat(x["nextRunOn"])
    assert nxt.weekday() == 2 and 0 <= (nxt - date.fromisoformat(x["createdOn"])).days < 7  # never run: the next Wednesday
    x = api.patch(f"/api/discovery/searches/{x['id']}", headers=ME, json={"weekday": -1, "freq": "day", "per_run": 2}).json()
    assert x["weekday"] is None and x["freq"] == "day" and x["nextRunOn"] == x["createdOn"]
    assert api.patch("/api/discovery/searches/main", headers=ME, json={"name": "Renamed", "on": False}).json()["name"] == DS.MAIN_NAME
    names = [j["reason"] for j in patrol_kinds(api)]
    assert names == ["Automatic search: New Chief Digital Officers"]  # the main search is off
    ran = AU.run_pending(app)
    assert any(r["kind"] == "discovery_round" and "New Chief Digital Officers, round" in r["result"] for r in ran)
    after = next(s for s in api.get("/api/discovery/searches").json() if s["id"] == x["id"])
    assert 0 < after["found"] <= 2  # at most the companies per run
    item = next(i for i in api.get("/api/inbox?status=all").json() if i["kind"] == "candidates")
    assert item["owner"] == "Alex Morgan" and "New Chief Digital Officers" in item["title"]
    assert api.delete("/api/discovery/searches/main", headers=ME).status_code == 409
    assert api.delete(f"/api/discovery/searches/{x['id']}", headers=ME).json() == {"ok": True}
    assert [s["id"] for s in api.get("/api/discovery/searches").json()] == ["main"]


def test_ask_now_takes_how_many(app):
    api = TestClient(app)
    res = api.post("/api/candidates/recommend", headers=ME, json={"intent": "", "count": 3}).json()
    assert res["candidates"] == 3
    assert api.post("/api/candidates/recommend", headers=ME, json={"count": 11}).status_code == 422


def test_weekday_scheduling():
    x = m.DiscoverySearch(id="t", name="t", query="q", freq="week", weekday=0, per_run=5, on=True, main=False,
                          created_on=date(2026, 11, 4), rounds=[], last_run_on=date(2026, 11, 2))  # a Monday
    assert DS.next_run(None, x, date(2026, 11, 5)) == date(2026, 11, 9)
    x.freq, x.last_run_on = "4weeks", date(2026, 11, 4)  # run on a Wednesday: next is the Monday after four weeks
    assert DS.next_run(None, x, date(2026, 11, 5)) == date(2026, 12, 7)
    x.on = False
    assert DS.next_run(None, x, date(2026, 11, 5)) is None


def test_the_agent_stops_at_the_rounds_limit():
    spec, record = D.discovery_agent(Search(), DIRECTION, "", set(), TODAY, limit=1)
    t = {x.name: x.run for x in spec.tools}
    t["search_news"]({"query": "acquisition"})
    t["nominate"](nominate("Ardent Castings", "Ardent Castings to acquire Hollow Pine Foundry."))
    from api import agent_runtime as R
    with pytest.raises(R.ToolError, match="1 nomination is the limit"):
        t["nominate"](nominate("Kestrel Foods", "Kestrel Foods closes the acquisition of two snack brands.", url="https://news.example/kestrel"))
    assert record["dropped"] == {"over_limit": 1}
