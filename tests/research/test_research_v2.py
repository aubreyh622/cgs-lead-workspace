"""Quality failure cases, persisted stages and integrated API dispatch without network."""
import json
from datetime import date
from pathlib import Path
import time

import pytest

from backend.research_service import ResearchService, ResearchJobStore
from lead_agent.models import Limits, Seed
from lead_agent.quality import (Facts, EvidenceReview, admit_facts, reviewed_facts, evidence_bank,
                                substantive, rank_urls, article_links, gaps)
from lead_agent.research_v2 import new_research_payload
from lead_agent.worker import Worker

HOST = 'fixture-manufacturer.example'
COMPANY = 'Fixture Manufacturer'
TODAY = date.today().isoformat()
TEXTS = {
    'identity': f'{COMPANY} is a manufacturing company that makes industrial equipment for customers in several markets.',
    'industry': f'{COMPANY} manufactures industrial equipment and operates its own production facilities to supply customers.',
    'geography': f'{COMPANY} operates manufacturing plants in Ohio and Texas in the United States, employing production teams at both locations.',
    'signal': f'On {TODAY}, {COMPANY} announced an artificial intelligence programme to integrate production data across its manufacturing plants.',
}
PAGE = '\n\n'.join(TEXTS.values())


class Provider:
    def __init__(self, *, reject=False, malformed=False):
        self.reject, self.malformed = reject, malformed
        self.stages = []
        self.searches = 0

    def check(self):
        pass

    def search(self, query):
        self.searches += 1
        return {'results': [{'url': f'https://{HOST}/news/ai-programme', 'title': f'{COMPANY} announces manufacturing expansion',
                             'content': f'{COMPANY} announces an AI manufacturing investment.'}]}

    def read_page(self, url):
        return {'results': [{'url': url, 'raw_content': PAGE}]}

    def discovery(self, prompt, schema):
        data = json.loads(prompt)
        stage = data['stage']
        self.stages.append(stage)
        if stage == 'candidate_extract':
            result = {'candidates': [{'company': COMPANY, 'origin_id': data['discovery_inputs'][0]['origin_id'],
                'name_excerpt': COMPANY, 'company_type': 'manufacturer', 'signal_hint': 'Reported AI manufacturing investment.'}]}
        elif stage == 'resolve_company':
            result = {'official_url': f'https://{HOST}/news/ai-programme', 'reason': 'The supplied official company announcement.'}
        elif stage == 'extract_facts':
            if self.malformed and self.stages.count(stage) == 1:
                return '{bad', {'totalTokenCount': 10}
            result = {'facts': []}
            for category, text in TEXTS.items():
                key = next(k for k, v in data['passages'].items() if text in v['excerpt'])
                result['facts'].append({'quote_id': key, 'category': category, 'observed_fact': text,
                    'publication_date': TODAY if category == 'signal' else None,
                    'date_excerpt': TODAY if category == 'signal' else None})
        elif stage == 'review_evidence':
            result = {'verdicts': [{'fact_id': f['fact_id'], 'supported': not(self.reject and f['category'] == 'geography'),
                                   'reason': 'The quote explicitly supports the claim.'} for f in data['facts']],
                      'industry_match': True, 'geography_match': None if self.reject else True}
        elif stage == 'interpret':
            result = {'problem_hypothesis': 'The programme may require coordinated data ownership and operating-model decisions.',
                'cgs_relevance': 'CGS could help prioritize the first integration decisions if internal teams need support.',
                'alternative_explanation': 'Existing internal teams and partners may already cover this planning work.',
                'signal_evidence_ids': ['e4'], 'relevance': 'medium',
                'unanswered_questions': ['Who owns the integration roadmap and is external support needed?'],
                'next_action': 'Verify the Head of Transformation through the official leadership page before requesting a discussion.'}
        else:
            raise AssertionError(stage)
        return json.dumps(result), {'totalTokenCount': 10}


def limits():
    return Limits(searches=10, extractions=12, model_calls=18, tavily_credits=22, active_seconds=60)


def test_full_discovery_and_known_company_produce_reviewable_brief(store, brief, rubric):
    for company in (None, Seed(company=COMPANY, domain=HOST)):
        provider = Provider()
        run = store.create_run(new_research_payload(brief, rubric, company=company, target=1, limits=limits()))
        Worker(store, provider).execute(run)
        assert store.run(run)['status'] == 'completed', store.run(run)['error']
        lead = store.leads(run)[0]
        assert lead.review.decision == 'Ready for review'
        assert lead.review.reviewer is None
        assert len(lead.research.evidence) == 4 and lead.research.people is None
        assert provider.stages[-3:] == ['extract_facts', 'review_evidence', 'interpret']


