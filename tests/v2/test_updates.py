"""The updates feed the page polls (GET /api/updates): agent work for the person acting, research that ended, and
approved emails that went out or did not."""
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import select

from api import jobs as J
from api import models as m
from api.main import create_app
from api.settings import Settings

from tests.v2.test_research_live import FakeBridge, live
from tests.v2.test_toolkit_mail import BODY

ME = {"X-User": "Alex Morgan"}


def feed(api, since=None, who=ME):
    q = f"?since={quote(since)}" if since else "?days=7"
    r = api.get(f"/api/updates{q}", headers=who)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_feed_has_my_agent_work_and_the_teams_but_not_someone_elses(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "u.sqlite3"))
    api = TestClient(app)
    start = feed(api)["now"]
    with app.state.db.session() as s:
        o = s.scalars(select(m.Opportunity).where(m.Opportunity.owner == "Alex Morgan")).first()
        J.to_inbox(s, date(2026, 10, 26), None, "draft", "First email for Dana", "x", {"purpose": "first"}, opp=s.get(m.Opportunity, o.id), agent="Outreach agent")
        J.to_inbox(s, date(2026, 10, 26), None, "candidates", "4 new candidates", "x", {}, agent="Discovery agent")
        J.to_inbox(s, date(2026, 10, 26), None, "draft", "Sarah's draft", "x", {"purpose": "first"}, owner="Sarah Patel", agent="Outreach agent")
        s.commit()
    got = feed(api, start)
    texts = [x["text"] for x in got["items"]]
    assert "First email for Dana" in texts and "4 new candidates" in texts and "Sarah's draft" not in texts
    first = next(x for x in got["items"] if x["text"] == "First email for Dana")
    assert first["kind"] == "inbox" and first["itemKind"] == "draft" and first["opportunityId"] == o.id and first["stage"]
    assert feed(api, got["now"])["items"] == []  # nothing new since the last check
    assert "Sarah's draft" in [x["text"] for x in feed(api, start, {"X-User": "Sarah Patel"})["items"]]


def test_research_that_ends_is_in_the_feed(tmp_path):
    api = live(tmp_path, FakeBridge())
    start = feed(api)["now"]
    api.post("/api/candidates/c2/pick", headers=ME)
    assert not [x for x in feed(api, start)["items"] if x["kind"] == "research"]  # still running
    api.post("/api/research/sync", headers=ME)
    api.post("/api/research/sync", headers=ME)
    [r] = [x for x in feed(api, start)["items"] if x["kind"] == "research"]
    assert r["text"] == "Brief ready: Brightwater Valve Co." and r["status"] == "done" and r["stage"] == "review"


def test_an_approved_email_that_went_out_is_in_the_feed(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "e.sqlite3"))
    api = TestClient(app)
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["company"] == "Halvorsen Precision Castings")
    api.put(f"/api/opportunities/{oid}/outreach/draft", headers=ME, json={"subject": "Your second foundry", "body": BODY})
    start = feed(api)["now"]
    p = api.get(f"/api/opportunities/{oid}/outreach/email", headers=ME, params={"to": "dana@halvorsen.example"}).json()
    out = api.post(f"/api/opportunities/{oid}/outreach/email", headers=ME,
                   json={"to": "dana@halvorsen.example", "mode": "send", "seenHash": p["seenHash"], "delay": 10}).json()
    assert not [x for x in feed(api, start)["items"] if x["kind"] == "email"]  # waiting to go out
    api.post(f"/api/outbound/{out['id']}/send-now", headers=ME)
    [e] = [x for x in feed(api, start)["items"] if x["kind"] == "email"]
    assert e["text"] == "Email sent to dana@halvorsen.example." and e["status"] == "sent"
    assert not [x for x in feed(api, start, {"X-User": "Sarah Patel"})["items"] if x["kind"] == "email"]  # Alex's email


def test_a_bad_since_reads_the_last_days():
    from api.updates import since_from
    t = since_from("not a time", 2)
    assert datetime.now(timezone.utc) - t > timedelta(hours=47)
    assert since_from("2026-11-05T10:00:00+00:00", 7) == datetime(2026, 11, 5, 10, tzinfo=timezone.utc)
    assert since_from("2026-11-05T10:00:00 00:00", 7) == datetime(2026, 11, 5, 10, tzinfo=timezone.utc)  # "+" decoded as a space
