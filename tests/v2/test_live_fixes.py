"""Fixes from the full live test of 5 Nov 2026 (runtime/live-full-test-issues.md): the company website, the same-news
check, honest labels, drafts that leave with nurture, research in the agent panel."""
from datetime import date

from fastapi.testclient import TestClient

from api import discovery_agent as D
from api import jobs as J
from api import models as m
from api import research_agent as RA
from api.main import STARTING, create_app, starting_direction
from api.research_bridge import card_to_brief
from api.settings import Settings

from tests.v2.test_research_live import CARD, FakeBridge, live

ME = {"X-User": "Alex Morgan"}


class Results:
    def __init__(self, *urls):
        self.urls = urls

    def search(self, q, start_date=None, news=False):
        return [{"url": u, "title": "", "content": ""} for u in self.urls]


def test_the_website_search_needs_every_word_of_the_name_and_skips_colleges():
    # seen live: "Olin" (a chemicals company) went to a college, "Second Nature Brands" to another company's site
    assert D.resolve_domain(Results("https://my.olin.edu/about", "https://www.olin.com/"), "Olin") == "olin.com"
    assert D.resolve_domain(Results("https://secondnature.com/", "https://secondnaturebrands.com/pages/about"),
                            "Second Nature Brands") == "secondnaturebrands.com"
    assert D.resolve_domain(Results("https://www.linkedin.com/company/x", "https://secondnature.com/"), "Second Nature Brands") == ""


class Pages:
    def __init__(self, *pages):
        self.pages = pages

    def search(self, q, start_date=None, news=False):
        return [{"url": u, "title": t, "content": c} for u, t, c in self.pages]


def test_a_website_may_drop_one_word_when_the_page_names_the_company():
    assert D.resolve_domain(Pages(("https://www.kesslermotion.example/", "Kessler Motion Controls | Official site", "")),
                            "Kessler Motion Controls") == "kesslermotion.example"
    assert D.resolve_domain(Pages(("https://secondnature.com/", "Second Nature | Air filters", "Filters delivered.")),
                            "Second Nature Brands") == ""  # one word short and the page names another company


def test_the_research_agent_is_told_the_news_the_company_was_picked_for():
    t = RA.task("Olin", "", news='"Olin agreed to merge with Huntsman" (source: https://news.example/olin)')
    assert "Discovery picked this company for this news" in t and "Huntsman" in t and "different organisation" in t
    assert "Discovery picked" not in RA.task("Olin", "olin.com")


class NewsBridge(FakeBridge):
    news = True

    def start(self, company, domain, consultant, news=""):
        self.started.append((company, domain, news))
        return "run-1"


def test_a_brief_about_another_organisation_is_flagged(tmp_path):
    bridge = NewsBridge()
    api = live(tmp_path, bridge)
    with api.app.state.db.session() as s:  # the candidate was picked for a merger the brief never mentions
        cand = s.get(m.Candidate, "c2")
        cand.why, cand.evidence_quote = "Agreed to merge with Huntsman Chemicals in a chlor-alkali deal.", "Brightwater and Huntsman Chemicals agreed to merge."
        s.commit()
    oid = api.post("/api/candidates/c2/pick", headers=ME).json()["opportunities"][0]
    assert "Huntsman" in bridge.started[0][2]  # the research agent was told the news
    api.post("/api/research/sync", headers=ME)
    api.post("/api/research/sync", headers=ME)
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["brief"]["flags"][0]["type"] == "Same company?" and "huntsman" in o["brief"]["flags"][0]["text"]
    assert o["brief"]["recommendation"] == {"decision": "return", "basis": "checks", "reason": "Resolve the flags below before accepting."}


def test_a_brief_about_the_news_is_not_flagged(tmp_path):
    api = live(tmp_path, NewsBridge())
    oid = api.post("/api/candidates/c2/pick", headers=ME).json()["opportunities"][0]
    api.post("/api/research/sync", headers=ME)
    api.post("/api/research/sync", headers=ME)
    o = api.get(f"/api/opportunities/{oid}").json()
    assert not any(f["type"] == "Same company?" for f in o["brief"]["flags"])


def test_the_suggestion_says_it_comes_from_checks():
    brief, _ = card_to_brief(CARD, "Alex Morgan", "2026-10-30", {})
    assert brief["recommendation"]["basis"] == "checks" and "your call" in brief["recommendation"]["reason"]


def test_the_starting_direction_does_not_claim_an_approval():
    assert starting_direction().approved_by == STARTING


def test_moving_to_nurture_withdraws_unsent_drafts(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "n.sqlite3"))
    api = TestClient(app)
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["stage"] == "identified")
    with app.state.db.session() as s:
        item = J.to_inbox(s, date(2026, 10, 26), None, "draft", "First email", "x", {"subject": "Hi", "body": "Hi.", "purpose": "first", "facts": []},
                          opp=s.get(m.Opportunity, oid), agent="Outreach agent")
        item_id = item.id
        s.commit()
    r = api.post(f"/api/opportunities/{oid}/nurture", headers=ME, json={"reason": "Budget is set for 2027", "review_on": "2027-01-04"})
    assert r.status_code == 200, r.text
    with app.state.db.session() as s:
        it = s.get(m.InboxItem, item_id)
        assert it.status == "dismissed" and "nurture" in it.decided_by
    assert any("unsent email draft(s) withdrawn" in a["text"] for a in api.get(f"/api/opportunities/{oid}").json()["activity"])


def test_the_agent_panel_sees_research(tmp_path):
    api = live(tmp_path, FakeBridge())
    api.post("/api/candidates/c2/pick", headers=ME)
    r = api.get("/api/agents").json()["research"]
    assert "Brightwater Valve Co." in r["running"]  # the demo data has research of its own running too
    api.post("/api/research/sync", headers=ME)
    api.post("/api/research/sync", headers=ME)
    r = api.get("/api/agents").json()["research"]
    assert "Brightwater Valve Co." not in r["running"] and r["last"] == {"company": "Brightwater Valve Co.", "status": "done"}
