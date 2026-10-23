"""Offline execution guards for problem-driven discovery; no real providers."""
import json
from datetime import date

from lead_agent.assessment import make_lead
from lead_agent.demo import fixture
from lead_agent.models import Action, Limits
from lead_agent.worker import Worker, new_payload
from test_identification import FakeProviders


TODAY = date(2026, 10, 20)


def action(tool, **arguments):
    return Action(tool=tool, arguments_json=json.dumps(arguments), reason="Discovery guard fixture").model_dump_json(), {}


def context(prompt):
    return json.loads(prompt.split("Configuration and current run: ", 1)[1])


def supported_record(prompt, number=1):
    research, _ = fixture(today=TODAY, number=number)
    sources = context(prompt)["stored_sources"]
    for evidence in research.evidence:
        evidence.source_id = next(source["source_id"] for source in sources if evidence.excerpt in source["content"])
    return research.model_dump(mode="json")


class DiscoveryProviders(FakeProviders):
    def __init__(self, number=1):
        super().__init__()
        _, self.pages = fixture(today=TODAY, number=number)
        self.read_calls = []

    def search(self, query):
        return {"results": [{"url": source.url, "content": "Search hints are not evidence."} for source in self.pages]}

    def read_page(self, url):
        self.read_calls.append(url)
        source = next(source for source in self.pages if source.url == url)
        return {"results": [{"url": source.url, "raw_content": source.content}]}


def test_problem_search_retrieves_sources_before_recording(store, brief, rubric):
    class Recording(DiscoveryProviders):
        def action(self, prompt, messages):
            self.action_calls += 1
            if self.action_calls == 1:
                return action("search", query="manufacturing disconnected AI pilot strategy review")
            assert self.read_calls == [source.url for source in self.pages]
            assert store.leads(run) == []
            return action("record_lead", research=supported_record(prompt))

    provider = Recording()
    run = store.create_run(new_payload(brief, rubric, [], target=1, research_date=TODAY))
    Worker(store, provider).execute(run)

    row = store.run(run)
    assert row["status"] == "completed", row["error"]
    assert row["payload"]["counters"] == {"searches": 1, "extractions": 2, "model_calls": 2}
    card, = store.leads(run)
    assert not card.validation_issues
    assert all(e.excerpt in next(s.content for s in card.sources if s.source_id == e.source_id) for e in card.research.evidence)


def test_record_before_retrieval_is_never_saved_then_can_be_repaired(store, brief, rubric):
    class Repairing(DiscoveryProviders):
        def action(self, prompt, messages):
            self.action_calls += 1
            if self.action_calls == 1:
                research, _ = fixture(today=TODAY)
                return action("record_lead", research=research.model_dump(mode="json"))
            if self.action_calls == 2:
                assert store.leads(run) == []
                assert any("not saved" in message.get("validation_error", "").lower() for message in messages)
                return action("search", query="manufacturing AI strategy review official sources")
            assert store.leads(run) == []
            return action("record_lead", research=supported_record(prompt))

    run = store.create_run(new_payload(brief, rubric, [], target=1, research_date=TODAY))
    Worker(store, Repairing()).execute(run)

    assert store.run(run)["status"] == "completed"
    card, = store.leads(run)
    assert not card.validation_issues
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM research_revisions").fetchone()[0] == 0


def test_fabricated_quote_is_rejected_before_sqlite_save(store, brief, rubric):
    class Repairing(DiscoveryProviders):
        def action(self, prompt, messages):
            self.action_calls += 1
            if self.action_calls == 1:
                return action("search", query="manufacturing AI coordination problem")
            record = supported_record(prompt)
            if self.action_calls == 2:
                record["evidence"][-1]["excerpt"] = "This purported source passage was never retrieved."
            else:
                assert store.leads(run) == []
                assert any("not saved" in message.get("validation_error", "").lower() for message in messages)
            return action("record_lead", research=record)

    run = store.create_run(new_payload(brief, rubric, [], target=1, research_date=TODAY))
    Worker(store, Repairing()).execute(run)

    assert store.run(run)["status"] == "completed"
    card, = store.leads(run)
    assert not card.validation_issues
    assert card.research.evidence[-1].excerpt == fixture(today=TODAY)[0].evidence[-1].excerpt


def test_invalid_replacement_preserves_previous_valid_card_and_reports_shortfall(store, brief, rubric):
    class Replacing(DiscoveryProviders):
        saved_card = None

        def action(self, prompt, messages):
            self.action_calls += 1
            if self.action_calls == 1:
                return action("search", query="manufacturing strategy problem discovery")
            if self.action_calls == 2:
                return action("record_lead", research=supported_record(prompt))
            if self.action_calls == 3:
                self.saved_card = store.leads(run)[0].model_dump_json()
                record = supported_record(prompt)
                record["evidence"][-1]["excerpt"] = "An unsupported replacement claim."
                return action("record_lead", research=record)
            assert store.leads(run)[0].model_dump_json() == self.saved_card
            return action("finish")

    provider = Replacing()
    run = store.create_run(new_payload(brief, rubric, [], target=2, research_date=TODAY, limits=Limits(model_calls=8)))
    Worker(store, provider).execute(run)

    row = store.run(run)
    assert row["status"] == "incomplete"
    assert provider.action_calls == 6  # Two bounded repair opportunities, then an honest shortfall.
    assert "1" in row["error"] and "2" in row["error"]
    card, = store.leads(run)
    assert not card.validation_issues and card.model_dump_json() == provider.saved_card
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM research_revisions").fetchone()[0] == 0


