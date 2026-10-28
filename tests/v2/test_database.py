"""Step 3 checks: records are stored, survive a restart, and match the prototype's sample."""
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings


def make(tmp_path, mode="demo"):
    return TestClient(create_app(Settings(app_mode=mode, db_path=tmp_path / "ws.sqlite3")))


@pytest.fixture
def client(tmp_path):
    return make(tmp_path)


def test_demo_sample_is_loaded(client):
    stats = client.get("/api/stats").json()
    assert stats["opportunities"] == 14 and stats["companies"] == 14
    assert stats["candidates"] == 10 and stats["feedback"] == 7
    assert stats["direction_versions"] == 3 and stats["packs"] == 1


def test_data_survives_a_restart(tmp_path):
    first = make(tmp_path)
    first.app.state.db.engine.dispose()
    second = make(tmp_path)
    assert len(second.get("/api/opportunities").json()) == 14
    assert second.get("/api/stats").json()["opportunities"] == 14  # not seeded twice


def test_live_mode_starts_empty(tmp_path):
    assert make(tmp_path, "live").get("/api/opportunities").json() == []


def test_list_filters_and_rules_results(client):
    active = client.get("/api/opportunities", params={"stage": "active"}).json()
    assert {o["id"] for o in active} == {"meridian", "cedarbay", "atlas", "alder", "harbour"}
    cedar = next(o for o in active if o["id"] == "cedarbay")
    assert cedar["assessment"]["ql"] == 69 and cedar["assessment"]["readiness"] == "More evidence needed"
    review = client.get("/api/opportunities", params={"stage": "review"}).json()
    assert all(o["assessment"] is None for o in review)


def test_opportunity_detail_has_every_part(client):
    o = client.get("/api/opportunities/cedarbay").json()
    assert o["company"]["name"] == "Cedar Bay Logistics"
    assert o["proposals"][0]["key"] == "B" and o["proposals"][0]["status"] == "pending"
    assert o["notes"][0]["id"] == "n-cb"
    m = client.get("/api/opportunities/meridian").json()
    assert len(m["tasks"]) == 2 and len(m["evidence"]) == 2
    assert m["scores"]["A"] is None  # pending stays null, never zero


def test_unknown_contact_is_stored_as_null(client):
    w = client.get("/api/opportunities/westbrook").json()
    assert w["contact"]["name"] is None and w["research"]["progress"] == 1


def test_northstar_pack_is_out_of_date(client):
    n = client.get("/api/opportunities/northstar").json()
    pack = n["packs"][0]
    assert pack["staleReason"] and pack["snapshot"]["revenue"] == {"low": 600000, "high": 600000}
    assert n["revenue"] == {"low": 500000, "high": 700000}


def test_direction_history_is_complete(client):
    d = client.get("/api/direction").json()
    assert d["current"]["version"] == "v3" and [v["version"] for v in d["versions"]] == ["v3", "v2", "v1"]
    assert "Product or plant expansion" not in d["versions"][1]["signals"]
    assert {p["id"]: p["status"] for p in d["proposals"]} == {"p1": "approved", "p2": "pending"}


def test_unmeasured_review_time_stays_null(client):
    fb = {f["id"]: f for f in client.get("/api/feedback").json()}
    assert fb["fb3"]["minutes"] is None and fb["fb1"]["minutes"] == 4


def test_missing_opportunity_is_404(client):
    assert client.get("/api/opportunities/nope").status_code == 404


def test_reset_restores_the_sample_in_demo_only(client, tmp_path):
    me = {"X-User": "Alex Morgan"}
    assert client.post("/api/demo/reset").status_code == 401  # a plain cross-site form post carries no X-User
    assert client.post("/api/demo/reset", headers=me).json() == {"ok": True, "by": "Alex Morgan"}
    assert client.get("/api/stats").json()["opportunities"] == 14
    assert make(tmp_path / "live", "live").post("/api/demo/reset", headers=me).status_code == 403


def test_only_local_pages_can_reach_the_workspace(client):
    me = {"X-User": "Alex Morgan"}
    assert client.get("/api/health", headers={"Host": "evil.example:8800"}).status_code == 403  # DNS rebinding
    assert client.get("/api/health", headers={"Host": "localhost:8800"}).status_code == 200
    assert client.post("/api/opportunities/marlow/research/stop", headers={**me, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/opportunities/marlow/research/stop", headers={**me, "Origin": "http://127.0.0.1:8800"}).status_code == 200


def test_demo_mode_refuses_a_live_database(tmp_path):
    from api.main import create_app
    from api.settings import Settings
    db = tmp_path / "shared.sqlite3"
    create_app(Settings(app_mode="live", db_path=db)).state.db.engine.dispose()
    with pytest.raises(RuntimeError, match="live-mode records"):
        create_app(Settings(app_mode="demo", db_path=db))
    # the other way round is allowed (live AI on the synthetic sample), and the workspace says so
    create_app(Settings(app_mode="demo", db_path=tmp_path / "demo-first.sqlite3")).state.db.engine.dispose()
    live_on_demo = create_app(Settings(app_mode="live", db_path=tmp_path / "demo-first.sqlite3"))
    assert TestClient(live_on_demo).get("/api/config").json()["demoData"] is True
