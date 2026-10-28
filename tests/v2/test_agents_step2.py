"""Multi-agent step 2: the evidence and outreach agents use tools, and an approval is tied to exactly what the agent prepared.
Fake AI companies and a fake web stand in for the real ones, so these run offline."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api import jobs as J
from api import models as m
from api.llm import make_provider
from api.main import create_app
from api.settings import Settings
from agent_fakes import results, turn

ME = {"X-User": "Alex Morgan"}


class Script:
    """A fake AI company. Each answer is a full response, or a function of the request (to react to tool results)."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, url, headers, payload, **kw):
        self.requests.append(payload)
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return a(payload) if callable(a) else a


def live_app(tmp_path, ai, web=None):
    db = tmp_path / "s2.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=ai)
    if web:
        app.state.search_factory = lambda: web
    return app, TestClient(app)


# ---------- evidence agent ----------
def test_evidence_agent_may_quote_an_earlier_note_that_still_applies(tmp_path):
    earlier = "Elena said the CFO approved a budget of $400K for this work in the 2027 plan."

    def propose_from_earlier(payload):
        notes = results(payload)[0][1]
        old = next(n for n in notes["earlier_notes"] if n["text"] == earlier)
        return turn(("propose", {"kind": "score", "key": "B", "value": "4", "quote": earlier, "why": "Budget approved earlier",
                                 "evidence_type": "client_statement", "source": "notes", "note_id": old["id"]}))
    ai = Script(turn(("notes", {})), propose_from_earlier, turn(("finish", {"summary": "Budget from the earlier call."})))
    app, api = live_app(tmp_path, ai)
    api.post("/api/opportunities/alder/notes", headers=ME, json={"text": earlier, "analyze": False})
    old_id = next(n["id"] for n in api.get("/api/opportunities/alder").json()["notes"] if n["text"] == earlier)
    r = api.post("/api/opportunities/alder/notes", headers=ME, json={"text": "Short follow-up call. Nothing new on budget."}).json()
    assert r["proposals"] == 1
    with app.state.db.session() as s:
        p = s.query(m.Proposal).filter_by(opportunity_id="alder", key="B", status="pending").one()
        assert p.note_id == old_id and p.quote == earlier  # linked to the note the quote is really from


# ---------- outreach agent ----------
class FakeWeb:
    """One newer news page about Halvorsen, newer than its brief."""
    url = "https://news.example/halvorsen-ohio-ramp-up"
    text = "Halvorsen said its third Ohio foundry will reach full output in January 2027. The company is hiring 120 people."

    def search(self, query, start_date=None, news=False):
        return [{"url": self.url, "title": "Halvorsen ramps up Ohio foundry", "published": "2026-10-20", "content": self.text}]

    def read(self, url):
        return {"url": url, "title": "", "published": "2026-10-20", "content": self.text} if url == self.url else None


def test_outreach_agent_adds_a_newer_fact_from_a_page_it_read(tmp_path):
    quote = "Halvorsen said its third Ohio foundry will reach full output in January 2027."
    body = ("Hi Priya, I saw that [[f2|the third Ohio foundry will reach full output in January 2027]]. "
            "Would 20 minutes be useful? Alex Morgan, CGS Advisors")
    ai = Script(turn(("brief", {}), ("history", {}), ("search_web", {"query": "Halvorsen Ohio foundry news"})),
                turn(("add_fact", {"url": FakeWeb.url, "quote": quote})),           # refused: not read yet
                turn(("read_page", {"url": FakeWeb.url})),
                turn(("add_fact", {"url": FakeWeb.url, "quote": quote})),
                turn(("submit_draft", {"subject": "Ohio", "body": body.replace("full output in January 2027", "record profits")})),  # not the source
                turn(("submit_draft", {"subject": "Your Ohio ramp-up", "body": body})))
    app, api = live_app(tmp_path, ai, FakeWeb())
    r = api.post("/api/opportunities/halvorsen/outreach/generate", headers=ME, json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["subject"] == "Your Ohio ramp-up" and d["newFacts"][0]["id"] == "f2" and d["newFacts"][0]["src"] == FakeWeb.url
    o = api.get("/api/opportunities/halvorsen").json()
    assert o["brief"]["facts"][-1]["text"] == quote and o["outreach"]["body"] == body
    errors = [json.dumps(ai.requests[i]["contents"][-1]) for i in (2, 5)]
    assert "Read the page" in errors[0] and "does not match its source" in errors[1]
    run = next(x for x in api.get("/api/agent-runs").json() if x["agent"] == "Outreach agent")
    assert run["prompt"] == "outreach-agent-1.0" and run["calls"] == 6


# ---------- approval gate ----------
@pytest.fixture
def demo(tmp_path):
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "g.sqlite3"))
    return app, TestClient(app)


