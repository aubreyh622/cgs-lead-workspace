"""Multi-agent step 6: company research (Steps 2-3) as a tool-using agent. Runs on the synthetic internet with a fake AI."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api import agent_runtime as R
from api import research_agent as RA
from api.llm import make_provider
from api.main import create_app
from api.research_bridge import card_to_brief
from api.search import MockSearch
from api.settings import Settings
from agent_fakes import play, turn

ME = {"X-User": "Alex Morgan"}
NEWS = "https://www.plantnews.example/2026/08/northwind-fluid-systems-names-priya-desai-its-first"
LEADERS = "https://www.northwindfluid.example/leadership"


def tools(as_of=date(2026, 10, 28)):
    spec, record = RA.research_agent(MockSearch(as_of=as_of), "Northwind Fluid Systems", "northwindfluid.example")
    return {t.name: t.run for t in spec.tools + [spec.finish]}, record


FINISH = {"identity_confirmed": True, "hypothesis": "The new CDO may need help scaling the AI pilots across plants.",
          "relevance": "CGS designs operating models for scaling pilots.", "alternative": "The pilots may be scaled by the vendor.",
          "unknowns": ["Who owns the budget?", "Which plants are next?"], "confidence": "medium"}


def test_facts_and_the_contact_come_only_from_pages_the_agent_read():
    t, record = tools()
    found = t["company_pages"]({"topic": "leadership news"})["pages"]
    assert NEWS in [p["url"] for p in found] and LEADERS in [p["url"] for p in found]
    quote = "Northwind Fluid Systems has appointed Priya Desai as its first Chief Digital Officer, reporting to CEO Gregory Hale."
    with pytest.raises(R.ToolError, match="Read that page"):
        t["add_fact"]({"url": NEWS, "quote": quote, "fact": "Northwind named its first CDO.", "category": "signal"})
    t["read_page"]({"url": NEWS})
    with pytest.raises(R.ToolError, match="word for word"):
        t["add_fact"]({"url": NEWS, "quote": "Northwind has hired three new CDOs this year.", "fact": "Hiring spree.", "category": "signal"})
    assert t["add_fact"]({"url": NEWS, "quote": quote, "fact": "Northwind named its first CDO in August 2026.", "category": "signal"})["date"] == "2026-08-18"
    with pytest.raises(R.ToolError, match="already recorded"):
        t["add_fact"]({"url": NEWS, "quote": quote, "fact": "The same again, in other words.", "category": "person"})
    with pytest.raises(R.ToolError, match="Read that page"):
        t["set_contact"]({"name": "Priya Desai", "title": "Chief Digital Officer", "url": LEADERS, "quote": "Priya Desai, Chief Digital Officer"})
    t["read_page"]({"url": LEADERS})
    with pytest.raises(R.ToolError, match="names this person"):
        t["set_contact"]({"name": "Invented Person", "title": "COO", "url": LEADERS, "quote": "Gregory Hale, President and CEO."})
    assert t["set_contact"]({"name": "Priya Desai", "title": "Chief Digital Officer (oversees the AI pilots)", "url": LEADERS,
                             "quote": "Priya Desai, Chief Digital Officer, joined in August 2026 and reports to the CEO."})["accepted"]
    assert t["finish"](FINISH) == {"facts": 1, "contact": True}
    assert record["dropped"] == {"not_read": 2, "quote_not_found": 1, "duplicate": 1, "person_not_on_page": 1}
    brief, person = card_to_brief(record["card"], "Alex Morgan", "2026-11-01", {"searches": 1, "extractions": 2, "model_calls": 5})
    assert brief["facts"][0]["src"] == NEWS and brief["facts"][0]["date"] == "2026-08-18" and brief["facts"][0]["quote"] == quote
    assert person == {"name": "Priya Desai", "title": "Chief Digital Officer"}
    assert brief["recommendation"]["decision"] == "accept" and brief["unknowns"] == ["Who owns the budget?", "Which plants are next?"]


def test_finish_needs_a_fact_and_a_hypothesis_and_searches_are_capped():
    t, record = tools()
    with pytest.raises(R.ToolError, match="at least one fact"):
        t["finish"](FINISH)
    for i in range(RA.MAX_SEARCHES):
        t["search_web"]({"query": f"Northwind pumps {i}"})
    with pytest.raises(R.ToolError, match="used up"):
        t["company_pages"]({"topic": "news"})
    assert record["searches"] == RA.MAX_SEARCHES


class SnippetWeb:
    """Like the real web: search returns snippets without dates; read() returns the whole page."""
    name = "snippets"
    PAGE = ("Crawfordsville, Ind. (Oct. 8, 2026) - Northwind Fluid Systems will expand its Iowa plant. "
            "The company said the expansion adds 100 jobs. Priya Desai, Chief Digital Officer, will lead the automation work.")

    def __init__(self):
        self.reads = []

    def search(self, query, start_date=None, news=False):
        return [{"title": "Northwind expands", "url": "https://news.example/nw", "content": self.PAGE[:60], "published": ""}]

    def read(self, url):
        self.reads.append(url)
        return {"url": url, "title": "", "content": self.PAGE, "published": ""}


def test_on_the_real_web_reading_fetches_the_whole_page_and_dates_are_checked_against_it():
    web = SnippetWeb()
    spec, record = RA.research_agent(web, "Northwind Fluid Systems", "northwindfluid.example")
    t = {x.name: x.run for x in spec.tools + [spec.finish]}
    t["search_web"]({"query": "Northwind expansion"})
    page = t["read_page"]({"url": "https://news.example/nw"})
    assert "100 jobs" in page["text"] and web.reads == ["https://news.example/nw"]
    t["read_page"]({"url": "https://news.example/nw"})
    assert web.reads == ["https://news.example/nw"]  # fetched once
    fact = {"url": "https://news.example/nw", "quote": "The company said the expansion adds 100 jobs.",
            "fact": "Northwind is expanding its Iowa plant with 100 jobs.", "category": "signal"}
    with pytest.raises(R.ToolError, match="does not show the date"):
        t["add_fact"]({**fact, "date": "2026-10-18"})
    assert t["add_fact"]({**fact, "date": "2026-10-08"})["date"] == "2026-10-08"
    with pytest.raises(R.ToolError, match="already recorded this fact"):  # the same fact, quoted from another sentence
        t["add_fact"]({**fact, "quote": "Northwind Fluid Systems will expand its Iowa plant.", "fact": "Northwind is expanding the Iowa plant, adding 100 jobs."})
    assert t["add_fact"]({**fact, "quote": "Northwind Fluid Systems will expand its Iowa plant.", "date": "",
                          "fact": "The company plans a plant expansion in Iowa."})["date"] == "undated"
    with pytest.raises(R.ToolError, match="another explanation"):
        t["finish"]({**FINISH, "alternative": ""})
    with pytest.raises(R.ToolError, match="open question"):
        t["finish"]({**FINISH, "unknowns": []})
    assert t["finish"](FINISH)["facts"] == 2


@pytest.mark.parametrize("iso,text,ok", [
    ("2026-10-08", "(Oct. 8, 2026)", True), ("2026-10-08", "on October 8, 2026 the", True), ("2026-10-08", "8 October 2026", True),
    ("2026-09-30", "Sept 30, 2026", True), ("2026-10-08", "10/8/2026", True), ("2026-10-08", "2026-10-08T09:00", True),
    ("2026-10-08", "October 2026", False), ("2026-13-01", "anything", False)])
def test_date_on_page(iso, text, ok):
    assert RA.date_on_page(iso, text) is ok


def test_tavily_dates_are_read_as_iso():
    from api.search import iso_date
    assert iso_date("Tue, 27 Oct 2026 10:00:00 GMT") == "2026-10-27"
    assert iso_date("2026-10-08") == "2026-10-08" and iso_date("2026-10-08T09:00:00Z") == "2026-10-08"
    assert iso_date("last week") == "" and iso_date(None) == ""


def test_the_simulated_date_hides_later_news():
    t, _ = tools(as_of=date(2026, 7, 1))  # before the CDO announcement
    assert NEWS not in [p["url"] for p in t["company_pages"]({"topic": "news"})["pages"]]


def test_the_bridge_runs_the_agent_and_reports_like_the_pipeline():
    p = make_provider("gemini", "m", key="k", transport=lambda url, headers, payload, **kw: play(payload))
    bridge = RA.AgentResearchBridge(lambda: p, lambda as_of: MockSearch(as_of=as_of), background=False)
    run = bridge.start("Northwind Fluid Systems", "northwindfluid.example", "Alex Morgan", as_of=date(2026, 10, 28))
    st = bridge.status(run)
    assert st["status"] == "completed" and st["usage"]["model_calls"] == 4 and st["usage"]["searches"] == 1 and st["usage"]["extractions"] == 3
    assert any(e.startswith("Read page") for e in st["events"])
    assert bridge.result(run)["research"]["evidence"] and bridge.trace(run)[-1]["type"] == "finish"
    assert bridge.status("agent-gone")["status"] == "failed"


def test_an_agent_that_never_finishes_ends_the_run_as_failed():
    p = make_provider("gemini", "m", key="k", transport=lambda url, headers, payload, **kw: turn(("finish", FINISH)))
    bridge = RA.AgentResearchBridge(lambda: p, lambda as_of: MockSearch(), background=False)
    st = bridge.status(bridge.start("Northwind Fluid Systems", "northwindfluid.example", "Alex Morgan"))
    assert st["status"] == "failed" and "without a brief" in st["error"]

    def broken():
        raise RuntimeError("boom")
    bridge = RA.AgentResearchBridge(broken, lambda as_of: MockSearch(), background=False)
    st = bridge.status(bridge.start("Northwind Fluid Systems", "northwindfluid.example", "Alex Morgan"))
    assert st["status"] == "failed" and "crashed" in st["error"]


def test_live_research_uses_the_agent_by_default_and_keeps_its_steps(tmp_path, monkeypatch):
    monkeypatch.delenv("WEB_MODE", raising=False)
    tasks = []

    def ai(url, headers, payload, **kw):
        tasks.append(payload["contents"][0]["parts"][0]["text"])
        return play(payload)

    create_app(Settings(app_mode="demo", db_path=tmp_path / "b.sqlite3")).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=tmp_path / "b.sqlite3"))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=ai)
    api = TestClient(app)
    oid = api.post("/api/opportunities", json={"company": "Northwind Fluid Systems"}, headers=ME).json()["id"]
    assert isinstance(app.state.research_bridge, RA.AgentResearchBridge)
    api.post("/api/research/sync", headers=ME)
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["stage"] == "review" and o["brief"]["facts"] and all(f["src"].startswith("https://") for f in o["brief"]["facts"])
    run = next(r for r in api.get("/api/agent-runs").json() if r["agent"] == "Research agent")
    assert run["prompt"] == RA.VERSION and run["model"] == "gemini:m+mock" and run["steps"]
    # the reviewer sends the brief back with a question: the next run is told to answer it first
    r = api.post(f"/api/opportunities/{oid}/step4", headers=ME, json={"decision": "return", "questions": "Who runs the AI pilots day to day?"})
    assert r.status_code == 200, r.text
    api.post("/api/research/sync", headers=ME)
    assert "Who runs the AI pilots day to day?" in tasks[-1]


def test_the_pipeline_engine_is_still_available(tmp_path):
    from api.mock_research import MockResearchBridge
    from api.actions import research_bridge
    app = create_app(Settings(app_mode="live", db_path=tmp_path / "p.sqlite3", research_engine="pipeline"))
    assert isinstance(research_bridge(app), MockResearchBridge)
    with pytest.raises(ValueError, match="RESEARCH_ENGINE"):
        Settings(research_engine="other")


# ---------- the other two tasks: contact and refresh ----------
KESSLER_NEW = "https://www.manufacturingtoday.example/2027/03/kessler-motion-arcwell-phase-one-complete"
KESSLER_OLD = "https://www.industrywire.example/2026/10/kessler-motion-controls-and-arcwell-drives-complete-merger"


def test_refresh_keeps_only_news_since_the_brief_from_new_pages():
    spec, record = RA.refresh_agent(MockSearch(as_of=date(2027, 4, 2)), "Kessler Motion Controls", "kesslermotion.example",
                                    date(2026, 10, 2), {KESSLER_OLD})
    t = {x.name: x.run for x in spec.tools + [spec.finish]}
    assert {x.name for x in spec.tools} >= {"add_news", "read_page"} and "set_contact" not in {x.name for x in spec.tools}
    t["company_pages"]({"topic": "news"})
    old = t["read_page"]({"url": KESSLER_OLD})["text"].split(". ")[0] + "."
    with pytest.raises(R.ToolError, match="already a source in the brief"):
        t["add_news"]({"url": KESSLER_OLD, "quote": old, "fact": "Kessler and Arcwell completed their merger.", "category": "signal"})
    new = t["read_page"]({"url": KESSLER_NEW})["text"].split(". ")[0]
    assert t["add_news"]({"url": KESSLER_NEW, "quote": new, "fact": "Kessler moved all nine plants onto one ERP system.",
                          "category": "signal", "date": "2027-03-23"})["date"] == "2027-03-23"
    assert t["finish"]({"summary": "One new development."})["news"] == 1


def test_refresh_refuses_old_or_undated_news():
    spec, record = RA.refresh_agent(MockSearch(as_of=date(2027, 4, 2)), "Kessler Motion Controls", "kesslermotion.example",
                                    date(2027, 3, 30), set())
    t = {x.name: x.run for x in spec.tools + [spec.finish]}
    t["company_pages"]({"topic": "news"})
    new = t["read_page"]({"url": KESSLER_NEW})["text"].split(". ")[0]
    with pytest.raises(R.ToolError, match="Only news after 2027-03-30"):  # published 23 March, before the brief's last date
        t["add_news"]({"url": KESSLER_NEW, "quote": new, "fact": "Kessler moved all nine plants onto one ERP system.", "category": "signal"})
    assert record["dropped"] == {"not_new": 1}


def test_contact_and_refresh_stop_at_the_daily_ai_budget():
    calls = []

    def ai(url, headers, payload, **kw):
        calls.append(1)
        return play(payload)

    p = make_provider("gemini", "m", key="k", transport=ai)
    answer, _, trace = RA.find_contact(p, MockSearch(), "Orchard Lane Foods", "orchardlanefoods.example", "CEO", "the CEO would decide",
                                       [], may_call=lambda: False)
    assert answer["found"] is False and calls == [] and trace[-1]["type"] == "stop"
    facts, _, trace = RA.refresh(p, MockSearch(as_of=date(2027, 4, 2)), "Kessler Motion Controls", "kesslermotion.example",
                                 date(2026, 10, 2), set(), may_call=lambda: p.calls < 2)
    assert facts == [] and len(calls) == 2 and trace[-1]["type"] == "stop"  # stopped after reading, before recording anything


# ---------- company size: the direction's range, checked in research ----------
ARDENT = "https://www.ardentcastings.example/"
ARDENT_SIZE = "It runs foundries in Ohio, Indiana and Michigan and employs about 1,900 people."


def size_tools(limits):
    spec, record = RA.research_agent(MockSearch(as_of=date(2026, 10, 28)), "Ardent Castings", "ardentcastings.example", limits)
    t = {x.name: x.run for x in spec.tools + [spec.finish]}
    t["company_pages"]({"topic": "about"})
    t["read_page"]({"url": ARDENT})
    return t, record


def test_size_must_be_stated_in_the_quote_and_is_compared_with_the_range():
    t, record = size_tools({"max_employees": 1000})
    with pytest.raises(R.ToolError, match="does not state that employee count"):
        t["record_size"]({"employees": 2500, "revenue_musd": 0, "url": ARDENT, "quote": ARDENT_SIZE})
    out = t["record_size"]({"employees": 1900, "revenue_musd": 0, "url": ARDENT, "quote": ARDENT_SIZE})
    assert "outside the range 0 to 1,000" in out["outside_the_direction"]
    t["add_fact"]({"url": ARDENT, "quote": ARDENT_SIZE, "fact": "Ardent runs foundries in three states with about 1,900 staff.", "category": "identity"})
    t["finish"](FINISH)
    brief, _ = card_to_brief(record["card"], "Alex Morgan", "2026-11-01", {})
    assert record["card"]["research"]["eligibility"] == "ineligible"
    assert any(f["type"] == "Outside the direction" and "1,900" in f["text"] for f in brief["flags"])


def test_inside_the_range_or_no_range_means_no_size_flag_and_a_missing_size_is_a_research_gap():
    t, record = size_tools({"min_employees": 100, "max_employees": 5000})
    t["record_size"]({"employees": 1900, "revenue_musd": 0, "url": ARDENT, "quote": ARDENT_SIZE})
    t["add_fact"]({"url": ARDENT, "quote": ARDENT_SIZE, "fact": "Ardent runs foundries in three states with about 1,900 staff.", "category": "identity"})
    t["finish"](FINISH)
    assert record["card"]["research"]["eligibility"] == "eligible" and not record["card"]["validation_issues"]
    t, record = size_tools({"max_employees": 5000})  # a range, but the agent never records a size
    t["add_fact"]({"url": ARDENT, "quote": ARDENT_SIZE, "fact": "Ardent runs foundries in three states with about 1,900 staff.", "category": "identity"})
    t["finish"](FINISH)
    brief, _ = card_to_brief(record["card"], "Alex Morgan", "2026-11-01", {})
    assert any(f["type"] == "Research gap" and "size not found" in f["text"] for f in brief["flags"])


def test_the_size_range_is_set_through_an_approved_direction_change(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "d.sqlite3")))
    assert api.get("/api/direction").json()["current"]["company_size"] == {}
    bad = api.post("/api/direction/proposals", headers=ME, json={"field": "company_size", "value": {"min_employees": 900, "max_employees": 100},
                                                                  "rationale": "CGS works with mid-sized manufacturers"})
    assert bad.status_code == 400
    pid = api.post("/api/direction/proposals", headers=ME, json={"field": "company_size", "value": {"min_employees": 100, "max_employees": 5000},
                                                                  "rationale": "CGS works with mid-sized manufacturers"}).json()["id"]
    api.post(f"/api/direction/proposals/{pid}/approve", headers=ME)
    current = api.get("/api/direction").json()["current"]
    assert current["company_size"] == {"min_employees": 100, "max_employees": 5000} and current["change"] == "Change company size"
