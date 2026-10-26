"""Offline stage, provenance and resume checks; no real-company or provider results."""
import json
from collections import Counter
from datetime import date

import pytest

from lead_agent.demo import fixture
from lead_agent.discovery import new_discovery_payload
from lead_agent.models import Limits
from lead_agent.worker import Worker


TODAY = date(2026, 10, 20)
HOST = "synthetic-factory-1.example"
ABOUT = f"https://{HOST}/about"
NEWS = f"https://{HOST}/news"
COMPANY = "Synthetic Factory 1"


def snapshot(*, pages=False):
    _, sources = fixture(today=TODAY)
    return {
        "source_run_id": "offline-snapshot-run",
        "search_cache": {
            "manufacturing business signals": [{
                "url": "https://industry-review.example/manufacturers",
                "title": "Manufacturing business signals",
                "discovery_hint": f"{COMPANY} starts an AI strategy review. Example Consultancy publishes this article.",
            }],
        },
        "sources": [s.model_dump(mode="json") for s in sources] if pages else [],
    }


def nomination(context, company=COMPANY, *, company_type="manufacturer", excerpt=None):
    return {
        "company": company,
        "origin_id": context["discovery_inputs"][0]["origin_id"],
        "name_excerpt": excerpt or company,
        "signal_hint": "A reported strategy review warrants investigation; demand remains unverified.",
        "company_type": company_type,
    }


class StageProvider:
    """Scripted structured outputs still traverse the real worker, binding and SQLite."""

    def __init__(self, *, nominations=None, official_url=ABOUT, invalid_assessments=0,
                 off_domain=False, inaccessible=False, resolution_url=ABOUT):
        self.nominations = nominations
        self.official_url = official_url
        self.invalid_assessments = invalid_assessments
        self.off_domain = off_domain
        self.inaccessible = inaccessible
        self.resolution_url = resolution_url
        self.calls = Counter()
        self.read_urls = []
        self.contexts = []
        self.on_nomination = None

    def check(self):
        pass

    def action(self, *_):
        raise AssertionError("Staged discovery must not fall back to free-form tool actions.")

    def discovery(self, prompt, schema):
        context = json.loads(prompt)
        stage = context["stage"]
        self.calls[stage] += 1
        self.contexts.append(context)
        assert schema["type"] == "object"
        if stage == "candidate_extract":
            result = {"candidates": (self.nominations(context) if self.nominations else [nomination(context)])}
            if self.on_nomination:
                self.on_nomination()
                self.on_nomination = None
        elif stage == "resolve_company":
            result = {"official_url": self.official_url,
                      "reason": "Selected from the returned corporate-site search results."}
        else:
            raise AssertionError(f"Unexpected discovery model stage: {stage}")
        return json.dumps(result), {"totalTokenCount": 10}

    def search(self, query, *, start_date=None):
        self.calls["search"] += 1
        url = self.resolution_url if "official corporate website" in query else NEWS
        return {"results": [{"url": url, "title": COMPANY,
                             "content": f"Search hint about {COMPANY}; this is not evidence."}],
                "usage": {"credits": 0.2}}

    def read_page(self, url):
        self.calls["extract"] += 1
        self.read_urls.append(url)
        _, sources = fixture(today=TODAY)
        text = "" if self.inaccessible else next(s.content for s in sources if s.url == url)
        return {"results": [{"url": url, "raw_content": text}], "usage": {"credits": 0.2}}

    def research(self, prompt, source_ids, *, quote_ids=None):
        self.calls["assessment"] += 1
        context = json.loads(prompt.split("Input: ", 1)[1])
        assert context["company"] == {"company": COMPANY, "domain": HOST}
        assert set(source_ids) == {s["source_id"] for s in context["sources"]}
        assert set(quote_ids) == {q["quote_id"] for q in context["evidence_bank"]}
        research, _ = fixture(today=TODAY)
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
        if self.calls["assessment"] <= self.invalid_assessments:
            record["evidence"][0]["quote_id"] = "fabricated-quote-id"
        if self.off_domain:
            record["domain"] = "another-factory.example"
        return json.dumps(record), {"totalTokenCount": 20}


def create_run(store, brief, rubric, *, pages=False, limits=None, target=1):
    return store.create_run(new_discovery_payload(
        brief, rubric, snapshot=snapshot(pages=pages), target=target, limits=limits,
        model="unchanged-test-model", research_date=TODAY,
    ))


def execute(store, run, provider):
    Worker(store, provider, sleep=lambda _: None).execute(run)
    result = store.run(run)
    return result, result["payload"]["discovery"]


