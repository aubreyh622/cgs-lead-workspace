"""Multi-agent step 4: the tool-using discovery agent (Step 1). The tools are called directly, or a fake AI plays the agent."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api import agent_runtime as R
from api import autonomy as AU
from api import discovery_agent as D
from api.llm import make_provider
from api.main import create_app
from api.settings import Settings
from agent_fakes import play, turn

ME = {"X-User": "Alex Morgan"}
TODAY = date(2026, 10, 28)
DIRECTION = {"problem": "Integration planning after a merger", "signals": ["Acquisition", "New CIO"], "buyer_roles": ["CIO", "COO"],
             "exclusions": ["Consulting firms"], "excluded_domains": ["rivalco.example"]}
LONG = ("Ardent Castings to acquire Hollow Pine Foundry. The deal closes in December. "
        "Ardent Castings said CIO Maria Lopez will lead the integration of both plants.")


class Search:
    """Search results carry a short preview; read() gives the full page."""
    name = "test"

    def __init__(self):
        self.queries, self.reads = [], []

    def search(self, q, start_date=None, news=False):
        self.queries.append(q)
        return [{"title": "Ardent Castings to acquire Hollow Pine Foundry", "url": "https://news.example/ardent",
                 "content": "Ardent Castings to acquire Hollow Pine Foundry.", "published": "2026-10-10"},
                {"title": "RivalCo buys a plant", "url": "https://news.example/rival",
                 "content": "RivalCo (rivalco.example) completed the acquisition of a plant.", "published": "2026-10-01"},
                {"title": "Kestrel Foods acquires snack brands", "url": "https://news.example/kestrel",
                 "content": "Kestrel Foods closes the acquisition of two snack brands.", "published": "2026-09-01"}]

    def read(self, url):
        self.reads.append(url)
        return {"url": url, "content": LONG} if url.endswith("ardent") else None


def tools(search=None, known=frozenset({"kestrel foods"}), config=None):
    spec, record = D.discovery_agent(search or Search(), DIRECTION, "Foundries after an acquisition", set(known), TODAY, config)
    return {t.name: t.run for t in spec.tools + [spec.finish]}, record


def nominate(company, quote, url="https://news.example/ardent", **kw):
    return {"company": company, "url": url, "why": "Acquired a foundry.", "signal": "Acquisition", "quote": quote, **kw}


def test_the_quote_must_be_in_the_source_and_reading_the_page_can_supply_it():
    t, record = tools()
    t["search_news"]({"query": "foundry acquisition"})
    quote = "Ardent Castings said CIO Maria Lopez will lead the integration of both plants."
    with pytest.raises(R.ToolError, match="word for word"):  # the preview is cut off before this sentence
        t["nominate"](nominate("Ardent Castings", quote))
    page = t["read_page"]({"url": "https://news.example/ardent"})
    assert "Maria Lopez" in page["text"]
    ok = t["nominate"](nominate("Ardent Castings", quote, person_name="Maria Lopez", person_title="CIO"))
    assert ok["accepted"] and record["kept"][0]["person"] == "Maria Lopez" and record["kept"][0]["quote"] == quote
    assert record["kept"][0]["signal_date"] == "2026-10-10" and record["dropped"] == {"quote_not_in_source": 1}


def test_code_rejects_what_the_direction_or_the_workspace_rules_out():
    t, record = tools()
    t["search_news"]({"query": "acquisition"})
    with pytest.raises(R.ToolError, match="search_news returned"):
        t["nominate"](nominate("Ardent Castings", "Ardent Castings to acquire Hollow Pine Foundry.", url="https://elsewhere.example/x"))
    with pytest.raises(R.ToolError, match="excludes"):
        t["nominate"](nominate("RivalCo", "RivalCo (rivalco.example) completed the acquisition of a plant.",
                               url="https://news.example/rival", domain="rivalco.example"))
    with pytest.raises(R.ToolError, match="already has"):
        t["nominate"](nominate("Kestrel Foods", "Kestrel Foods closes the acquisition of two snack brands.", url="https://news.example/kestrel"))
    assert t["check_company"]({"company": "Kestrel Foods", "domain": ""})["already_known"]
    assert t["check_company"]({"company": "X", "domain": "plant.rivalco.example"})["website_excluded"]
    ok = t["nominate"](nominate("Ardent Castings", "Ardent Castings to acquire Hollow Pine Foundry.", person_name="Invented Person"))
    assert "left out" in ok["note"] and record["kept"][0]["person"] == "" and record["kept"][0]["flags"] == ["Contact to confirm"]
    with pytest.raises(R.ToolError, match="already nominated"):
        t["nominate"](nominate("Ardent Castings", "Ardent Castings to acquire Hollow Pine Foundry."))
    assert record["dropped"] == {"no_source": 1, "excluded": 1, "already_known": 1, "duplicate": 1}


def test_searches_and_reads_are_capped_and_reads_stay_on_found_pages():
    search = Search()
    t, record = tools(search)
    with pytest.raises(R.ToolError, match="search_news returned"):
        t["read_page"]({"url": "https://news.example/ardent"})
    for i in range(D.MAX_AGENT_SEARCHES):
        t["search_news"]({"query": f"acquisition {i}"})
    with pytest.raises(R.ToolError, match="used up"):
        t["search_news"]({"query": "one more"})
    assert len(search.queries) == D.MAX_AGENT_SEARCHES and len(record["found"]) == 3  # the same url is kept once
    for _ in range(D.MAX_READS):
        t["read_page"]({"url": "https://news.example/rival"})
    with pytest.raises(R.ToolError, match="used up"):
        t["read_page"]({"url": "https://news.example/rival"})


def test_custom_queries_are_passed_to_the_agent():
    t, _ = tools(config={"custom_queries": ["foundry merger Ohio"]})
    d = t["direction"]({})
    assert d["run_these_searches_first"] == ["foundry merger Ohio"] and d["consultant_request"] == "Foundries after an acquisition"
    assert d["today"] == "2026-10-28" and d["buyer_roles"] == ["CIO", "COO"]


def test_a_full_round_with_the_fake_agent(tmp_path):
    def pick(direction, found):
        return [nominate("Ardent Castings", "Ardent Castings to acquire Hollow Pine Foundry.", url=found[0]["url"])]

    p = make_provider("gemini", "m", key="k", transport=lambda url, headers, payload, **kw: play(payload, discovery=pick))
    debug = tmp_path / "round.json"
    (kept, info), usage, trace = D.discover_with_agent(p, Search(), DIRECTION, "", set(), TODAY, debug_file=debug)
    assert [k["company"] for k in kept] == ["Ardent Castings"] and p.calls == 4
    assert info["searches"] == 1 and info["ai_nominations"] == 1 and info["search_results"] == 3 and "stopped" not in info
    assert trace[-1]["type"] == "finish" and usage.tokens_in == 20
    assert '"agent": "discovery-agent-1.0"' in debug.read_text(encoding="utf-8")


def test_the_budget_stops_the_agent_and_keeps_what_it_checked():
    calls = []

    def ai(url, headers, payload, **kw):
        calls.append(1)
        return play(payload, discovery=lambda d, found: [nominate("Ardent Castings", "Ardent Castings to acquire Hollow Pine Foundry.")])

    p = make_provider("gemini", "m", key="k", transport=ai)
    (kept, info), _, trace = D.discover_with_agent(p, Search(), DIRECTION, "", set(), TODAY, may_call=lambda: p.calls < 3)
    assert len(calls) == 3 and [k["company"] for k in kept] == ["Ardent Castings"]  # nominated on the third turn, then stopped
    assert "stopped" in info and trace[-1]["type"] == "stop"


def test_the_weekly_round_uses_the_agents_ai_budget(tmp_path, monkeypatch):
    monkeypatch.delenv("WEB_MODE", raising=False)  # synthetic internet
    db = tmp_path / "w.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db))
    turns = []

    def ai(url, headers, payload, **kw):
        turns.append(1)
        return play(payload)

    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=ai)
    api = TestClient(app)
    api.patch("/api/agents", headers=ME, json={"ai_budget": 2})
    with app.state.db.session() as s:
        c = AU.agent_ctx(app, s)
        status, text = AU.do_discovery_round(c, None, None)  # the weekly job's work, with 2 AI requests left today
        s.commit()
    assert status == "done" and "no new candidates" in text and len(turns) == 2  # stopped at the budget, mid-round
    run = next(r for r in api.get("/api/agent-runs").json() if r["agent"] == "Discovery agent")
    assert run["prompt"] == "discovery-agent-1.0" and run["calls"] == 2
    steps = api.get(f"/api/agent-runs/{run['id']}").json()["trace"]
    assert steps[-1]["type"] == "stop"


def test_a_turn_with_text_only_is_nudged_back_to_the_tools():
    replies = iter([{"candidates": [{"content": {"role": "model", "parts": [{"text": "I will look for companies."}]}}], "usageMetadata": {}},
                    turn(("finish", {"summary": "Nothing to nominate."}))])
    p = make_provider("gemini", "m", key="k", transport=lambda url, headers, payload, **kw: next(replies))
    (kept, info), _, trace = D.discover_with_agent(p, Search(), DIRECTION, "", set(), TODAY)
    assert kept == [] and info["searches"] == 0 and trace[-1]["type"] == "finish"
