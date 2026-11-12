"""What the workspace learns from contact plans (phase 3): the scorecard counts how people decided plans and what came of
the approved ones, and the weekly lessons review sees a finding when people reject many plans or skip one kind of step.
Rules only; demo mode."""
from fastapi.testclient import TestClient

from api import lessons as LS
from api import scorecard as SC
from api.main import create_app
from api.settings import Settings

ME = {"X-User": "Alex Morgan"}


def plan(api, oid, decision="approve", note=""):
    api.post(f"/api/opportunities/{oid}/outreach/plan", json={}, headers=ME)
    return api.post(f"/api/opportunities/{oid}/outreach/plan/decision", json={"decision": decision, "note": note}, headers=ME).json()


def sc(api):
    return api.get("/api/manager/scorecard", headers=ME).json()["plans"]


def test_rejected_plans_and_their_reasons_become_a_finding_for_the_outreach_agent(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "l.sqlite3"))
    api = TestClient(app)
    for why in ("Too many steps", "Priya is the wrong person", "We know their CFO", "LinkedIn is not how we work", "Too slow"):
        plan(api, "halvorsen", "reject", why)
    plan(api, "halvorsen", "approve")
    p = sc(api)
    assert p["decided"] == 6 and p["rejected"] == 5 and p["approved"] == 1
    assert "We know their CFO" in p["rejectionNotes"]
    value_first = next(a for a in p["byApproach"] if a["approach"] == "value_first")
    assert value_first["decided"] == 6 and value_first["plans"] == 1 and value_first["running"] == 1
    with app.state.db.session() as s:
        from datetime import date
        f = next(x for x in LS.findings(s, date(2026, 10, 26)) if x["ref"] == "plans:rejected")
        assert f["agent"] == "Outreach agent" and f["numbers"] == {"decided": 6, "approved": 1, "rejected": 5}
        assert "Too slow" in f["reviewer_reasons"]


def test_skipped_steps_and_replies_are_counted_per_channel(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "c.sqlite3"))
    api = TestClient(app)
    for oid in ("halvorsen", "bluegate", "summit"):
        v = plan(api, oid)
        for st in v["plan"]["steps"]:
            if st["channel"] == "email":
                api.post(f"/api/opportunities/{oid}/outreach/plan/steps/{st['n']}", json={"action": "skip", "note": "We email after a call"}, headers=ME)
            else:
                api.post(f"/api/opportunities/{oid}/outreach/plan/steps/{st['n']}", json={"action": "done", "note": "Done"}, headers=ME)
        if oid == "halvorsen":  # the reply comes after the plan's last step: the CGS piece was the last thing done
            api.post(f"/api/opportunities/{oid}/outreach/reply", json={"text": "Thanks for the case, let's talk."}, headers=ME)
    p = sc(api)
    rows = {r["channel"]: r for r in p["byChannel"]}
    assert rows["share_content"]["beforeReply"] == 1 and rows["email"]["skipped"] == 6 and rows["email"]["enough"]
    assert "We email after a call" in rows["email"]["skipNotes"]
    vf = next(a for a in p["byApproach"] if a["approach"] == "value_first")
    assert vf["replied"] == 1 and vf["plans"] == 3
    with app.state.db.session() as s:
        from datetime import date
        refs = [x["ref"] for x in LS.findings(s, date(2026, 10, 26))]
        assert "plans:skipped:email" in refs
        assert LS.numbers(s, date(2026, 10, 26), "plans:skipped:email", SC.WINDOW_DAYS)["skipped"] == 6