def test_search_snippets_cannot_be_saved_when_pages_are_unavailable(store, brief, rubric):
    class SnippetsOnly(DiscoveryProviders):
        def search(self, query):
            return {"results": [{"url": source.url, "content": source.content} for source in self.pages]}

        def read_page(self, url):
            self.read_calls.append(url)
            return {"results": [], "failed_results": [{"url": url, "error": "Unavailable fixture page"}]}

        def action(self, prompt, messages):
            self.action_calls += 1
            if self.action_calls == 1:
                return action("search", query="manufacturing AI problem snippets")
            if self.action_calls == 2:
                research, _ = fixture(today=TODAY)
                return action("record_lead", research=research.model_dump(mode="json"))
            assert store.leads(run) == []
            return action("finish")

    provider = SnippetsOnly()
    run = store.create_run(new_payload(brief, rubric, [], target=1, research_date=TODAY, limits=Limits(model_calls=8)))
    Worker(store, provider).execute(run)

    row = store.run(run)
    assert row["status"] == "incomplete"
    assert provider.action_calls == 5
    assert len(provider.read_calls) == 2
    assert row["payload"]["sources"] == [] and store.leads(run) == []
    assert "0" in row["error"] and "1" in row["error"]


def test_discovery_reuses_cached_search_and_retrieved_sources(store, brief, rubric):
    class Cached(DiscoveryProviders):
        def action(self, prompt, messages):
            self.action_calls += 1
            if self.action_calls <= 2:
                return action("search", query="same manufacturing strategy research query")
            return action("record_lead", research=supported_record(prompt))

    provider = Cached()
    run = store.create_run(new_payload(brief, rubric, [], target=1, research_date=TODAY))
    Worker(store, provider).execute(run)

    row = store.run(run)
    assert row["status"] == "completed"
    assert row["payload"]["counters"] == {"searches": 1, "extractions": 2, "model_calls": 3}
    assert len(provider.read_calls) == len(row["payload"]["sources"]) == 2
    assert row["payload"]["queries"] == ["same manufacturing strategy research query"]


def test_automatic_retrieval_leaves_a_turn_to_save_under_web_budget(store, brief, rubric):
    class Budgeted(DiscoveryProviders):
        def read_page(self, url):
            self.read_calls.append(url)
            return {"results": [{"url": url, "raw_content": "\n".join(source.content for source in self.pages)}]}

        def action(self, prompt, messages):
            self.action_calls += 1
            if self.action_calls == 1:
                return action("search", query="manufacturing strategy review with one strong official source")
            assert len(self.read_calls) == 1
            return action("record_lead", research=supported_record(prompt))

    run = store.create_run(new_payload(brief, rubric, [], target=1, research_date=TODAY,
                                      limits=Limits(extractions=1, tavily_credits=2, model_calls=4)))
    Worker(store, Budgeted()).execute(run)

    row = store.run(run)
    assert row["status"] == "completed", row["error"]
    assert row["payload"]["reserved_credits"] == 2
    assert row["payload"]["counters"] == {"searches": 1, "extractions": 1, "model_calls": 2}
    card, = store.leads(run)
    assert not card.validation_issues and len(card.sources) == 1


def test_legacy_invalid_card_does_not_satisfy_discovery_target(store, brief, rubric):
    class Recording(DiscoveryProviders):
        def action(self, prompt, messages):
            self.action_calls += 1
            if self.action_calls == 1:
                return action("search", query="another manufacturing company with supported strategy problem")
            return action("record_lead", research=supported_record(prompt, number=2))

    run = store.create_run(new_payload(brief, rubric, [], target=1, research_date=TODAY))
    research, _ = fixture(today=TODAY)
    legacy = make_lead(run, research, [], brief, rubric, TODAY)
    assert legacy.validation_issues
    store.add_lead(legacy)
    provider = Recording(number=2)
    Worker(store, provider).execute(run)

    assert store.run(run)["status"] == "completed"
    assert provider.action_calls == 2
    cards = store.leads(run)
    assert len(cards) == 2
    assert [card.research.domain for card in cards if not card.validation_issues] == ["synthetic-factory-2.example"]


