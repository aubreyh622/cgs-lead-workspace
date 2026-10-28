"""Step 6c checks: live discovery (Step 1) and live research (Steps 2-3) with fake web search, fake AI and a fake research pipeline."""
import json
import sqlite3

from fastapi.testclient import TestClient

from api.discovery_agent import check_nominations
from api.llm import make_provider
from api.main import create_app
from api.research_bridge import card_to_brief
from api.settings import Settings

ME = {"X-User": "Alex Morgan"}

CARD = {"research": {
    "company": "Brightwater Valve Co.", "domain": "brightwater.example", "identity": "confirmed", "eligibility": "eligible", "eligibility_reason": "US plants",
    "evidence": [{"evidence_id": "e1", "category": "signal", "observed_fact": "Brightwater announced an ERP consolidation across five plants.",
                  "source_id": "s1", "excerpt": "Brightwater Valve today announced it will consolidate ERP systems across its five plants.",
                  "publication_date": "2026-10-02"}],
    "people": [{"name": "Marcus Hale", "role": "Chief Information Officer", "company": "Brightwater", "evidence_ids": ["e1"], "verified_at": "2026-10-26"}],
    "problem_hypothesis": "Consolidating five ERP estates may need an operating-model plan.", "cgs_relevance": "Matches CGS operating-model planning work.",
    "alternative_explanation": "The ERP vendor may handle process design.", "confidence": "medium", "unanswered_questions": ["Who owns the budget?"],
    "conflicts": [], "components": {"buyer_access": {"assessment": "medium", "reason": "CIO named", "evidence_ids": []},
                                    "capability_fit": {"assessment": "high", "reason": "ERP and operating model", "evidence_ids": []}},
    "suggested_next_investigation": "Confirm the program sponsor."},
    "sources": [{"source_id": "s1", "url": "https://brightwater.example/news/erp"}],
    "priority": {"band": "medium", "components": {"buyer_access": {"assessment": "medium", "reason": "CIO named"},
                                                  "capability_fit": {"assessment": "high", "reason": "ERP and operating model"}}},
    "validation_issues": [], "research_gaps": [], "review": {"decision": "Ready for review"}}


class FakeBridge:
    def __init__(self, card=CARD, final="completed"):
        self.card, self.final, self.calls, self.started = card, final, 0, []

    def start(self, company, domain, consultant):
        self.started.append((company, domain))
        return "run-1"

    def status(self, run_id):
        self.calls += 1
        return {"status": "running" if self.calls == 1 else self.final, "stage": "extract_facts",
                "usage": {"searches": 4, "extractions": 6, "model_calls": 7}, "error": None if self.card else "Budget reached", "events": ["Read official site"]}

    def result(self, run_id):
        return self.card

    def stop(self, run_id):
        pass

    def resume(self, run_id):
        pass


class FakeAI:
    def __init__(self, *answers):
        self.answers = list(answers)

    def __call__(self, url, headers, payload, **kw):
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(self.answers.pop(0))}]}}], "usageMetadata": {}}


class FakeSearch:
    def __init__(self, results):
        self.results, self.queries = results, []

    def search(self, q, start_date=None, news=False):
        self.queries.append(q)
        return self.results if len(self.queries) == 1 else []


def live(tmp_path, bridge=None, ai=None, search=None):
    create_app(Settings(app_mode="demo", db_path=tmp_path / "r.sqlite3")).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=tmp_path / "r.sqlite3"))
    app.state.research_bridge = bridge
    if ai:
        app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=ai)
    if search:
        app.state.search_factory = lambda: search
    return TestClient(app)


def test_card_to_brief_keeps_quotes_dates_and_the_person():
    brief, person = card_to_brief(CARD, "Alex Morgan", "2026-10-30", {"searches": 4, "extractions": 6, "model_calls": 7})
    f = brief["facts"][0]
    assert f["quote"].startswith("Brightwater Valve today announced") and f["src"].startswith("https://brightwater.example") and f["date"] == "2026-10-02"
    assert person == {"name": "Marcus Hale", "title": "Chief Information Officer"}
    assert brief["recommendation"]["decision"] == "accept" and brief["usage"] == {"searches": 4, "pages": 6, "calls": 7}
    assert brief["priority"]["tier"] == 1  # public research never sets CGS revenue


