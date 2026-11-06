"""Fixes from the live test, third batch: the first reply read for evidence, notes read again, an edited agent draft,
the nurture actions, several contacts and the research agent's own recommendation, and the website a nomination gives."""
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from api import autonomy as AU
from api import jobs as J
from api import models as m
from api.main import create_app
from api.research_bridge import card_to_brief, recommendation
from api.settings import Settings

from tests.v2.test_actions import post

ME = {"X-User": "Alex Morgan"}
REPLY = {"text": "Thanks. Could we talk on Wednesday 4 November?", "on": "2026-10-28"}


def app_and_api(tmp_path):
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "b3.sqlite3"))
    return app, TestClient(app)


def to_active(api):
    """Summit is waiting for a reply in the demo data."""
    post(api, "/api/opportunities/summit/outreach/reply", REPLY)


def note_jobs(app):
    with app.state.db.session() as s:
        return list(s.scalars(select(m.AgentJob).where(m.AgentJob.kind == "note_evidence")))


# ---------- the first reply ----------
def test_the_first_reply_is_read_for_evidence_in_the_background(tmp_path):
    app, api = app_and_api(tmp_path)
    to_active(api)
    [job] = note_jobs(app)
    o = api.get("/api/opportunities/summit").json()
    reply = next(n for n in o["notes"] if n["type"] == "Email reply")
    assert job.payload["note_id"] == reply["id"] and reply["reading"] and reply["analysis"] is None
    assert AU.run_job(app, job.id) == "done"
    reply = next(n for n in api.get("/api/opportunities/summit").json()["notes"] if n["id"] == reply["id"])
    assert reply["analysis"] is not None and not reply["reading"]
    assert AU.run_job(app, job.id) == "done"  # finished jobs do not run again
    with app.state.db.session() as s:
        again = J.enqueue(s, date(2026, 10, 28), "note_evidence", "summit", "event", "again", {"note_id": reply["id"]})
        s.commit()
        jid = again.id
    assert AU.run_job(app, jid) == "skipped"  # a note already read is not read twice


def test_a_note_saved_earlier_can_be_read_for_evidence(tmp_path):
    app, api = app_and_api(tmp_path)
    to_active(api)
    res = post(api, "/api/opportunities/summit/notes", {"text": "Short call. Nothing new.", "analyze": False})
    out = post(api, f"/api/opportunities/summit/notes/{res['noteId']}/analyze")
    assert out["noteId"] == res["noteId"] and "proposals" in out
    note = next(n for n in api.get("/api/opportunities/summit").json()["notes"] if n["id"] == res["noteId"])
    assert note["analysis"]["proposals"] == out["proposals"]
    assert api.post("/api/opportunities/summit/notes/n-missing/analyze", headers=ME).status_code == 404


def test_the_evidence_agents_summary_is_kept_with_the_note(tmp_path, monkeypatch):
    from api.llm import make_provider
    from agent_fakes import play
    monkeypatch.delenv("WEB_MODE", raising=False)
    db = tmp_path / "live.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=lambda url, headers, payload, **kw: play(payload))
    api = TestClient(app)
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["stage"] == "active")
    res = post(api, f"/api/opportunities/{oid}/notes", {"text": "Short call. The CFO will join next time."})
    assert res["summary"] == "Went through the notes."
    note = next(n for n in api.get(f"/api/opportunities/{oid}").json()["notes"] if n["id"] == res["noteId"])
    assert note["analysis"]["summary"] == "Went through the notes." and note["analysis"]["proposals"] == res["proposals"]


