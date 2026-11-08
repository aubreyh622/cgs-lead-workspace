"""The home page's to-do list (GET /api/todo): grouped by what the person must do, one row per piece of work, honest
counts, buttons that act on the row, and workspace problems only when there is one."""
from datetime import date

from api import autonomy as AU
from api import jobs as J
from api import models as m
from api import undo as U
from tests.v2.test_undo import TO, approve, ws  # noqa: F401  (ws is a fixture)

ALEX = {"X-User": "Alex Morgan"}
SARAH = {"X-User": "Sarah Patel"}
ORDER = ["problem", "waiting", "decide", "check", "steer"]


def todo(api, who=ALEX):
    r = api.get("/api/todo", headers=who)
    assert r.status_code == 200
    return r.json()


def rows(t):
    return [it for g in t["groups"] for it in g["items"]]


def test_groups_are_in_order_counts_are_honest_and_nothing_is_listed_twice(ws):
    app, api, oid = ws
    for who in (ALEX, SARAH):
        t = todo(api, who)
        ids = [g["id"] for g in t["groups"]]
        assert ids == [g for g in ORDER if g in ids]
        assert all(g["count"] == len(g["items"]) > 0 for g in t["groups"])  # an empty group is not shown
        assert t["count"] == len(rows(t))
        keys = [it["key"] for it in rows(t)]
        assert len(keys) == len(set(keys))
        assert t["health"] == []  # a quiet demo workspace has nothing wrong with it
    assert api.get("/api/todo").status_code == 401  # someone must be acting


def test_one_row_per_piece_of_work(ws):
    app, api, oid = ws
    t = todo(api, SARAH)
    by_opp = {}
    for it in rows(t):
        if it["label"] in ("Overdue", "Follow up") and it["opportunity"]:
            by_opp.setdefault(it["opportunity"]["id"], []).append(it["label"])
    assert by_opp and all(len(v) == 1 for v in by_opp.values())  # an overdue follow-up is not also "follow up"
    # an agent's follow-up draft replaces the "no reply yet" row; its row is the draft to review and send
    follow = [it for it in rows(todo(api, SARAH)) if it["label"] == "Follow up"]
    with app.state.db.session() as s:
        for it in follow:
            o = s.get(m.Opportunity, it["opportunity"]["id"])
            J.to_inbox(s, AU.agent_ctx(app, s).today, None, "draft", f"Follow-up draft for {o.company.name}", "Hello", opp=o, agent="Outreach agent")
    after = rows(todo(api, SARAH))
    assert not [it for it in after if it["label"] == "Follow up"]
    assert len([it for it in after if it["label"] == "Review and send"]) == len(follow)


def test_an_agents_ready_note_replaces_the_rules_ready_row(ws, monkeypatch):
    from sqlalchemy import select
    from api import todo as TD
    app, api, oid = ws
    with app.state.db.session() as s:
        active = next(o for o in s.scalars(select(m.Opportunity)) if o.owner == "Alex Morgan" and o.stage == "active")
        target = active.id
    monkeypatch.setattr(TD, "assessment", lambda o: {"readiness": "Ready"} if o.id == target else None)
    ready_rows = lambda: [it for it in rows(todo(api)) if it["label"] == "Send for approval" and it["opportunity"]["id"] == target]
    assert [r["key"] for r in ready_rows()] == [f"ready:{target}"]  # the rules' own row
    with app.state.db.session() as s:
        it = J.to_inbox(s, date.today(), None, "ready", "Ready to submit", "", opp=s.get(m.Opportunity, target), agent="Manager agent")
        item_id = it.id
    assert [r["key"] for r in ready_rows()] == [f"inbox:{item_id}"]  # the agent's note, once


def test_an_agents_item_can_be_closed_from_its_row(ws):
    app, api, oid = ws
    with app.state.db.session() as s:
        o = s.get(m.Opportunity, oid)
        o.owner = "Alex Morgan"
        it = J.to_inbox(s, AU.agent_ctx(app, s).today, None, "facts", f"2 new facts about {o.company.name}", "x", opp=o, agent="Research agent")
        item_id = it.id
    row = next(r for r in rows(todo(api)) if r["key"] == f"inbox:{item_id}")
    assert row["group"] == "check" and row["act"] == ["dismiss"] and row["ref"]["hash"]
    r = api.post(f"/api/inbox/{item_id}", headers=ALEX, json={"action": "dismiss", "payload_hash": row["ref"]["hash"]})
    assert r.status_code == 200
    assert not [r for r in rows(todo(api)) if r["key"] == f"inbox:{item_id}"]


def test_an_email_that_may_have_gone_out_is_a_problem_until_someone_checks_it(ws):
    app, api, oid = ws
    with app.state.db.session() as s:
        s.get(m.Opportunity, oid).owner = "Alex Morgan"
    r = approve(api, oid)
    with app.state.db.session() as s:
        assert U._take(AU.agent_ctx(app, s), r["id"], "sending")
    AU.recover_after_restart(app)
    t = todo(api)
    assert t["groups"][0]["id"] == "problem"
    row = next(x for x in rows(t) if x["key"] == f"email:{r['id']}")
    assert row["label"] == "Email may have gone out" and row["act"] == ["checked"] and TO in row["title"]
    assert api.post(f"/api/outbound/{r['id']}/checked", headers=ALEX).json()["checkedBy"] == "Alex Morgan"
    assert not [x for x in rows(todo(api)) if x["key"] == f"email:{r['id']}"]
    assert api.get(f"/api/opportunities/{oid}", headers=ALEX).json()["outreach"]["waiting"] is None  # the page notice goes too
    # an email that went out cannot be "checked"
    r2 = approve(api, oid)
    api.post(f"/api/outbound/{r2['id']}/send-now", headers=ALEX)
    assert api.post(f"/api/outbound/{r2['id']}/checked", headers=ALEX).status_code == 409


def test_approvals_go_to_the_directors_when_they_are_named(ws, monkeypatch):
    app, api, oid = ws
    approve_rows = lambda who: [x for x in rows(todo(api, who)) if x["label"] == "Approve or return"]
    assert approve_rows(SARAH)  # no directors named: anyone but the owner
    monkeypatch.setattr(app.state, "settings", app.state.settings.__class__(**{**app.state.settings.__dict__, "directors": ("Quinn Harper",)}))
    assert approve_rows(SARAH) == [] and approve_rows({"X-User": "Quinn Harper"})


def test_health_lists_only_what_is_wrong(ws):
    app, api, oid = ws
    with app.state.db.session() as s:
        J.set_setting(s, "agents_paused", True)
    h = todo(api)["health"]
    assert [x["key"] for x in h] == ["paused"] and h[0]["page"] == "agents"
    with app.state.db.session() as s:
        J.set_setting(s, "agents_paused", False)
        jid = J.enqueue(s, AU.agent_ctx(app, s).today, "ready_reminder", "meridian", "person", "test", requested_by="test").id
        s.flush()
        J.finish(s.get(m.AgentJob, jid), "failed", error="boom")
    assert [x["key"] for x in todo(api)["health"]] == ["failed"]