def test_live_pick_runs_research_in_the_background(tmp_path):
    bridge = FakeBridge()
    api = live(tmp_path, bridge)
    oid = api.post("/api/candidates/c2/pick", headers=ME).json()["opportunities"][0]
    assert bridge.started == [("Brightwater Valve Co.", "brightwater.example")]
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["stage"] == "researching" and o["research"]["status"] == "running"
    assert api.post("/api/research/sync", headers=ME).json()["updates"][0]["status"] == "running"
    assert api.post("/api/research/sync", headers=ME).json()["updates"][0]["status"] == "review"
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["stage"] == "review" and o["brief"]["facts"][0]["date"] == "2026-10-02" and o["brief"]["usage"]["pages"] == 6
    run = api.get("/api/agent-runs").json()[0]
    assert run["agent"] == "Research agent" and run["model"] == "gemini+tavily" and run["status"] == "completed"


def test_live_research_without_a_brief_is_a_visible_failure(tmp_path):
    bridge = FakeBridge(card=None, final="incomplete")
    api = live(tmp_path, bridge)
    oid = api.post("/api/candidates/c2/pick", headers=ME).json()["opportunities"][0]
    api.post("/api/research/sync", headers=ME)
    upd = api.post("/api/research/sync", headers=ME).json()["updates"][0]
    assert upd["status"] == "failed" and upd["error"]
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["stage"] == "researching" and o["brief"]["facts"][0]["src"] == "Public source"  # the placeholder brief was not replaced
    assert api.post(f"/api/opportunities/{oid}/research/retry", headers=ME).status_code == 200
    assert len(bridge.started) == 2


def test_research_needs_a_domain(tmp_path):
    api = live(tmp_path, FakeBridge())
    oid = api.post("/api/opportunities", json={"company": "Stonebridge Tool & Die"}, headers=ME).json()["id"]
    assert "website" in api.get(f"/api/opportunities/{oid}").json()["research"]["error"]
    assert api.patch(f"/api/opportunities/{oid}/company", json={"domain": "not a domain"}, headers=ME).status_code == 400
    assert api.patch(f"/api/opportunities/{oid}/company", json={"domain": "https://www.stonebridge.example/"}, headers=ME).status_code == 200
    assert api.post(f"/api/opportunities/{oid}/research/retry", headers=ME).status_code == 200
    assert api.get(f"/api/opportunities/{oid}").json()["research"]["status"] == "running"


RESULTS = [
    {"title": "Ardent Castings to acquire Hollow Pine Foundry", "url": "https://news.example/ardent", "content": "Ardent Castings (ardentcastings.com) said CIO Maria Lopez will lead the integration.", "published": "2026-10-10"},
    {"title": "Graniteridge Steel buys service centers", "url": "https://news.example/granite", "content": "Graniteridge Steel (graniteridge.example) expands.", "published": "2026-10-09"},
    {"title": "Kestrel Foods acquires snack brands", "url": "https://news.example/kestrel", "content": "Kestrel Foods closes deal.", "published": "2026-09-01"},
]


def nom(i, company, domain="", person="", title=""):
    return {"result_index": i, "company": company, "domain": domain, "why": "x", "signal": "Acquisition", "person_name": person, "person_title": title}


def test_nomination_checks():
    kept, dropped = check_nominations(
        [nom(0, "Ardent Castings", "ardentcastings.com", "Maria Lopez", "CIO"), nom(0, "Ardent Castings"),
         nom(0, "Phantom Corp"), nom(9, "Ardent Castings"), nom(1, "Graniteridge Steel", "graniteridge.example"),
         nom(2, "Kestrel Foods"), nom(0, "Hollow Pine Foundry", "", "Invented Person", "CEO")],
        RESULTS, ["graniteridge.example"], {"kestrel foods"})
    assert [(k["company"], k["person"]) for k in kept] == [("Ardent Castings", "Maria Lopez"), ("Hollow Pine Foundry", "")]
    assert kept[0]["source_url"] == "https://news.example/ardent" and kept[0]["signal_date"] == "2026-10-10"
    assert kept[1]["flags"] == ["Contact to confirm"]
    assert dropped == {"duplicate": 1, "name_not_in_source": 1, "no_source": 1, "excluded": 1, "already_known": 1}