# ---------- an agent's draft, edited before approving ----------
def test_a_person_can_edit_an_agents_draft_and_approves_their_version(tmp_path):
    app, api = app_and_api(tmp_path)
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["stage"] == "identified")
    with app.state.db.session() as s:
        it = J.to_inbox(s, date(2026, 10, 26), None, "draft", "First email", "Hi.", {"subject": "Hi", "body": "Hi.", "purpose": "first", "facts": []},
                        opp=s.get(m.Opportunity, oid), agent="Outreach agent")
        item_id, old_hash = it.id, it.payload_hash
        s.commit()
    r = api.put(f"/api/inbox/{item_id}/draft", headers=ME, json={"subject": "Your new plant", "body": "Hello Dana.", "payload_hash": old_hash})
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["payloadHash"] != old_hash and item["data"]["subject"] == "Your new plant" and item["data"]["editedBy"] == "Alex Morgan"
    assert item["data"]["original"] == {"subject": "Hi", "body": "Hi."}
    stale = api.put(f"/api/inbox/{item_id}/draft", headers=ME, json={"subject": "x", "body": "y", "payload_hash": old_hash})
    assert stale.status_code == 409  # an edit of a version the person did not see is refused
    assert api.post(f"/api/inbox/{item_id}", headers=ME, json={"action": "apply", "payload_hash": old_hash}).status_code == 409
    assert api.post(f"/api/inbox/{item_id}", headers=ME, json={"action": "apply", "payload_hash": item["payloadHash"]}).status_code == 200
    from api import scorecard as SC
    with app.state.db.session() as s:
        row = next(r for r in SC.work(s, date(2026, 10, 27)) if r["agent"] == "Outreach agent" and r["used"])
        assert row["usedAfterEdit"] == 1
    assert api.put(f"/api/inbox/{item_id}/draft", headers=ME, json={"subject": "x", "body": "y"}).status_code == 409  # decided


# ---------- nurture ----------
def nurture_lead(api):
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["stage"] == "identified")
    post(api, f"/api/opportunities/{oid}/nurture", {"reason": "Budget is set for 2027", "review_on": "2027-01-04"})
    return oid


def test_nurture_review_date_reconnect_and_close(tmp_path):
    app, api = app_and_api(tmp_path)
    oid = nurture_lead(api)
    post(api, f"/api/opportunities/{oid}/nurture/review", {"review_on": "2026-12-01", "reason": "Their budget round moved"})
    assert api.get(f"/api/opportunities/{oid}").json()["next"]["due"] == "2026-12-01"
    assert api.post(f"/api/opportunities/{oid}/nurture/review", headers=ME, json={"review_on": "2020-01-01", "reason": "x"}).status_code == 400
    assert post(api, f"/api/opportunities/{oid}/nurture/reconnect") == {"queued": True, "already": False}
    assert post(api, f"/api/opportunities/{oid}/nurture/reconnect")["already"]
    with app.state.db.session() as s:
        [job] = s.scalars(select(m.AgentJob).where(m.AgentJob.kind == "research_refresh", m.AgentJob.opportunity_id == oid))
        assert job.trigger == "person" and "reconnect now" in job.reason
    assert api.post(f"/api/opportunities/{oid}/close", headers=ME, json={"reason": "Other"}).status_code == 400
    post(api, f"/api/opportunities/{oid}/close", {"reason": "Client declined", "comment": "They chose to build it in house."})
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["stage"] == "closed" and o["decisions"][-1]["decision"] == "Closed" and "Client declined" in o["decisions"][-1]["note"]