def put(app, opp_id, kind, data):
    with app.state.db.session() as s:
        o = s.get(m.Opportunity, opp_id)
        today = date.fromisoformat(s.get(m.Setting, "demo_today").value)
        item = J.to_inbox(s, today, None, kind, f"{kind} for {opp_id}", "body", data, opp=o, agent="test agent")
        return item.id


def test_apply_uses_exactly_what_was_prepared(demo):
    app, api = demo
    iid = put(app, "bluegate", "draft", {"subject": "s", "body": "b", "facts": [{"id": "f9", "text": "A newer sentence.", "src": "x"}]})
    item = next(i for i in api.get("/api/inbox?status=all").json() if i["id"] == iid)
    assert len(item["payloadHash"]) == 64 and item["expiresOn"]
    r = api.post(f"/api/inbox/{iid}", headers=ME, json={"action": "apply", "payload_hash": "0" * 64})
    assert r.status_code == 409 and r.json()["code"] == "changed"
    r = api.post(f"/api/inbox/{iid}", headers=ME, json={"action": "apply", "payload_hash": item["payloadHash"]})
    assert r.status_code == 200 and "1 cited fact added" in r.json()["note"]
    assert api.get("/api/opportunities/bluegate").json()["brief"]["facts"][-1]["id"] == "f9"


def test_apply_refuses_when_the_opportunity_moved_on_or_the_item_expired(demo):
    app, api = demo
    iid = put(app, "meridian", "nurture", {"review_on": "2027-04-01"})
    api.post("/api/opportunities/meridian/nurture", headers=ME, json={"reason": "Client asked to wait"})
    r = api.post(f"/api/inbox/{iid}", headers=ME, json={"action": "apply"})
    assert r.status_code == 409 and r.json()["code"] == "stale" and "active to nurture" in r.json()["error"]
    assert api.post(f"/api/inbox/{iid}", headers=ME, json={"action": "dismiss"}).status_code == 200  # dismissing is always allowed
    iid = put(app, "bluegate", "facts", {"facts": [{"id": "f1", "text": "Old news."}]})
    api.post("/api/demo/advance-days?days=15", headers=ME)
    r = api.post(f"/api/inbox/{iid}", headers=ME, json={"action": "apply"})
    assert r.status_code == 409 and r.json()["code"] == "expired"


def test_a_draft_whose_facts_now_mean_something_else_is_refused(demo):
    app, api = demo
    first = api.get("/api/opportunities/bluegate").json()["brief"]["facts"][0]
    iid = put(app, "bluegate", "draft", {"subject": "s", "body": "b", "facts": [{**first, "text": "Something the brief does not say."}]})
    r = api.post(f"/api/inbox/{iid}", headers=ME, json={"action": "apply"})
    assert r.status_code == 409 and r.json()["code"] == "stale" and first["id"] in r.json()["error"]


def test_a_research_request_keeps_only_the_job_title():
    """Found with real Claude: the evidence agent wrote the role as "CEO (approves spend for Orchard Lane Foods)", and the
    research agent's correct answer "CEO" was then rejected."""
    from api.services import job_title, role_matches
    assert job_title("CEO (approves spend for Orchard Lane Foods)") == "CEO"
    assert job_title("CFO, who signs off above $500K") == "CFO" and job_title("Chief Financial Officer") == "Chief Financial Officer"
    assert role_matches("CEO", "CEO (approves spend for Orchard Lane Foods)")
    assert role_matches("Chief Executive Officer", "CEO") and not role_matches("Chief Operating Officer", "CEO")