def test_stages_save_only_retrieved_bound_evidence_and_leave_review_pending(store, brief, rubric):
    run = create_run(store, brief, rubric)
    provider = StageProvider()
    result, discovery = execute(store, run, provider)

    assert result["status"] == "completed"
    assert result["payload"]["model"] == "unchanged-test-model"
    assert result["payload"]["counters"] == {"searches": 2, "extractions": 2, "model_calls": 3}
    assert provider.read_urls == [ABOUT, NEWS]
    assert provider.calls["candidate_extract"] == provider.calls["resolve_company"] == provider.calls["assessment"] == 1
    entry, = discovery["candidates"]
    assert entry["status"] == "saved" and entry["domain"] == HOST
    assert entry["nomination"]["provenance"] == "discovery_hint"
    assert entry["nomination"]["evidence_verified"] is False
    assert len(entry["source_ids"]) == 2
    lead, = store.leads(run)
    assert lead.validation_issues == []
    assert lead.review.decision == "Ready for review"
    assert {s.url for s in lead.sources} == {ABOUT, NEWS}
    assert all(e.source_id in entry["source_ids"] for e in lead.research.evidence)
    assert store.handoff(run) == []
    events = [event["message"] for event in result["payload"]["events"]]
    assert any("validate_save" in event for event in events)
    assert events.index("Discovery stage: assessment") < events.index("Discovery stage: validate_save")


def test_snapshot_pages_are_reused_without_becoming_fresh_extraction_calls(store, brief, rubric):
    run = create_run(store, brief, rubric, pages=True)
    provider = StageProvider()
    result, discovery = execute(store, run, provider)

    assert result["status"] == "completed"
    assert discovery["reused_inputs"] == {"queries": 1, "results": 1, "pages": 2,
                                           "source_run_id": "offline-snapshot-run"}
    assert provider.calls["extract"] == result["payload"]["counters"]["extractions"] == 0
    lead, = store.leads(run)
    assert {s.source_id for s in lead.sources} == {"s0", "s1"}


def test_nomination_requires_exact_origin_and_deduplicates_and_excludes_publishers(store, brief, rubric):
    def candidates(context):
        return [nomination(context), nomination(context, company="SYNTHETIC FACTORY 1"),
                nomination(context, company="Invented Factory", excerpt="Invented Factory"),
                nomination(context, company="Example Consultancy", company_type="other")]

    run = create_run(store, brief, rubric)
    result, discovery = execute(store, run, StageProvider(nominations=candidates))

    assert result["status"] == "completed"
    assert [c["company"] for c in discovery["candidates"]] == [COMPANY]
    rejections = discovery["rejected_nominations"]
    assert len(rejections) == 3
    assert any("Duplicate" in item["reason"] for item in rejections)
    assert any("exact company-name support" in item["reason"] for item in rejections)
    assert any("non-manufacturer" in item["reason"] for item in rejections)


def test_ungrounded_nominations_stop_after_two_attempts_without_queue_or_web_calls(store, brief, rubric):
    run = create_run(store, brief, rubric)
    provider = StageProvider(nominations=lambda context: [nomination(context, company="Invented Factory")])
    result, discovery = execute(store, run, provider)

    assert result["status"] == "incomplete"
    assert provider.calls["candidate_extract"] == 2
    assert discovery["candidates"] == []
    assert provider.calls["search"] == provider.calls["extract"] == 0
    assert store.leads(run) == []


@pytest.mark.parametrize("official_url,resolution_url", [
    ("https://invented-official.example/", ABOUT),
    ("https://www.linkedin.com/company/synthetic-factory-1/", "https://www.linkedin.com/company/synthetic-factory-1/"),
])
def test_resolver_cannot_read_invented_or_known_social_urls(store, brief, rubric, official_url, resolution_url):
    run = create_run(store, brief, rubric)
    provider = StageProvider(official_url=official_url, resolution_url=resolution_url)
    result, discovery = execute(store, run, provider)

    assert result["status"] == "incomplete"
    entry, = discovery["candidates"]
    assert entry["status"] == "needs_research"
    assert entry["domain"] is None
    assert provider.calls["resolve_company"] == 2
    assert provider.calls["extract"] == provider.calls["assessment"] == 0
    assert store.leads(run) == []


def test_unresolved_official_site_is_an_explicit_queue_disposition(store, brief, rubric):
    run = create_run(store, brief, rubric)
    provider = StageProvider(official_url=None)
    result, discovery = execute(store, run, provider)

    assert result["status"] == "incomplete"
    entry, = discovery["candidates"]
    assert entry["status"] == "needs_research"
    assert "unresolved" in entry["disposition"]
    assert provider.calls["resolve_company"] == 1
    assert provider.calls["extract"] == provider.calls["assessment"] == 0
    assert store.leads(run) == []