def test_semantic_rejection_never_counts_as_success(store, brief, rubric):
    provider = Provider(reject=True)
    run = store.create_run(new_research_payload(brief, rubric, company=Seed(company=COMPANY, domain=HOST), limits=limits()))
    Worker(store, provider).execute(run)
    assert store.run(run)['status'] == 'incomplete'
    assert not store.leads(run)
    candidate = store.run(run)['payload']['discovery']['candidates'][0]
    assert 'geography' in candidate['quality_gaps']
    assert provider.stages.count('extract_facts') == 1  # No new pages: no identical retry.
    assert 'interpret' not in provider.stages


def test_format_retry_does_not_trigger_web_repair(store, brief, rubric):
    provider = Provider(malformed=True)
    run = store.create_run(new_research_payload(brief, rubric, company=Seed(company=COMPANY, domain=HOST), limits=limits()))
    Worker(store, provider).execute(run)
    assert store.run(run)['status'] == 'completed'
    assert provider.stages.count('extract_facts') == 2
    assert provider.searches == 2  # Signal and cohort; no format-triggered web repair.


def test_weak_material_and_stale_search_results_are_not_preferred(brief):
    assert not substantive('![Texas production factory](https://example.com/TEXAS_04_LAYS.jpg)')
    assert not substantive('[AI investment announced at factory](https://example.com/news/ai-investment)')
    assert not substantive('© 2026 Manufacturing United States Contact Privacy Cookies')
    urls = rank_urls([{'url': f'https://{HOST}/annual-report-2021.pdf'},
                      {'url': f'https://{HOST}/news/2026/ai-manufacturing-investment'}],
                     {'domain': HOST}, brief, date(2026, 10, 23))
    assert urls[0].endswith('ai-manufacturing-investment') and len(urls) == 1


def test_invalid_quotes_dates_and_review_coverage_fail_closed(brief):
    source = {'source_id': 's1', 'url': f'https://{HOST}/news', 'content': PAGE}
    bank = evidence_bank([source], brief)
    key = next(k for k, v in bank.items() if TEXTS['signal'] in v['excerpt'])
    facts = Facts(facts=[{'quote_id': key, 'category': 'signal',
                         'observed_fact': TEXTS['signal'], 'publication_date': TODAY, 'date_excerpt': 'invented date'}])
    admitted, rejected = admit_facts(facts, bank, [source], date.today(), 365)
    assert admitted[0]['publication_date'] is None and rejected
    with pytest.raises(ValueError, match='every supplied fact'):
        reviewed_facts([{'evidence_id': 'e1'}], EvidenceReview(verdicts=[], industry_match=True, geography_match=True))


def test_multiline_article_links_and_cohort_gap():
    links = article_links([{'url': f'https://{HOST}/', 'content': '[![Photo](pic.jpg)\n\nNew manufacturing investment\n\nRead more](/news/expansion)'}])
    assert links == [{'url': f'https://{HOST}/news/expansion', 'title': 'New manufacturing investment Read more'}]
    from types import SimpleNamespace
    missing = gaps([{'category': 'geography', 'excerpt': 'Our company has a US subsidiary.', 'publication_date': None}],
                   EvidenceReview(verdicts=[], industry_match=True, geography_match=True),
                   SimpleNamespace(geography='Manufacturing operations in the United States'))
    assert 'manufacturing_operations_in_geography' in missing


def test_integrated_discovery_job_without_company(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    service = ResearchService(root, ResearchJobStore(tmp_path/'research.sqlite3'), 'gemini-test', lambda _: Provider())
    monkeypatch.setattr(service, 'discovery_limits', limits)
    try:
        started, created = service.start({'request_id': 'discover', 'consultant_id': 'tester', 'mode': 'discovery', 'target': 1})
        deadline = time.monotonic() + 10
        while service.status(started['run_id'])['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
        result = service.results(started['run_id'])
        assert created and result['run']['status'] == 'completed'
        assert result['run']['briefs_ready'] == 1
        assert result['run']['candidate_queue'][0]['company'] == COMPANY
        assert service.start({'request_id': 'discover', 'consultant_id': 'tester', 'mode': 'discovery', 'target': 1})[1] is False
    finally:
        service.shutdown()
