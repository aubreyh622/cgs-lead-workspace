"""Step 2 checks: the API starts, serves the frontend, and the rules match Linda's regression cases."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings

ALL_MET = {k: True for k in ("problem", "fit", "external", "next", "commercial", "conflict")}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Settings(db_path=tmp_path / "t.sqlite3")))


def calc(client, **body):
    r = client.post("/api/rules/calculate", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_health_and_config(client):
    assert client.get("/api/health").json()["ok"] is True
    cfg = client.get("/api/config").json()
    assert cfg["appMode"] == "demo" and cfg["crmMode"] == "disabled" and cfg["ruleVersion"] == "1.1-proposed"


def test_docs_and_frontend_are_served(client):
    assert client.get("/docs").status_code == 200
    page = client.get("/")
    assert page.status_code == 200 and "CGS Lead Workspace" in page.text


def test_rules_endpoint_exposes_weights(client):
    rules = client.get("/api/rules").json()
    assert rules["weights"] == {"B": 25, "A": 20, "N": 25, "T": 15, "H": 5, "E": 10}
    assert sum(rules["weights"].values()) == 100


def test_meridian_unknown_authority_keeps_a_range(client):
    r = calc(client, scores={"B": 4, "A": None, "N": 4, "T": 3, "H": 5, "E": 3},
             conditions={**ALL_MET, "commercial": None}, revenue={"low": 600000, "high": 800000})
    assert (r["ql"], r["qu"], r["coverage"]) == (60, 80, 80)
    assert (r["pl"], r["pu"]) == (68, 80)
    assert r["readiness"] == "More evidence needed"


def test_cedar_bay_budget_change_is_explained(client):
    before = {"scores": {"B": 3, "A": 4, "N": 4, "T": 3, "H": 3, "E": 3}, "conditions": ALL_MET, "revenue": {"low": 500000, "high": 700000}}
    after = {**before, "scores": {**before["scores"], "B": 4}}
    d = client.post("/api/rules/delta", json={"before": before, "after": after}).json()
    assert d["before"]["ql"] == 69 and d["after"]["ql"] == 74
    assert d["q"] == 5 and d["p"] == 3
    assert d["after"]["readiness"] == "Ready"


def test_blocker_overrides_high_priority(client):
    r = calc(client, scores={"B": 5, "A": 5, "N": 4, "T": 5, "H": 5, "E": 3},
             conditions={**ALL_MET, "external": False}, blocker="NO_EXTERNAL_SUPPORT",
             revenue={"low": 1200000, "high": 1500000})
    assert r["ql"] == 91 and r["readiness"] == "Not ready" and r["priority"] == "High"


def test_corvane_after_the_call_is_ready_without_revenue(client):
    r = calc(client, scores={"B": 3, "A": 4, "N": 4, "T": 3, "H": 2.5, "E": 4}, conditions=ALL_MET)
    assert r["ql"] == 70.5 and r["readiness"] == "Ready" and r["priority"] == "Pending confirmation"
    assert [g["key"] for g in r["gaps"]] == ["revenue"]


@pytest.mark.parametrize("body", [
    {"scores": {"B": 7}},
    {"scores": {"B": 2.5}},
    {"scores": {"Z": 3}},
    {"blocker": "MADE_UP"},
    {"revenue": {"low": 700000, "high": 500000}},
])
def test_invalid_input_is_rejected(client, body):
    assert client.post("/api/rules/calculate", json=body).status_code == 422


def test_bad_mode_is_refused():
    with pytest.raises(ValueError):
        Settings(app_mode="sometimes")