def test_search_hints_never_become_evidence_when_pages_are_inaccessible(store, brief, rubric):
    run = create_run(store, brief, rubric)
    provider = StageProvider(inaccessible=True)
    result, discovery = execute(store, run, provider)

    assert result["status"] == "incomplete"
    assert result["payload"]["sources"] == []
    entry, = discovery["candidates"]
    assert entry["source_ids"] == [] and entry["status"] == "needs_research"
    assert "discovery hints cannot serve as evidence" in entry["disposition"]
    assert provider.calls["assessment"] == 0
    assert store.leads(run) == []


@pytest.mark.parametrize("invalid_assessments,expected_status,expected_saved", [(1, "completed", 1), (2, "incomplete", 0)])
def test_bad_quote_repairs_are_bounded_and_never_persist_invalid_cards(
        store, brief, rubric, invalid_assessments, expected_status, expected_saved):
    run = create_run(store, brief, rubric)
    provider = StageProvider(invalid_assessments=invalid_assessments)
    result, discovery = execute(store, run, provider)

    assert result["status"] == expected_status
    entry, = discovery["candidates"]
    assert entry["attempts"]["assessment"] == provider.calls["assessment"] == 2
    assert provider.calls["search"] == 3
    assert any("quote_id" in issue for issue in entry["issues"])
    assert len(store.leads(run)) == expected_saved
    assert all(not lead.validation_issues for lead in store.leads(run))
    if not expected_saved:
        assert entry["status"] == "needs_research"
        assert "Two assessment attempts" in entry["disposition"]


def test_assessment_cannot_save_a_different_domain_than_the_queued_company(store, brief, rubric):
    run = create_run(store, brief, rubric)
    provider = StageProvider(off_domain=True)
    result, discovery = execute(store, run, provider)

    assert result["status"] == "incomplete"
    entry, = discovery["candidates"]
    assert entry["domain"] == HOST and entry["status"] == "needs_research"
    assert provider.calls["assessment"] == 2
    assert any("queued company" in issue for issue in entry["issues"])
    assert store.leads(run) == []


def test_stop_after_returned_nomination_resumes_from_pending_output_without_recalling_it(store, brief, rubric):
    run = create_run(store, brief, rubric)
    provider = StageProvider()
    provider.on_nomination = lambda: store.stop(run)
    paused, discovery = execute(store, run, provider)

    assert paused["status"] == "paused"
    assert discovery["pending_output"]["key"] == "candidate_extract"
    assert paused["payload"]["counters"]["model_calls"] == 1
    assert provider.calls["candidate_extract"] == 1
    assert store.leads(run) == []

    resumed, discovery = execute(store, run, provider)
    assert resumed["status"] == "completed"
    assert provider.calls["candidate_extract"] == 1
    assert resumed["payload"]["counters"]["model_calls"] == 3
    assert discovery["pending_output"] is None
    assert len(store.leads(run)) == 1


def test_budget_limit_retains_candidate_queue_and_consumed_usage(store, brief, rubric):
    run = create_run(store, brief, rubric, limits=Limits(model_calls=2))
    provider = StageProvider()
    result, discovery = execute(store, run, provider)

    assert result["status"] == "limit"
    assert result["payload"]["counters"]["model_calls"] == 2
    assert result["payload"]["usage"]["totalTokenCount"] == 20
    entry, = discovery["candidates"]
    assert entry["company"] == COMPANY and entry["domain"] == HOST
    assert len(entry["source_ids"]) == 2
    assert entry["status"] == "researching" and entry["stage"] == "assessment"
    assert provider.calls["assessment"] == 0
    assert store.leads(run) == []


def test_frozen_snapshot_is_unchanged_after_research_adds_queries_and_pages(store, brief, rubric):
    original = snapshot()
    run = store.create_run(new_discovery_payload(
        brief, rubric, snapshot=original, target=1, research_date=TODAY,
    ))
    frozen_before = store.run(run)["payload"]["discovery"]["snapshot"]
    result, discovery = execute(store, run, StageProvider())

    assert result["status"] == "completed"
    assert discovery["snapshot"] == frozen_before == original
    assert len(result["payload"]["search_cache"]) == 3
    assert len(result["payload"]["sources"]) == 2
    assert len(discovery["snapshot"]["search_cache"]) == 1
    assert discovery["snapshot"]["sources"] == []


class FullEvidencePageProvider(StageProvider):
    """One official page can contain both corporate identity and a dated event."""

    def read_page(self, url):
        assert url == ABOUT
        self.calls["extract"] += 1
        self.read_urls.append(url)
        _, sources = fixture(today=TODAY)
        return {"results": [{"url": url, "raw_content": "\n\n".join(s.content for s in sources)}],
                "usage": {"credits": 0.2}}


