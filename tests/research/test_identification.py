import json
from datetime import date, datetime, timezone

import httpx
import pytest
from pydantic import ValidationError

from lead_agent.assessment import issues_for, make_lead, prioritize
from lead_agent.demo import fixture, load_demo
from lead_agent.exporting import evaluation, inspection_csv
from lead_agent.models import Action, Lead, Limits, Review, Source, domain, public_url, stable_id
from lead_agent.providers import LiveProviders, ProviderError, post
from lead_agent.worker import Worker, new_payload

TODAY = date(2026, 10, 20)


def approve():
    return Review(decision="Approved for handoff", reviewer="Test reviewer", reviewed_at=datetime.now(timezone.utc),
                  source_support_checked=True, minutes=2)


def test_evidence_and_unknown_contact(brief, rubric):
    research, sources = fixture()
    assert issues_for(research, sources, brief, TODAY) == []
    lead = make_lead("run", research, sources, brief, rubric, TODAY)
    assert lead.review.decision == "Ready for review"
    assert lead.priority.score == 100
    assert lead.priority.coverage == .9
    assert lead.priority.possible_range == [90, 100]
    assert lead.research.people is None
    assert Lead.model_validate_json(lead.model_dump_json()) == lead


@pytest.mark.parametrize("mutate,match", [
    (lambda r: setattr(r.evidence[0], "excerpt", "This excerpt was invented, not retrieved."), "excerpt"),
    (lambda r: setattr(r, "identity", "ambiguous"), "identity"),
    (lambda r: r.conflicts.append("Two incompatible official addresses."), "Conflicting"),
    (lambda r: setattr(r.evidence[-1], "publication_date", date(2024, 1, 1)), "stale"),
    (lambda r: setattr(r.evidence[-1], "publication_date", date(2027, 1, 1)), "future"),
    (lambda r: setattr(r.evidence[-1], "date_excerpt", "Invented date"), "date excerpt"),
    (lambda r: setattr(r.components["problem"], "evidence_ids", ["invented"]), "references"),
    (lambda r: setattr(r.components["problem"], "assessment", None), "unknown"),
    (lambda r: setattr(r, "company", "Similar Name Other Entity"), "name the company"),
])
def test_failed_evidence_goes_to_research(brief, rubric, mutate, match):
    research, sources = fixture()
    mutate(research)
    lead = make_lead("run", research, sources, brief, rubric, TODAY)
    assert lead.review.decision == "Needs research"
    assert lead.priority.score is None
    assert any(match in x for x in lead.validation_issues)


def test_exclusions(brief):
    research, sources = fixture()
    brief.excluded_domains = [research.domain]
    assert "Company domain is excluded." in issues_for(research, sources, brief, TODAY)