def test_unchanged_valid_card_cannot_clear_another_candidates_pending_repair(store, brief, rubric):
    class Repeating(DiscoveryProviders):
        saved_card = None
        pending = None

        def action(self, prompt, messages):
            self.action_calls += 1
            state = store.run(run)["payload"]
            if self.action_calls == 1:
                return action("search", query="manufacturing strategy discovery with two candidate companies")
            if self.action_calls == 2:
                return action("record_lead", research=supported_record(prompt))
            if self.action_calls == 3:
                self.saved_card = store.leads(run)[0].model_dump_json()
                unsupported, _ = fixture(today=TODAY, number=2)
                return action("record_lead", research=unsupported.model_dump(mode="json"))
            if self.action_calls == 4:
                self.pending = state["discovery_pending"]
                assert self.pending["domain"] == "synthetic-factory-2.example"
                assert self.pending["issues"]
                return action("record_lead", research=supported_record(prompt))
            assert store.leads(run)[0].model_dump_json() == self.saved_card
            assert state["discovery_pending"] == self.pending
            assert state.get("discovery_finish_repairs", 0) == self.action_calls - 5
            if self.action_calls == 5:
                assert messages[-1]["result"]["outcome"] == "unchanged"
                assert not messages[-1]["result"]["saved"]
            return action("finish")

    provider = Repeating()
    run = store.create_run(new_payload(brief, rubric, [], target=2, research_date=TODAY,
                                      limits=Limits(model_calls=10)))
    Worker(store, provider).execute(run)

    row = store.run(run)
    assert row["status"] == "incomplete"
    assert provider.action_calls == 7
    assert row["payload"]["discovery_pending"] == provider.pending
    assert row["payload"]["discovery_finish_repairs"] == 2
    assert "1/2" in row["error"]
    card, = store.leads(run)
    assert card.model_dump_json() == provider.saved_card and not card.validation_issues
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM research_revisions").fetchone()[0] == 0


def test_stop_resume_preserves_rejected_candidate_cache_budgets_and_repair_bound(store, brief, rubric):
    query = "manufacturing AI strategy discovery for a bounded resumable run"

    def unsupported_record(prompt):
        record = supported_record(prompt)
        record["evidence"][-1]["excerpt"] = "This unsupported passage must never become a saved card."
        return record

    class Stopping(DiscoveryProviders):
        def action(self, prompt, messages):
            self.action_calls += 1
            if self.action_calls == 1:
                return action("search", query=query)
            if self.action_calls == 2:
                return action("record_lead", research=unsupported_record(prompt))
            assert store.leads(run) == []
            if self.action_calls == 3:
                return action("finish")
            assert store.run(run)["payload"]["discovery_finish_repairs"] == 1
            store.stop(run)
            return action("search", query=query)

    run = store.create_run(new_payload(brief, rubric, [], target=1, research_date=TODAY,
                                      limits=Limits(searches=2, extractions=3, tavily_credits=4, model_calls=7)))
    initial = Stopping()
    Worker(store, initial).execute(run)

    paused = store.run(run)
    before = paused["payload"]
    assert paused["status"] == "paused" and "Stopped by user" in paused["error"]
    assert before["pending_action"]["tool"] == "search"
    assert before["discovery_pending"]["issues"]
    assert before["discovery_finish_repairs"] == 1
    assert before["counters"] == {"searches": 1, "extractions": 2, "model_calls": 4}
    assert before["reserved_credits"] == 3
    assert before["search_cache"][query] and len(before["sources"]) == 2
    assert store.leads(run) == []

    class Resuming(DiscoveryProviders):
        def action(self, prompt, messages):
            self.action_calls += 1
            state = store.run(run)["payload"]
            assert state["discovery_pending"] == before["discovery_pending"]
            assert state["search_cache"] == before["search_cache"]
            assert state["sources"] == before["sources"]
            assert state["reserved_credits"] == before["reserved_credits"]
            assert not self.read_calls and store.leads(run) == []
            if self.action_calls == 1:
                assert messages[-1]["action"]["tool"] == "search"
                assert state["counters"] == {"searches": 1, "extractions": 2, "model_calls": 5}
                return action("record_lead", research=unsupported_record(prompt))
            assert state["discovery_finish_repairs"] == self.action_calls - 1
            return action("finish")

    resumed = Resuming()
    Worker(store, resumed).execute(run)

    row = store.run(run)
    after = row["payload"]
    assert row["status"] == "incomplete" and "0/1" in row["error"]
    assert resumed.action_calls == 3  # Cached pending search and the first repair are not repeated.
    assert after["counters"] == {"searches": 1, "extractions": 2, "model_calls": 7}
    assert after["reserved_credits"] == before["reserved_credits"]
    assert after["discovery_finish_repairs"] == 2
    assert after["discovery_pending"] == before["discovery_pending"]
    assert after["search_cache"] == before["search_cache"]
    assert after["sources"] == before["sources"]
    assert after["active_seconds"] >= before["active_seconds"]
    assert store.leads(run) == []
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM research_revisions").fetchone()[0] == 0