def test_cached_resolution_query_remains_usable_after_search_budget_is_exhausted(store, brief, rubric):
    cached = snapshot()
    cached["search_cache"][f"{COMPANY} official corporate website"] = [
        {"url": ABOUT, "title": COMPANY, "discovery_hint": "Corporate website search result."},
    ]
    run = store.create_run(new_discovery_payload(
        brief, rubric, snapshot=cached, target=1, limits=Limits(searches=1), research_date=TODAY,
    ))
    # A resumed run has already consumed its one search; replaying a snapshot is free.
    token = store.claim(run)
    persisted = store.run(run)["payload"]
    persisted["counters"]["searches"] = 1
    persisted["reserved_credits"] = 1
    store.save(run, persisted, token)
    store.finish(run, "paused", "Offline fixture with a previously consumed search.", token)
    provider = FullEvidencePageProvider()
    result, discovery = execute(store, run, provider)

    assert result["status"] == "completed"
    assert provider.calls["search"] == 0
    assert result["payload"]["counters"]["searches"] == 1
    assert discovery["candidates"][0]["status"] == "saved"
    assert len(store.leads(run)) == 1


def test_optional_signal_search_is_skipped_when_official_page_already_supports_a_card(store, brief, rubric):
    run = create_run(store, brief, rubric, limits=Limits(searches=1))
    provider = FullEvidencePageProvider()
    result, discovery = execute(store, run, provider)

    assert result["status"] == "completed"
    assert provider.calls["search"] == provider.calls["extract"] == provider.calls["assessment"] == 1
    entry, = discovery["candidates"]
    assert entry["status"] == "saved" and len(entry["source_ids"]) == 1
    assert any("No optional signal-search allowance" in issue for issue in entry["issues"])
    lead, = store.leads(run)
    assert lead.validation_issues == []


def test_company_name_cannot_be_only_a_substring_of_a_different_word(store, brief, rubric):
    cached = snapshot()
    cached["search_cache"]["manufacturing business signals"][0]["discovery_hint"] = (
        "Oxford publishes a report about manufacturing strategy."
    )
    run = store.create_run(new_discovery_payload(
        brief, rubric, snapshot=cached, target=1, research_date=TODAY,
    ))
    provider = StageProvider(nominations=lambda context: [
        nomination(context, company="Ford", excerpt="Oxford publishes a report"),
    ])
    result, discovery = execute(store, run, provider)

    assert result["status"] == "incomplete"
    assert discovery["candidates"] == []
    assert provider.calls["candidate_extract"] == 2
    assert provider.calls["search"] == provider.calls["extract"] == 0
    assert all("exact company-name support" in item["reason"] for item in discovery["rejected_nominations"])
    assert store.leads(run) == []


def test_fresh_discovery_runs_code_owned_signal_searches_before_company_research(store, brief, rubric):
    run = store.create_run(new_discovery_payload(
        brief, rubric, target=1, research_date=TODAY,
    ))
    provider = StageProvider()
    result, discovery = execute(store, run, provider)

    assert result["status"] == "completed"
    assert discovery["query_index"] == 2
    assert discovery["snapshot"] is None
    assert result["payload"]["counters"] == {"searches": 4, "extractions": 2, "model_calls": 3}
    extraction_context = next(c for c in provider.contexts if c["stage"] == "candidate_extract")
    assert len(extraction_context["discovery_inputs"]) == 2
    assert all(origin["kind"] == "discovery_hint" for origin in extraction_context["discovery_inputs"])
    assert provider.read_urls == [ABOUT, NEWS]
    lead, = store.leads(run)
    assert lead.validation_issues == []
    assert {source.url for source in lead.sources} == {ABOUT, NEWS}
    assert all("Search hint about" not in source.content for source in lead.sources)


def test_malformed_assessment_response_counts_toward_the_two_attempt_limit(store, brief, rubric):
    class MalformedAssessmentProvider(StageProvider):
        def research(self, prompt, source_ids, *, quote_ids=None):
            self.calls["assessment"] += 1
            # The adapter may reject its internal JSON before returning text or usage.
            raise ValueError("Model returned malformed research arguments JSON.")

    run = create_run(store, brief, rubric)
    provider = MalformedAssessmentProvider()
    result, discovery = execute(store, run, provider)

    assert result["status"] == "incomplete"
    assert provider.calls["assessment"] == 2
    assert result["payload"]["counters"]["model_calls"] == 4
    entry, = discovery["candidates"]
    assert entry["attempts"]["assessment"] == 2
    assert entry["status"] == "needs_research"
    assert "Two assessment attempts" in entry["disposition"]
    assert any("malformed research arguments JSON" in issue for issue in entry["issues"])
    assert store.leads(run) == []