# ---------- research: contacts and the agent's own recommendation ----------
def test_the_research_agent_records_up_to_three_contacts():
    import pytest
    from api import agent_runtime as R
    from tests.v2.test_research_agent import FINISH, LEADERS, NEWS, tools
    t, record = tools()
    t["company_pages"]({"topic": "leadership news"})
    t["read_page"]({"url": LEADERS})
    assert t["add_contact"]({"name": "Priya Desai", "title": "Chief Digital Officer", "url": LEADERS, "why": "Leads the AI pilots",
                             "quote": "Priya Desai, Chief Digital Officer, joined in August 2026 and reports to the CEO."})["contacts"] == 1
    with pytest.raises(R.ToolError, match="already recorded"):
        t["add_contact"]({"name": "Priya Desai", "title": "CDO", "url": LEADERS,
                          "quote": "Priya Desai, Chief Digital Officer, joined in August 2026 and reports to the CEO."})
    assert t["add_contact"]({"name": "Gregory Hale", "title": "President and CEO", "url": LEADERS, "why": "Approves spend",
                             "quote": "Gregory Hale, President and CEO."})["contacts"] == 2
    t["read_page"]({"url": NEWS})
    t["add_fact"]({"url": NEWS, "category": "signal", "fact": "Northwind named its first CDO.",
                   "quote": "Northwind Fluid Systems has appointed Priya Desai as its first Chief Digital Officer, reporting to CEO Gregory Hale."})
    with pytest.raises(R.ToolError, match="why you recommend"):
        t["finish"]({**FINISH, "verdict": "accept", "verdict_reason": ""})
    t["finish"]({**FINISH, "verdict": "send_back", "verdict_reason": "Worth it, but who owns the budget is not known yet."})
    brief, person = card_to_brief(record["card"], "Alex Morgan", "2026-11-01", {})
    assert person == {"name": "Priya Desai", "title": "Chief Digital Officer"}
    assert [c["name"] for c in brief["contacts"]] == ["Priya Desai", "Gregory Hale"] and brief["contacts"][1]["why"] == "Approves spend"
    assert brief["contacts"][0]["url"] == LEADERS
    assert brief["recommendation"] == {"decision": "return", "basis": "agent", "reason": "Worth it, but who owns the budget is not known yet."}


def test_the_checks_can_only_overrule_an_accept():
    card = {"research_gaps": []}
    flag = [{"type": "Identity unconfirmed", "text": "x"}]
    assert recommendation(None, True, [], card)["basis"] == "checks"
    assert recommendation({"decision": "accept", "reason": "Fits."}, True, [], card) == {"decision": "accept", "basis": "agent", "reason": "Fits."}
    over = recommendation({"decision": "accept", "reason": "Fits."}, True, flag, card)
    assert over["decision"] == "return" and over["overruled"] == "accept" and over["checks"]
    assert recommendation({"decision": "not_now", "reason": "Too small."}, True, [], card)["decision"] == "reject"
    assert recommendation({"decision": "send_back", "reason": "Ask first."}, False, flag, card)["decision"] == "return"


def test_public_research_sets_no_revenue_tier_and_no_relationship():
    from tests.v2.test_research_agent import FINISH, LEADERS, NEWS, tools
    t, record = tools()
    t["company_pages"]({"topic": "leadership news"})
    t["read_page"]({"url": NEWS})
    t["add_fact"]({"url": NEWS, "category": "signal", "fact": "Northwind named its first CDO.",
                   "quote": "Northwind Fluid Systems has appointed Priya Desai as its first Chief Digital Officer, reporting to CEO Gregory Hale."})
    t["finish"](FINISH)
    p = card_to_brief(record["card"], "Alex Morgan", "2026-11-01", {})[0]["priority"]
    assert p["tier"] is None and p["relationship"] is None and "not connected" not in str(p)


# ---------- discovery: the website a nomination gives ----------
def test_a_nomination_keeps_only_a_website_the_source_shows():
    from api.discovery_agent import company_site
    text = "kessler motion controls opens a plant. about kessler: www.kesslermotion.com"
    assert company_site("www.kesslermotion.com", "Kessler Motion Controls", "https://news.example/a", text) == "kesslermotion.com"
    assert company_site("https://kesslermotion.com/about", "Kessler Motion Controls", "https://news.example/a", text) == "kesslermotion.com"
    assert company_site("kessler.com", "Kessler Motion Controls", "https://news.example/a", text) == ""   # not in the source
    assert company_site("yahoo.com", "Olin", "https://finance.yahoo.com/news/olin", "olin and huntsman") == ""  # the news site
    assert company_site("olin.com", "Olin Corporation", "https://www.olin.com/news/merger", "olin and huntsman") == "olin.com"  # on its own site
    assert company_site("olin.edu", "Olin", "https://news.example/a", "see olin.edu") == ""  # a university
    assert company_site("linkedin.com", "Olin", "https://news.example/a", "linkedin.com/company/olin") == ""
