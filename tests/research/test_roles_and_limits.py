from datetime import date, datetime, timezone

import pytest

from lead_agent.assessment import issues_for, person_issues
from lead_agent.demo import fixture
from lead_agent.models import Limits, Person
from lead_agent.worker import Worker, new_payload
from test_identification import FakeProviders


def test_current_role_support_and_staleness(brief):
    research, sources = fixture()
    text = "2026-10-20: Morgan Example is Chief Information Officer at Synthetic Factory 1."
    sources[0].content += "\n" + text
    research.evidence.append(type(research.evidence[0])(
        evidence_id="person", category="person", source_id="s0", excerpt=text,
        observed_fact="Morgan Example is named as Chief Information Officer at the synthetic company."))
    person = Person(name="Morgan Example", role="Chief Information Officer", company=research.company,
                    evidence_ids=["person"], role_as_of=date(2026, 10, 20), role_date_excerpt=text,
                    verified_at=date(2026, 10, 20))
    research.people = [person]
    assert issues_for(research, sources, brief, date(2026, 10, 20)) == []
    person.role_as_of = date(2024, 1, 1)
    assert any("current role date" in s for s in person_issues(person, research, sources, brief, date(2026, 10, 20)))
    person.role = "Chief Executive Officer"
    assert any("role, employer" in s for s in person_issues(person, research, sources, brief, date(2026, 10, 20)))


def test_credit_limit_stops_before_second_web_request(store, brief, rubric):
    provider = FakeProviders([("search", {"query": "first research query"}),
                              ("search", {"query": "second research query"})])
    run = store.create_run(new_payload(brief, rubric, [], limits=Limits(tavily_credits=1)))
    Worker(store, provider).execute(run)
    row = store.run(run)
    assert row["status"] == "limit"
    assert row["payload"]["counters"]["searches"] == 1
    assert row["payload"]["reserved_credits"] == 1


def test_baseline_uses_fixed_discovery_queries(store, brief, rubric):
    run = store.create_run(new_payload(brief, rubric, [], strategy="baseline"))
    Worker(store, FakeProviders()).execute(run)
    row = store.run(run)
    assert row["status"] == "incomplete"
    assert len(row["payload"]["queries"]) == 6
    assert all(brief.industry in q and brief.geography in q for q in row["payload"]["queries"])


def test_pending_action_is_reused_after_stop(store, brief, rubric):
    class Stopping(FakeProviders):
        def action(self, prompt, messages):
            store.stop(run)
            return super().action(prompt, messages)
    run = store.create_run(new_payload(brief, rubric, []))
    Worker(store, Stopping()).execute(run)
    after = store.run(run)["payload"]
    assert after["pending_action"]["tool"] == "finish"
    provider = FakeProviders()
    Worker(store, provider).execute(run)
    assert provider.action_calls == 0
    assert store.run(run)["status"] == "incomplete"
