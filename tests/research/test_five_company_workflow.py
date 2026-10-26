"""Controlled provider responses only; these are not measured real-company results."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from lead_agent.assessment import make_lead, person_issues
from lead_agent.demo import fixture
from lead_agent.models import Action, Person, Review
from lead_agent.providers import LiveProviders
from lead_agent.worker import Worker, new_payload
from lead_agent.models import Limits
from test_identification import FakeProviders


def selected_passage_record(research, context):
    """Controlled model output selects passage IDs, never authors source quotes."""
    record = research.model_dump(mode="json")
    aliases = {}
    for observation in record["evidence"]:
        selected = next(q for q in context["evidence_bank"] if observation["excerpt"] in q["excerpt"])
        aliases[observation.pop("evidence_id")] = selected["quote_id"]
        observation.pop("excerpt")
        observation.pop("source_id")
        observation["quote_id"] = selected["quote_id"]
    for component in record["components"].values():
        component["evidence_ids"] = [aliases.get(ref, ref) for ref in component["evidence_ids"]]
    return json.dumps(record)


def test_five_companies_over_http_adapter_with_one_inaccessible(monkeypatch, store, brief, rubric):
    """Exercise the real adapter, action schema, tool routing and SQLite together."""
    today = datetime.now(timezone.utc).date()
    fixtures = [fixture(today=today, number=i) for i in range(1, 6)]
    seeds = [{"company": r.company, "domain": r.domain} for r, _ in fixtures]
    stages = {}
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(str(request.url))
        if "generateContent" in request.url.path:
            assert request.headers["x-goog-api-key"] == "fixture-gemini-key"
            assert body["generationConfig"]["responseJsonSchema"]["properties"]["tool"]
            raw = body["systemInstruction"]["parts"][0]["text"].split("Configuration and current run: ", 1)[1]
            context, _ = json.JSONDecoder().raw_decode(raw)
            seed = context["current_company"]
            index = next(i for i, pair in enumerate(fixtures) if pair[0].domain == seed["domain"])
            stage = stages.get(index, 0)
            stages[index] = stage + 1
            if stage == 0:
                tool, args = "read_page", {"url": f"https://{seed['domain']}/"}
            elif index == 0 or stage == 3:
                tool, args = "finish", {}
            elif stage == 1:
                tool, args = "read_page", {"url": f"https://{seed['domain']}/news"}
            else:
                r, sources = fixtures[index]
                # Old source content is present independently of message-window truncation.
                assert all(any(s.content in p["content"] for p in context["stored_sources"]) for s in sources)
                source_ids = {("s1" if s["url"].endswith("/news") else "s0"): s["source_id"]
                              for s in context["stored_sources"]}
                for e in r.evidence:
                    e.source_id = source_ids[e.source_id]
                tool, args = "record_lead", {"research": r.model_dump(mode="json")}
            action = Action(tool=tool, arguments_json=json.dumps(args), reason="Controlled workflow test")
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": action.model_dump_json()}]}}]})
        assert request.url.path == "/extract"
        assert request.headers["authorization"] == "Bearer fixture-tavily-key"
        url = body["urls"][0]
        index = next(i for i, pair in enumerate(fixtures) if pair[0].domain in url)
        if index == 0:
            return httpx.Response(200, json={"results": [], "failed_results": [{"url": url, "error": "Blocked"}]})
        _, sources = fixtures[index]
        content = sources[1].content if url.endswith("/news") else sources[0].content + f"\nhttps://{seeds[index]['domain']}/news"
        return httpx.Response(200, json={"results": [{"url": url, "raw_content": content}], "usage": {"credits": .2}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        live = LiveProviders("fixture-gemini-key", "fixture-tavily-key", "gemini-fixture")
        run = store.create_run(new_payload(brief, rubric, seeds, strategy="seeds", model=live.model, research_date=today))
        Worker(store, live).execute(run)
    row = store.run(run)
    cards = store.leads(run)
    assert row["status"] == "completed", row["error"]
    assert row["payload"]["seed_index"] == 5
    assert len(cards) == len({l.lead_id for l in cards}) == 5
    assert cards[0].research.evidence == [] and cards[0].review.decision == "Needs research"
    assert all(l.review.decision == "Ready for review" and not l.validation_issues for l in cards[1:])
    assert len(requests) == 27  # 18 model decisions and 9 retrievals, including the failed page.
    assert row["payload"]["counters"]["extractions"] == 9
    assert "fixture-gemini-key" not in json.dumps(row)
    assert "fixture-tavily-key" not in json.dumps(row)


def test_one_supported_signal_page_is_useful_without_contact(brief, rubric):
    r, sources = fixture()
    sources[0].content += "\n" + sources[1].content
    for e in r.evidence:
        e.source_id = "s0"
    r.evidence[-1].category = "signal"
    r.components["problem"].assessment = "medium"
    card = make_lead("run", r, sources[:1], brief, rubric, date(2026, 10, 20))
    assert not card.validation_issues
    assert card.priority.score is not None
    assert card.research.people is None
    assert any("Only one source" in x for x in card.research_gaps)


def test_undated_signal_preserves_company_priority_with_unknown_timing(brief, rubric):
    r, sources = fixture()
    r.evidence[-1].publication_date = None
    r.evidence[-1].date_excerpt = None
    card = make_lead("run", r, sources, brief, rubric, date(2026, 10, 20))
    assert not card.validation_issues
    assert card.priority.score is not None
    assert card.priority.components["timing"].assessment is None
    assert any("Timing is unknown" in gap for gap in card.research_gaps)


def test_equivalent_source_references_normalize_without_changing_quotes(brief, rubric):
    r, sources = fixture()
    r.evidence = [e for e in r.evidence if e.category != "identity"]
    r.evidence[-1].source_id = sources[1].url
    r.components["problem"].evidence_ids = ["s1"]
    card = make_lead("run", r, sources, brief, rubric, date(2026, 10, 20))
    assert not card.validation_issues
    assert card.research.components["problem"].evidence_ids == ["problem"]
    assert any(e.category == "identity" for e in card.research.evidence)
    assert card.research.evidence[0].excerpt == r.evidence[0].excerpt


def test_ambiguous_numeric_date_is_unknown_instead_of_a_guessed_calendar_day(brief, rubric):
    r, sources = fixture()
    sources[1].content += "\n10.08.26"
    r.evidence[-1].publication_date = date(2026, 10, 8)
    r.evidence[-1].date_excerpt = "10.08.26"
    card = make_lead("run", r, sources, brief, rubric, date(2026, 10, 20))
    assert card.research.evidence[-1].publication_date is None
    assert card.research.components["timing"].assessment is None
    assert any("ambiguous" in gap for gap in card.research_gaps)
    assert not card.validation_issues


def test_unknown_eligibility_keeps_score_but_requires_research(store, brief, rubric):
    r, sources = fixture()
    r.eligibility = "unknown"
    r.evidence = [e for e in r.evidence if e.category != "geography"]
    run = store.create_run(new_payload(brief, rubric, []))
    card = make_lead(run, r, sources, brief, rubric, date(2026, 10, 20))
    store.add_lead(card)
    assert card.priority.score is not None and card.review.decision == "Needs research"
    with pytest.raises(ValueError, match="eligibility"):
        store.review(run, card.lead_id, Review(decision="Approved for handoff", reviewer="Tester",
                     reviewed_at=datetime.now(timezone.utc), source_support_checked=True))


def test_undated_official_role_and_invalid_contact_do_not_block_company(brief, rubric):
    today = date(2026, 10, 20)
    r, sources = fixture()
    text = "Morgan Example, Chief Information Officer, Synthetic Factory 1 leadership."
    sources[0].content += "\n" + text
    r.evidence.append(type(r.evidence[0])(evidence_id="buyer", category="person", source_id="s0",
                      excerpt=text, observed_fact="The official leadership page lists Morgan Example as Chief Information Officer."))
    person = Person(name="Morgan Example", role="Chief Information Officer", company=r.company,
                    evidence_ids=["buyer"], verified_at=today)
    r.people = [person]
    assert person_issues(person, r, sources, brief, today) == []
    assert make_lead("run", r, sources, brief, rubric, today).research.people
    person.role = "Unsupported CEO title"
    card = make_lead("run", r, sources, brief, rubric, today)
    assert card.research.people is None and card.priority.score is not None
    assert any("proposed buyer" in x for x in card.research_gaps)


def test_enrichment_preserves_history_and_invalidates_old_review(store, brief, rubric):
    r, sources = fixture()
    run = store.create_run(new_payload(brief, rubric, []))
    original = make_lead(run, r, sources, brief, rubric, date(2026, 10, 20))
    assert store.save_research(original) == "created"
    store.review(run, original.lead_id, Review(decision="Deferred", reviewer="Tester",
                 reviewed_at=datetime.now(timezone.utc), notes="Need more evidence"))
    r.suggested_next_investigation = "Investigate the newly discovered planning deadline with the strategy team."
    improved = make_lead(run, r, sources, brief, rubric, date(2026, 10, 20))
    assert store.save_research(improved) == "updated"
    assert store.leads(run)[0].review.reviewer is None
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM research_revisions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1
    assert store.save_research(improved) == "unchanged"


def test_simplified_app_requires_keys_before_creating_run(monkeypatch, store):
    import lead_agent.storage
    monkeypatch.setattr(lead_agent.storage, "Store", lambda _: store)
    for key in ("GEMINI_API_KEY", "TAVILY_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(key, raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "app.py"), default_timeout=20).run()
    next(b for b in app.button if b.label == "Research company").click().run()
    assert not app.exception and store.runs() == []
    assert any("keys" in error.value for error in app.error)
    next(r for r in app.radio if r.label == "Research scope").set_value("Five-company batch").run()
    assert not app.exception
    assert next(b for b in app.button if b.label == "Research five companies")


def test_connection_probe_checks_all_three_routes_and_redacts_errors(monkeypatch):
    calls = []
    def respond(request):
        calls.append(str(request.url))
        if "generateContent" in request.url.path:
            action = Action(tool="finish", arguments_json="{}", reason="Probe")
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": action.model_dump_json()}]}}]})
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [{"url": "https://ford.com/"}]})
        return httpx.Response(401, json={"error": {"message": "fixture-tavily-secret is invalid"}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        result = LiveProviders("fixture-gemini-secret", "fixture-tavily-secret", "gemini-fixture").test_connections()
    assert len(calls) == 3 and [r["ok"] for r in result] == [True, True, False]
    assert "fixture-tavily-secret" not in json.dumps(result)


def test_one_company_cannot_consume_the_batch_search_allowance(store, brief, rubric):
    seeds = [{"company": f"Company {i}", "domain": f"company-{i}.example"} for i in range(5)]
    class Searching(FakeProviders):
        def action(self, prompt, messages):
            self.action_calls += 1
            if '"company": "Company 0"' in prompt.split('"current_company":', 1)[1].split('"stored_sources":', 1)[0]:
                pair = ("search", {"query": f"different company query {self.action_calls}"})
            else:
                pair = ("finish", {})
            return Action(tool=pair[0], arguments_json=json.dumps(pair[1]), reason="Bounded fixture").model_dump_json(), {}
    run = store.create_run(new_payload(brief, rubric, seeds, strategy="seeds", limits=Limits(searches=10)))
    Worker(store, Searching()).execute(run)
    assert store.run(run)["status"] == "completed"
    assert store.run(run)["payload"]["counters"]["searches"] == 2
    assert len(store.leads(run)) == 5


def test_batch_resumes_at_current_company_without_restarting_finished_companies(store, brief, rubric):
    seeds = [{"company": f"Company {i}", "domain": f"company-{i}.example"} for i in range(5)]
    class Stopping(FakeProviders):
        def action(self, prompt, messages):
            answer = super().action(prompt, messages)
            if self.action_calls == 2:
                store.stop(run)
            return answer
    run = store.create_run(new_payload(brief, rubric, seeds, strategy="seeds"))
    Worker(store, Stopping()).execute(run)
    assert store.run(run)["status"] == "paused"
    assert store.run(run)["payload"]["seed_index"] == 1
    first = store.leads(run)[0].model_dump_json()
    Worker(store, FakeProviders()).execute(run)
    assert store.run(run)["status"] == "completed"
    assert store.run(run)["payload"]["counters"]["model_calls"] == 5
    assert store.leads(run)[0].model_dump_json() == first
    assert len(store.leads(run)) == 5


def test_official_subdomain_redirect_is_cached_and_unrelated_site_is_rejected(store, brief, rubric):
    class Ford(FakeProviders):
        def read_page(self, url):
            return {"results": [{"url": "https://www.ford.com/about/", "raw_content": "Ford Motor Company corporate information."}]}
    provider = Ford([("read_page", {"url": "https://corporate.ford.com/"}),
                     ("read_page", {"url": "https://corporate.ford.com/"}),
                     ("read_page", {"url": "https://unrelated.example/"}), ("finish", {})])
    run = store.create_run(new_payload(brief, rubric, [{"company": "Ford", "domain": "ford.com"}], strategy="seeds"))
    Worker(store, provider).execute(run)
    state = store.run(run)["payload"]
    assert store.run(run)["status"] == "completed"
    assert state["counters"]["extractions"] == 1
    assert state["sources"][0]["url"] == "https://www.ford.com/about"
    assert any("Search for other URLs first" in e["message"] for e in state["events"])


def test_pacing_survives_a_quota_pause_without_real_sleep(store, brief, rubric):
    from lead_agent.providers import ProviderError
    virtual = [1000.0]
    def advance(seconds):
        virtual[0] += seconds
    args = dict(clock=lambda: virtual[0], wall_clock=lambda: virtual[0], sleep=advance)
    run = store.create_run(new_payload(brief, rubric, [], limits=Limits(model_requests_per_minute=4)))
    Worker(store, FakeProviders(error=ProviderError("Quota", "quota")), **args).execute(run)
    assert store.run(run)["status"] == "paused"
    assert store.run(run)["payload"]["next_model_at"] == 1015.0
    Worker(store, FakeProviders(), **args).execute(run)
    assert virtual[0] == 1015.0
    assert store.run(run)["payload"]["counters"]["model_calls"] == 2


def test_search_retrieves_pages_and_requires_citation_repair_before_finishing(store, brief, rubric):
    class Repairing(FakeProviders):
        def search(self, query):
            return {"results": [{"url": "https://synthetic-factory-1.example/news", "content": "A discovery hint only"}]}

        def action(self, prompt, messages):
            self.action_calls += 1
            context = json.loads(prompt.split("Configuration and current run: ", 1)[1])
            if self.action_calls == 1:
                pair = ("read_page", {"url": "https://synthetic-factory-1.example/"})
            elif self.action_calls == 2:
                pair = ("search", {"query": "synthetic-factory-1.example transformation"})
            elif self.action_calls in (3, 5):
                r, _ = fixture()
                sources = {("s1" if s["url"].endswith("/news") else "s0"): s["source_id"] for s in context["stored_sources"]}
                assert len(sources) == 2  # The search retrieved the news page without another model turn.
                for e in r.evidence:
                    e.source_id = sources[e.source_id]
                if self.action_calls == 3:
                    r.evidence[-1].source_id = "https://unread.example/article"
                pair = ("record_lead", {"research": r.model_dump(mode="json")})
            else:
                pair = ("finish", {})
            return Action(tool=pair[0], arguments_json=json.dumps(pair[1]), reason="Citation repair fixture").model_dump_json(), {}
    run = store.create_run(new_payload(brief, rubric, [{"company": "Synthetic Factory 1", "domain": "synthetic-factory-1.example"}],
                                     strategy="seeds", research_date=date(2026, 10, 20)))
    Worker(store, Repairing()).execute(run)
    assert store.run(run)["status"] == "completed"
    assert store.run(run)["payload"]["counters"]["model_calls"] == 6
    assert not store.leads(run)[0].validation_issues
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM research_revisions").fetchone()[0] == 1


def test_company_prefetch_supplies_primary_sources_before_first_model_call(store, brief, rubric):
    class Prepared(FakeProviders):
        def search(self, query, *, start_date=None):
            assert "site:synthetic-factory-1.example" in query
            assert start_date == "2025-10-20"
            return {"results": [{"url": "https://synthetic-factory-1.example/news"}]}

        def research(self, prompt, source_ids, **kwargs):
            self.action_calls += 1
            context = json.loads(prompt.split("Input: ", 1)[1])
            assert len(context["sources"]) == 2
            r, _ = fixture()
            ids = {("s1" if s["url"].endswith("/news") else "s0"): s["source_id"] for s in context["sources"]}
            for e in r.evidence:
                e.source_id = ids[e.source_id]
            return selected_passage_record(r, context), {}
    run = store.create_run(new_payload(brief, rubric, [{"company": "Synthetic Factory 1", "domain": "synthetic-factory-1.example"}],
                                     strategy="seeds", prefetch_sources=True, research_date=date(2026, 10, 20)))
    Worker(store, Prepared()).execute(run)
    assert store.run(run)["status"] == "completed"
    assert store.run(run)["payload"]["counters"] == {"searches": 1, "extractions": 2, "model_calls": 1}
    assert not store.leads(run)[0].validation_issues


def test_prepared_five_company_http_workflow_constrains_source_ids(monkeypatch, store, brief, rubric):
    today = datetime.now(timezone.utc).date()
    fixtures = [fixture(today=today, number=i) for i in range(1, 6)]
    seeds = [{"company": r.company, "domain": r.domain} for r, _ in fixtures]
    def respond(request):
        body = json.loads(request.content)
        if request.url.path == "/search":
            assert body["filter_by_published_date"] is False  # Keep undated pages as explicit unknowns.
            assert body["include_published_date"] is True
            company_domain = body["query"].split()[0].removeprefix("site:")
            return httpx.Response(200, json={"results": [{"url": f"https://{company_domain}/news"}]})
        if request.url.path == "/extract":
            url = body["urls"][0]
            index = next(i for i, pair in enumerate(fixtures) if pair[0].domain in url)
            if index == 0:
                return httpx.Response(200, json={"results": [], "failed_results": [{"url": url, "error": "Blocked"}]})
            _, sources = fixtures[index]
            text = sources[1].content if url.endswith("/news") else sources[0].content
            return httpx.Response(200, json={"results": [{"url": url, "raw_content": text}]})
        prompt = body["systemInstruction"]["parts"][0]["text"]
        context, _ = json.JSONDecoder().raw_decode(prompt.split("Input: ", 1)[1])
        source_ids = [s["source_id"] for s in context["sources"]]
        wire_schema = body["generationConfig"]["responseJsonSchema"]
        record_schema = json.loads(prompt.split("Record schema:\n", 1)[1].split("\nResearch input:\n", 1)[0])
        observation_schema = record_schema["$defs"]["Observation"]["properties"]
        assert observation_schema["quote_id"]["enum"] == [q["quote_id"] for q in context["evidence_bank"]]
        assert "excerpt" not in observation_schema and "source_id" not in observation_schema
        assert set(record_schema["properties"]["components"]["properties"]) == {"problem", "capability_fit", "timing", "buyer_access"}
        assert wire_schema["properties"]["tool"]["enum"]
        index = next(i for i, pair in enumerate(fixtures) if pair[0].domain == context["company"]["domain"])
        r, _ = fixtures[index]
        ids = {("s1" if s["url"].endswith("/news") else "s0"): s["source_id"] for s in context["sources"]}
        for e in r.evidence:
            e.source_id = ids[e.source_id]
        envelope = Action(tool="record_lead", arguments_json=json.dumps({"research": json.loads(selected_passage_record(r, context))}), reason="Prepared assessment fixture").model_dump_json()
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": envelope}]}}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        run = store.create_run(new_payload(brief, rubric, seeds, strategy="seeds", prefetch_sources=True, research_date=today))
        Worker(store, LiveProviders("fixture-gemini-key", "fixture-tavily-key", "gemini-fixture")).execute(run)
    row = store.run(run)
    assert row["status"] == "completed", row["error"]
    assert row["payload"]["counters"] == {"searches": 5, "extractions": 10, "model_calls": 4}
    cards = store.leads(run)
    assert len(cards) == 5 and cards[0].review.decision == "Needs research"
    assert all(l.review.decision == "Ready for review" and not l.validation_issues for l in cards[1:])


def test_prepared_assessment_is_saved_before_stop_and_reused_on_resume(store, brief, rubric):
    class Stopping(FakeProviders):
        def search(self, query, *, start_date=None):
            return {"results": [{"url": "https://synthetic-factory-1.example/news"}]}
        def research(self, prompt, source_ids, **kwargs):
            self.action_calls += 1
            context = json.loads(prompt.split("Input: ", 1)[1])
            r, _ = fixture()
            ids = {("s1" if s["url"].endswith("/news") else "s0"): s["source_id"] for s in context["sources"]}
            for e in r.evidence:
                e.source_id = ids[e.source_id]
            store.stop(run)
            return selected_passage_record(r, context), {}
    run = store.create_run(new_payload(brief, rubric, [{"company": "Synthetic Factory 1", "domain": "synthetic-factory-1.example"}],
                                     strategy="seeds", prefetch_sources=True, research_date=date(2026, 10, 20)))
    provider = Stopping()
    Worker(store, provider).execute(run)
    assert store.run(run)["status"] == "paused"
    assert store.run(run)["payload"]["pending_research"]
    Worker(store, provider).execute(run)
    assert store.run(run)["status"] == "completed"
    assert provider.action_calls == 1 and not store.leads(run)[0].validation_issues


def test_missing_signal_gets_one_bounded_source_expansion(store, brief, rubric):
    class Expanding(FakeProviders):
        def search(self, query, *, start_date=None):
            suffix = "about" if start_date else "news"
            return {"results": [{"url": "https://synthetic-factory-1.example/" + suffix}]}
        def research(self, prompt, source_ids, **kwargs):
            self.action_calls += 1
            context = json.loads(prompt.split("Input: ", 1)[1])
            r, _ = fixture()
            root = next(s["source_id"] for s in context["sources"] if s["url"].endswith("/"))
            news = next((s["source_id"] for s in context["sources"] if s["url"].endswith("/news")), None)
            for e in r.evidence:
                e.source_id = news if e.source_id == "s1" else root
            if not news:
                r.evidence = [e for e in r.evidence if e.category != "problem"]
                for component in r.components.values():
                    component.assessment = None
                    component.evidence_ids = []
            return selected_passage_record(r, context), {}
    run = store.create_run(new_payload(brief, rubric, [{"company": "Synthetic Factory 1", "domain": "synthetic-factory-1.example"}],
                                     strategy="seeds", prefetch_sources=True, research_date=date(2026, 10, 20)))
    Worker(store, Expanding()).execute(run)
    row = store.run(run)
    assert row["status"] == "completed"
    assert row["payload"]["counters"]["searches"] == 2
    assert row["payload"]["counters"]["model_calls"] == 2
    assert not store.leads(run)[0].validation_issues