@pytest.mark.parametrize("url", ["http://localhost/a", "http://127.0.0.1/", "https://a.internal/", "ftp://a.com/", "https://user:pass@example.com/", "https://example.com:8000/"])
def test_invalid_urls(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_stable_ids_and_domain_normalization():
    assert domain("https://www.EXAMPLE.co.uk/path") == "example.co.uk"
    assert stable_id("www.example.com") == stable_id("https://example.com/")
    assert stable_id("example.com", "synthetic") != stable_id("example.com", "live")


def test_configurable_weights(brief, rubric):
    research, sources = fixture()
    research.components["problem"].assessment = "medium"
    first = prioritize(research, rubric, [])
    rubric.dimensions["problem"].weight = .6
    rubric.dimensions["timing"].weight = 0.01
    rubric.dimensions["capability_fit"].weight = .29
    second = prioritize(research, rubric, [])
    assert first.score != second.score
    with pytest.raises(ValidationError):
        type(rubric).model_validate({**rubric.model_dump(), "high_threshold": 20})


def test_review_gate_and_idempotent_handoff(store, brief, rubric):
    run = store.create_run(new_payload(brief, rubric, [], research_date=TODAY))
    research, sources = fixture()
    lead = make_lead(run, research, sources, brief, rubric, TODAY)
    assert store.add_lead(lead)
    assert not store.add_lead(lead)
    assert store.handoff(run) == []
    with pytest.raises(ValueError, match="named reviewer"):
        store.review(run, lead.lead_id, Review(decision="Approved for handoff"))
    review = approve()
    review.source_support_checked = False
    with pytest.raises(ValueError, match="material claims"):
        store.review(run, lead.lead_id, review)
    store.review(run, lead.lead_id, approve())
    once = store.handoff(run)
    assert len(once) == 1 and once == store.handoff(run)
    assert once[0]["approval_basis"] == "mock"
    with pytest.raises(ValueError, match="immutable"):
        store.review(run, lead.lead_id, approve())
    second_run = store.create_run(new_payload(brief, rubric, []))
    other = lead.model_copy(update={"run_id": second_run, "review": approve()})
    store.add_lead(other)
    assert store.handoff(second_run) == []


def test_unapproved_and_incomplete_cannot_be_handed_off(store, brief, rubric):
    brief.approved_by = None
    run = store.create_run(new_payload(brief, rubric, []))
    research, sources = fixture()
    lead = make_lead(run, research, sources, brief, rubric, TODAY)
    store.add_lead(lead)
    with pytest.raises(ValueError, match="approval recorded"):
        store.review(run, lead.lead_id, approve())
    lead.validation_issues = ["Missing geography evidence"]
    with store.db() as db:
        db.execute("UPDATE leads SET record=?", (lead.model_dump_json(),))
    with pytest.raises(ValueError, match="research issues"):
        store.review(run, lead.lead_id, approve())


class FakeProviders:
    def __init__(self, actions=(), error=None):
        self.actions = iter(actions)
        self.error = error
        self.action_calls = 0

    def check(self):
        pass

    def action(self, prompt, messages):
        self.action_calls += 1
        if self.error:
            raise self.error
        action = next(self.actions, ("finish", {}))
        return Action(tool=action[0], arguments_json=json.dumps(action[1]), reason="Test action").model_dump_json(), {"totalTokenCount": 10}

    def search(self, query):
        return {"results": [{"url": "https://synthetic-factory-1.example/about", "content": "search hint"}], "usage": {"credits": 1}}

    def read_page(self, url):
        research, sources = fixture()
        text = sources[0].content if url.endswith("/") else next(s.content for s in sources if s.url == url)
        return {"results": [{"url": url, "raw_content": text}]}


def test_worker_grounding_record_and_completion(store, brief, rubric):
    # Provider response uses source IDs returned by the worker, just as the real model does.
    class Recording(FakeProviders):
        def action(self, prompt, messages):
            self.action_calls += 1
            if self.action_calls == 1:
                pair = ("read_page", {"url": "https://synthetic-factory-1.example/"})
            elif self.action_calls == 2:
                pair = ("search", {"query": "synthetic-factory-1.example transformation"})
            elif self.action_calls == 3:
                # The searched fixture points to the second source for this test.
                pair = ("read_page", {"url": "https://synthetic-factory-1.example/news"})
            elif self.action_calls == 4:
                r, _ = fixture()
                source_ids = [m["result"]["source_id"] for m in messages if "source_id" in m.get("result", {})]
                for e in r.evidence:
                    e.source_id = source_ids[1] if e.source_id == "s1" else source_ids[0]
                pair = ("record_lead", {"research": r.model_dump(mode="json")})
            else:
                pair = ("finish", {})
            return Action(tool=pair[0], arguments_json=json.dumps(pair[1]), reason="Fixture step").model_dump_json(), {}

        def search(self, query):
            return {"results": [{"url": "https://synthetic-factory-1.example/news"}]}

    run = store.create_run(new_payload(brief, rubric, [{"company": "Synthetic Factory 1", "domain": "synthetic-factory-1.example"}], strategy="seeds", research_date=datetime.now(timezone.utc).date()))
    # Fixture signal is old relative to some system clocks, so record with a fixed research day.
    worker = Worker(store, Recording())
    worker.execute(run)
    assert store.run(run)["status"] == "completed"
    assert len(store.leads(run)) == 1
    state = store.run(run)["payload"]
    assert state["counters"] == {"searches": 1, "extractions": 2, "model_calls": 5}
    assert len(state["sources"]) == 2


@pytest.mark.parametrize("kind,expected", [("temporary", 2), ("auth", 1), ("quota", 1)])
def test_provider_failure_visible_and_bounded(store, brief, rubric, kind, expected):
    run = store.create_run(new_payload(brief, rubric, []))
    provider = FakeProviders(error=ProviderError("Visible provider failure", kind, 503))
    Worker(store, provider, sleep=lambda _: None).execute(run)
    assert store.run(run)["status"] == "paused"
    assert "Visible provider failure" in store.run(run)["error"]
    assert provider.action_calls == expected
    assert store.leads(run) == []  # Never substitute demo data.


def test_resume_preserves_usage(store, brief, rubric):
    run = store.create_run(new_payload(brief, rubric, []))
    Worker(store, FakeProviders(error=ProviderError("quota", "quota"))).execute(run)
    before = store.run(run)["payload"]["counters"]["model_calls"]
    Worker(store, FakeProviders()).execute(run)
    assert store.run(run)["payload"]["counters"]["model_calls"] == before + 1
    assert store.run(run)["status"] == "incomplete"


def test_limit_cannot_be_bypassed_by_resume(store, brief, rubric):
    run = store.create_run(new_payload(brief, rubric, [], limits=Limits(model_calls=1)))
    p = FakeProviders([("search", {"query": "industrial transformation"})])
    Worker(store, p).execute(run)
    assert store.run(run)["status"] == "limit"
    with pytest.raises(ValueError):
        Worker(store, p).execute(run)
    assert store.run(run)["payload"]["counters"]["model_calls"] == 1


def test_worker_lease_and_crash_recovery(store, brief, rubric):
    run = store.create_run(new_payload(brief, rubric, []))
    old = store.claim(run)
    with pytest.raises(ValueError):
        store.claim(run)
    with store.db() as db:
        db.execute("UPDATE runs SET lease=0 WHERE id=?", (run,))
    new = store.claim(run)
    assert old != new
    with pytest.raises(ValueError, match="lease"):
        store.save(run, {}, old)


def test_stop_saves_then_resumes(store, brief, rubric):
    class Stopping(FakeProviders):
        def action(self, prompt, messages):
            store.stop(run)
            return super().action(prompt, messages)
    run = store.create_run(new_payload(brief, rubric, []))
    Worker(store, Stopping()).execute(run)
    assert store.run(run)["status"] == "paused"
    assert store.run(run)["payload"]["counters"]["model_calls"] == 1


def test_credential_validation():
    for key in ("", "[REDACTED]", "your-api-key"):
        with pytest.raises(ProviderError):
            LiveProviders(key, key, "gemini-example").check()


@pytest.mark.parametrize("code,kind", [(401, "auth"), (429, "quota"), (503, "temporary"), (404, "config")])
def test_provider_wire_errors_are_sanitized(monkeypatch, code, kind):
    secret = "test-secret-value"
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: httpx.Response(code, json={"error": {"message": secret}}))
    with pytest.raises(ProviderError) as exc:
        post("https://api.tavily.com/search", {"Authorization": "Bearer " + secret}, {})
    assert exc.value.kind == kind and secret not in str(exc.value)


def test_csv_evaluation_and_explicit_synthetic(store, brief, rubric):
    run = load_demo(store, brief, rubric)
    leads = store.leads(run)
    assert len(leads) == 5 and all(l.mode == "synthetic" for l in leads)
    assert len({l.lead_id for l in leads}) == 5
    text = inspection_csv(leads)
    assert "record_json" in text and "synthetic" in text
    report = evaluation(store, run)
    assert report["total_cost_usd"] is None and report["approval_precision_at_10"] is None
    store.review(run, leads[0].lead_id, approve())
    handoff = store.handoff(run)
    assert len(handoff) == 1 and handoff[0]["mode"] == "synthetic" and handoff[0]["approval_basis"] == "mock"
