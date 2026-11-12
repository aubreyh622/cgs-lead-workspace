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


def test_a_website_may_shorten_words_when_the_page_names_the_company():
    # the quality check's research cases: these two companies were never researched because no website was found
    assert D.resolve_domain(Pages(("https://www.silverlinemed.example/", "Silverline Medical Devices | Official site", "")),
                            "Silverline Medical Devices") == "silverlinemed.example"
    assert D.resolve_domain(Pages(("https://www.quillonchem.example/", "Quillon Specialty Chemicals | Official site", "")),
                            "Quillon Specialty Chemicals") == "quillonchem.example"
    # not without the page naming the company, not with letters the name does not explain, not from short pieces alone
    assert D.resolve_domain(Pages(("https://www.silverlinemed.example/", "Home", "")), "Silverline Medical Devices") == ""
    assert D.resolve_domain(Pages(("https://www.silverlinemedx.example/", "Silverline Medical Devices", "")), "Silverline Medical Devices") == ""
    assert not D.abbreviates("silmeddev.example", ["silverline", "medical", "devices"])


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


def test_contacting_by_hand_or_a_reply_withdraws_unsent_drafts(tmp_path):
    """Live demo prep, 6 Nov 2026: the lead was contacted on LinkedIn and replied, but the agent's first email still
    waited in To approve."""
    app = create_app(Settings(db_path=tmp_path / "d.sqlite3"))
    api = TestClient(app)
    oid = next(o["id"] for o in api.get("/api/opportunities").json() if o["stage"] == "identified")

    def draft():
        with app.state.db.session() as s:
            item = J.to_inbox(s, date(2026, 10, 26), None, "draft", "Email", "x", {"subject": "Hi", "body": "Hi.", "purpose": "first", "facts": []},
                              opp=s.get(m.Opportunity, oid), agent="Outreach agent")
            s.commit()
            return item.id

    def status(item_id):
        with app.state.db.session() as s:
            return s.get(m.InboxItem, item_id).status

    first = draft()
    assert api.post(f"/api/opportunities/{oid}/outreach/sent", headers=ME, json={"via": "LinkedIn"}).status_code == 200
    assert status(first) == "dismissed"
    follow = draft()
    r = api.post(f"/api/opportunities/{oid}/outreach/reply", headers=ME, json={"text": "Thanks, happy to talk next week."})
    assert r.status_code == 200, r.text
    assert status(follow) == "dismissed"
    assert sum("unsent email draft(s) withdrawn" in a["text"] for a in api.get(f"/api/opportunities/{oid}").json()["activity"]) == 2


def test_the_new_candidates_notice_closes_when_its_round_is_decided(tmp_path):
    """Live demo prep, 6 Nov 2026: "3 new candidates" stayed on Home after all three were picked."""
    app = create_app(Settings(db_path=tmp_path / "c.sqlite3"))
    api = TestClient(app)
    fresh = [x for x in api.get("/api/candidates", params={"status": "new"}).json()]
    rnd = fresh[0]["round"]
    same = [x for x in fresh if x["round"] == rnd]
    with app.state.db.session() as s:
        item_id = J.to_inbox(s, date(2026, 10, 26), None, "candidates", f"{len(same)} new candidates", "x", {"round": rnd}).id
        s.commit()

    def status():
        with app.state.db.session() as s:
            return s.get(m.InboxItem, item_id).status

    for x in same[:-1]:
        assert api.post(f"/api/candidates/{x['id']}/skip", headers=ME, json={"reason": "Too small"}).status_code == 200
        assert status() == "new"
    last = same[-1]
    assert api.post(f"/api/candidates/{last['id']}/pick", headers=ME).status_code == 200
    assert status() == "done"
