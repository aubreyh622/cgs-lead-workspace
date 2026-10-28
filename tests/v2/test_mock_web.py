"""Mock web checks: the search plug, the synthetic internet, synthetic research, and search settings for CGS."""
import json
from datetime import date

from fastapi.testclient import TestClient

from api.discovery_agent import discover, resolve_domain
from api.llm import make_provider
from api.main import create_app
from api.mock_research import MockResearchBridge, research
from api.search import PROVIDERS, MockSearch, make_search, search_config
from api.settings import Settings
from agent_fakes import turn

ME = {"X-User": "Alex Morgan"}


class FakeAI:
    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, url, headers, payload, **kw):
        self.requests.append(payload)
        a = self.answers.pop(0)
        if "candidates" in a and "usageMetadata" in a:  # already a full response (a tool-using agent's turn)
            return a
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(a)}]}}], "usageMetadata": {"promptTokenCount": 7}}


def test_default_provider_is_the_synthetic_internet(monkeypatch):
    monkeypatch.delenv("WEB_MODE", raising=False)
    assert search_config()["provider"] == "mock" and isinstance(make_search(), MockSearch)
    monkeypatch.setenv("WEB_MODE", "tavily")
    assert search_config()["provider"] == "tavily"
    assert set(PROVIDERS) >= {"mock", "tavily"}


def test_mock_search_finds_news_and_websites():
    m = MockSearch()
    titles = [r["title"] for r in m.search("manufacturer AI pilot plants scale", date(2026, 4, 1), news=True)]
    assert any("Northwind" in t for t in titles) and all(r["published"] for r in m.search("AI pilot", news=True))
    assert resolve_domain(m, "Kessler Motion Controls") == "kesslermotion.example"
    assert "northwindfluid.example" in m.simulations


def test_custom_queries_skip_the_ai_query_writer():
    ai = FakeAI({"candidates": []})
    m = MockSearch()
    (kept, info), _ = discover(make_provider("gemini", "m", key="k", transport=ai), m, {"problem": "x", "signals": [], "excluded_domains": []},
                               "", set(), date(2026, 10, 26), config={"custom_queries": ["completes merger integration office"]})
    assert info["queries"] == "custom" and info["searches"] == 1 and len(ai.requests) == 1


def test_synthetic_research_checks_quotes_and_names():
    pages = MockSearch().pages_for("Northwind Fluid Systems", "")
    lead = next(i for i, p in enumerate(pages) if "leadership" in p["url"])
    answer = {"identity_confirmed": True, "facts": [
        {"page_index": lead, "quote": "Priya Desai, Chief Digital Officer, joined in August 2026", "fact": "CDO joined in August.", "category": "person"},
        {"page_index": 2, "quote": "has appointed Priya Desai as its first Chief Digital Officer", "fact": "First CDO.", "category": "signal"},
        {"page_index": 0, "quote": "Northwind doubled revenue last year", "fact": "Invented.", "category": "other"},
        {"page_index": 99, "quote": "x", "fact": "x", "category": "other"}],
        "person_name": "Priya Desai", "person_role": "Chief Digital Officer", "person_page_index": lead,
        "hypothesis": "Scaling the pilots may need a plan.", "relevance": "First-mile AI planning.", "alternative": "Internal team.",
        "unknowns": ["Budget owner"], "confidence": "medium"}
    card, counts = research(make_provider("gemini", "m", key="k", transport=FakeAI(answer)), pages, "Northwind Fluid Systems", "northwindfluid.example")
    assert len(card["research"]["evidence"]) == 2 and counts["quote_not_found"] == 1 and counts["bad_page"] == 1
    assert card["research"]["people"][0]["name"] == "Priya Desai" and card["review"]["decision"] == "Ready for review"
    bad = dict(answer, person_name="Invented Person")
    card, counts = research(make_provider("gemini", "m", key="k", transport=FakeAI(bad)), pages, "Northwind Fluid Systems", "")
    assert card["research"]["people"] == [] and counts["person_not_on_page"] == 1


def test_live_mode_on_the_synthetic_internet(tmp_path, monkeypatch):
    monkeypatch.delenv("WEB_MODE", raising=False)
    answer = {"identity_confirmed": True, "facts": [{"page_index": 2, "quote": "has appointed Priya Desai as its first Chief Digital Officer", "fact": "First CDO.", "category": "signal"}],
              "person_name": "", "person_role": "", "person_page_index": -1, "hypothesis": "h", "relevance": "r", "alternative": "a", "unknowns": [], "confidence": "low"}
    create_app(Settings(app_mode="demo", db_path=tmp_path / "m.sqlite3")).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=tmp_path / "m.sqlite3"))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=FakeAI(answer))
    api = TestClient(app)
    oid = api.post("/api/opportunities", json={"company": "Northwind Fluid Systems"}, headers=ME).json()["id"]
    assert isinstance(app.state.research_bridge, MockResearchBridge)
    api.post("/api/research/sync", headers=ME)
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["stage"] == "review" and o["company"]["domain"] == "northwindfluid.example"
    assert api.get("/api/agent-runs").json()[0]["model"] == "gemini:m (synthetic web)"
    assert api.get("/api/search/settings").json()["provider"] == "mock"


def test_draft_citing_a_synthetic_case_carries_a_warning(tmp_path):
    from api.knowledge import library
    create_app(Settings(app_mode="demo", db_path=tmp_path / "w.sqlite3")).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=tmp_path / "w.sqlite3"))
    b = TestClient(app).get("/api/opportunities/halvorsen").json()["brief"]
    query = b["hypothesis"] + " " + b["relevance"]
    passage = library().search(query, k=3, types=("case", "service", "document"))[0]
    good = {"subject": "Your new Ohio foundry", "body": f"Hi Priya, I saw that [[f1|Halvorsen opened a third foundry in Ohio]]. "
            f"[[{passage['id']}|{' '.join(passage['text'].split()[:6])}]]. Open to 20 minutes? Alex Morgan, CGS Advisors"}
    fake = FakeAI(turn(("brief", {})), turn(("search_library", {"query": query})), turn(("submit_draft", good)))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=fake)
    r = TestClient(app).post("/api/opportunities/halvorsen/outreach/generate", json={}, headers=ME).json()
    assert "SYNTHETIC" in r["warnings"][0]


def test_synthetic_internet_has_enough_companies_for_repeat_demos():
    m = MockSearch()
    homes = [p for p in m.pages if p["url"].endswith(".example/")]
    assert len(homes) >= 20 and len(m.simulations) >= 10
    assert all(d in {p["url"].split("/")[2].removeprefix("www.") for p in homes} for d in m.simulations)
