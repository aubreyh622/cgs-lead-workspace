"""The CGS email standard (api/email_rubric.py, config/email_rubric.json): one rubric the outreach agent writes to, its
reviewer checks drafts against, and the quality check scores with. Only an admin changes it; every change is a version."""
from fastapi.testclient import TestClient

from api import email_rubric as ER
from api.main import create_app
from api.settings import Settings

ALEX = {"X-User": "Alex Morgan"}
SARAH = {"X-User": "Sarah Patel"}


def make(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "rubric.sqlite3", admins=("Alex Morgan",)))
    return app, TestClient(app)


def test_the_built_in_standard_is_whole_and_its_examples_score_every_line():
    r = ER.built_in()
    assert sum(c["weight"] for c in r["criteria"]) == 100
    keys = [c["key"] for c in r["criteria"]]
    assert all(set(e["expected"]) == set(keys) for e in r["examples"]["emails"])
    # the team's own expected scores: strong meets the bar, typical and poor do not
    meets = {e["id"]: ER.meets(e["expected"], r) for e in r["examples"]["emails"]}
    assert meets == {"strong": True, "typical": False, "poor": False}
    text = ER.as_text(ER.current())
    assert "CGS email standard (built-in-1)" in text and "Looks like a 2" in text and "90-140 words" in text


def test_weighted_mean_and_bar():
    r = ER.built_in()
    s = {"hook": 4, "insight": 3, "specific": 4, "ask": 5, "voice": 4}
    assert ER.weighted(s, r) == 3.9 and ER.meets(s, r)
    assert not ER.meets({**s, "voice": 2}, r)  # one line below 3 fails whatever the mean


def test_only_an_admin_changes_the_standard_and_each_change_is_a_version(tmp_path):
    app, api = make(tmp_path)
    cur = api.get("/api/email-rubric", headers=SARAH).json()
    assert cur["canChange"] is False and cur["current"]["version"] == "built-in-1" and cur["history"] == []
    crit = cur["current"]["criteria"]
    new = {"criteria": [{**c, "weight": w} for c, w in zip(crit, (15, 30, 20, 15, 20))], "bar": {"mean": 3.8, "each": 3},
           "review": {"rounds": 2}, "reason": "Insight matters most to CGS"}
    assert api.put("/api/email-rubric", headers=SARAH, json=new).json()["code"] == "not_admin"
    bad = api.put("/api/email-rubric", headers=ALEX, json={**new, "criteria": new["criteria"][:4]}).json()
    assert bad["code"] == "invalid" and "add up to 100; these add up to 80" in bad["error"]
    clash = api.put("/api/email-rubric", headers=ALEX, json={**new, "criteria": [*new["criteria"][:4], {**crit[4], "key": "fixes", "weight": 20}]}).json()
    assert clash["code"] == "invalid"
    assert api.put("/api/email-rubric", headers=ALEX, json={**new, "review": {"rounds": 5}}).json()["code"] == "invalid"
    assert api.put("/api/email-rubric", headers=ALEX, json={**new, "reason": ""}).json()["code"] == "no_reason"
    saved = api.put("/api/email-rubric", headers=ALEX, json=new).json()
    assert saved["current"]["version"] == "email-1" and saved["current"]["review"]["rounds"] == 2
    assert saved["changed"]["by"] == "Alex Morgan" and saved["history"][0]["by"] == "Built in"
    with app.state.db.session() as s:
        assert ER.current(s)["criteria"][1]["weight"] == 30 and ER.current(s)["bar"]["mean"] == 3.8
    assert api.put("/api/email-rubric", headers=ALEX, json=new).json()["code"] == "unchanged"
    # a new criterion without a key gets one from its name
    added = {**new, "criteria": [*[{**c, "weight": 15} for c in new["criteria"][:4]], {**crit[4], "weight": 20},
                                 {"label": "Credibility", "weight": 20, "question": "Does it give one plain reason to believe CGS can help?"}],
             "reason": "Add credibility"}
    r = api.put("/api/email-rubric", headers=ALEX, json=added).json()
    assert r["current"]["version"] == "email-2" and r["current"]["criteria"][-1]["key"] == "credibility"
    back = api.put("/api/email-rubric", headers=ALEX, json={"builtIn": True, "reason": "Back to the start"}).json()
    assert back["current"]["version"] == "email-3-built-in" and len(back["current"]["criteria"]) == 5
    assert [h["version"] for h in back["history"]] == ["email-2", "email-1", "built-in-1"]