class DiscoveryAI:
    """Plays the tool-using discovery agent: direction -> one search -> two nominations -> finish."""

    def __init__(self, query, nominations):
        self.query, self.nominations, self.turns = query, nominations, 0

    def __call__(self, url, headers, payload, **kw):
        from agent_fakes import results, turn
        self.turns += 1
        done = results(payload)
        if not done:
            return turn(("direction", {}))
        if len(done) == 1:
            assert done[0][1]["consultant_request"] == "Midwest foundries after an acquisition"
            return turn(("search_news", {"query": self.query}))
        if len(done) == 2:
            return turn(*[("nominate", n) for n in self.nominations])
        return turn(("finish", {"summary": "One search, one company."}))


def agent_nom(company, quote, domain="", person="", title="", url="https://news.example/ardent"):
    return {"company": company, "url": url, "why": "Acquired a foundry.", "signal": "Acquisition", "quote": quote,
            "domain": domain, "person_name": person, "person_title": title}


def test_live_recommend_round(tmp_path):
    ai = DiscoveryAI("foundry completes acquisition Midwest",
                     [agent_nom("Ardent Castings", "Ardent Castings (ardentcastings.com) said CIO Maria Lopez will lead the integration.",
                                "ardentcastings.com", "Maria Lopez", "CIO"),
                      agent_nom("Phantom Corp", "Phantom Corp will lead the integration.")])
    search = FakeSearch(RESULTS)
    api = live(tmp_path, ai=ai, search=search)
    r = api.post("/api/candidates/recommend", json={"intent": "Midwest foundries after an acquisition"}, headers=ME).json()
    assert r["round"] == 4 and r["candidates"] == 1 and search.queries == ["foundry completes acquisition Midwest"]
    assert r["checks"]["ai_nominations"] == 2 and r["checks"]["dropped_name_not_in_source"] == 1
    assert r["checks"]["queries"] == "chosen by the agent" and r["checks"]["searches"] == 1
    c = api.get("/api/candidates", params={"status": "new"}).json()
    ardent = next(x for x in c if x["company"] == "Ardent Castings")
    assert ardent["person"] == "Maria Lopez" and ardent["sourceUrl"] == "https://news.example/ardent" and "Maria Lopez" in ardent["quote"]
    run = api.get("/api/agent-runs").json()[0]
    assert run["agent"] == "Discovery agent" and run["prompt"] == "discovery-agent-1.0" and run["calls"] == ai.turns == 4
    steps = api.get(f"/api/agent-runs/{run['id']}").json()["trace"]
    assert any(s.get("tool") == "nominate" and "Write the company exactly" in str(s) for s in steps)  # Phantom Corp's rejection


def test_demo_recommend_round_respects_exclusions(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "d.sqlite3")))
    r = api.post("/api/candidates/recommend", json={}, headers=ME).json()
    names = [c["company"] for c in api.get("/api/candidates").json() if c["round"] == r["round"]]
    assert r["candidates"] == 10 and "Graniteridge Steel Service" not in names


def test_old_database_gets_new_columns(tmp_path):
    db = tmp_path / "old.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE candidates (id VARCHAR(64) PRIMARY KEY, round INTEGER, company VARCHAR(200), domain VARCHAR(200), person VARCHAR(200), "
                "title VARCHAR(200), why_person TEXT, why TEXT, signal VARCHAR(200), signal_date VARCHAR(40), flags JSON, status VARCHAR(20), "
                "skip_reason VARCHAR(200), skip_scope VARCHAR(20), direction_version VARCHAR(40), demo_star BOOLEAN)")
    con.commit()
    con.close()
    create_app(Settings(db_path=db)).state.db.engine.dispose()
    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(candidates)")]
    assert "source_url" in cols


class SiteSearch:
    def search(self, q, start_date=None, news=False):
        return [{"title": "Stonebridge on LinkedIn", "url": "https://www.linkedin.com/company/stonebridge", "content": "", "published": ""},
                {"title": "Stonebridge Tool & Die", "url": "https://www.stonebridgetool.com/about", "content": "", "published": ""}]


def test_research_finds_the_website_when_missing(tmp_path):
    bridge = FakeBridge()
    api = live(tmp_path, bridge, search=SiteSearch())
    oid = api.post("/api/opportunities", json={"company": "Stonebridge Tool & Die"}, headers=ME).json()["id"]
    assert bridge.started == [("Stonebridge Tool & Die", "stonebridgetool.com")]
    assert any("found the company website" in a["text"] for a in api.get(f"/api/opportunities/{oid}").json()["activity"])
