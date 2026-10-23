"""Small typed research steps and conservative evidence admission for research v2."""
from datetime import date
import re
from urllib.parse import urljoin, urlsplit

from pydantic import Field

from .assessment import supports_date
from .models import Model, Observation, Research, public_url, domain


class Fact(Model):
    quote_id: str
    category: str = Field(pattern="^(identity|industry|geography|signal|account|person)$")
    observed_fact: str = Field(min_length=10, max_length=600)
    publication_date: date | None = None
    date_excerpt: str | None = None


class Facts(Model):
    facts: list[Fact] = Field(max_length=12)


class Verdict(Model):
    fact_id: str
    supported: bool
    reason: str = Field(min_length=10, max_length=500)


class EvidenceReview(Model):
    verdicts: list[Verdict] = Field(max_length=12)
    industry_match: bool | None
    geography_match: bool | None


class Interpretation(Model):
    problem_hypothesis: str = Field(min_length=20, max_length=1400)
    cgs_relevance: str = Field(min_length=20, max_length=1400)
    alternative_explanation: str = Field(min_length=20, max_length=1000)
    signal_evidence_ids: list[str] = Field(min_length=1, max_length=6)
    relevance: str = Field(pattern="^(low|medium|high)$")
    unanswered_questions: list[str] = Field(min_length=1, max_length=8)
    next_action: str = Field(min_length=10, max_length=1200)


def substantive(text):
    """Reject navigation/image/link-only material; this is not semantic proof."""
    plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    plain = re.sub(r"https?://\S+", "", plain)
    plain = re.sub(r"\[[^\]]*\]\([^)]*\)", "", plain)
    return len(re.findall(r"\b[A-Za-z][A-Za-z'-]+\b", plain)) >= 12 and bool(re.search(r"[.!?](?:\s|$)", plain))


def evidence_bank(sources, brief):
    """Keep exact prose paragraphs with stable IDs; bound context before model calls."""
    bank = {}
    terms = set(re.findall(r"\b\w{4,}\b", (brief.capability + ' ' + ' '.join(brief.signals)).lower()))
    for source in sources:
        ranked = []
        for index, block in enumerate(re.split(r"\n\s*\n", source['content'])):
            for start in range(0, len(block), 1600):
                text = block[start:start+1600].strip()
                if substantive(text):
                    words = set(re.findall(r"\b\w{4,}\b", text.lower()))
                    score = len(words & terms) + 3 * bool(re.search(r"\b(20\d{2}|manufactur\w*|United States|U\.S\.)", text))
                    ranked.append((score, index, start, text))
        # Reserve per-page capacity so a long annual report cannot crowd out articles.
        for _, index, start, text in sorted(ranked, key=lambda row: -row[0])[:10]:
            key = f"{source['source_id']}:{index}:{start}"
            bank[key] = {'source_id': source['source_id'], 'url': source['url'], 'excerpt': text}
    return bank


def admit_facts(batch, bank, sources, today, max_age):
    source_map = {s['source_id']: s for s in sources}
    admitted, errors = [], []
    seen = set()
    for item in batch.facts:
        passage = bank.get(item.quote_id)
        reason = None
        if not passage or not substantive(passage['excerpt']):
            reason = 'Quote must be exact substantive prose from its selected passage.'
        if reason:
            errors.append({'category': item.category, 'reason': reason})
            continue
        key = (item.category, passage['excerpt'])
        if key in seen:
            continue
        seen.add(key)
        published, date_text = item.publication_date, item.date_excerpt
        if published:
            numeric = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', date_text or '')
            numeric_match = bool(numeric and int(numeric[2]) > 12 and
                                 (int(numeric[3]), int(numeric[1]), int(numeric[2])) == (published.year, published.month, published.day))
            valid_date = (date_text and date_text in source_map[passage['source_id']]['content']
                          and (supports_date(published, date_text) or numeric_match) and published <= today)
            if not valid_date:
                errors.append({'category': item.category, 'reason': 'Unsupported date removed; the substantive fact remains undated.'})
                published, date_text = None, None
            elif item.category == 'signal' and (today-published).days > max_age:
                errors.append({'category': item.category, 'reason': 'Dated event is outside the configured signal window.'})
                continue
        admitted.append(Observation(evidence_id=f'e{len(admitted)+1}', category=item.category,
            observed_fact=item.observed_fact, source_id=passage['source_id'], excerpt=passage['excerpt'],
            publication_date=published, date_excerpt=date_text).model_dump(mode='json'))
    return admitted, errors


def reviewed_facts(facts, review):
    ids = {f['evidence_id'] for f in facts}
    verdicts = {v.fact_id: v for v in review.verdicts}
    if len(verdicts) != len(review.verdicts) or set(verdicts) != ids:
        raise ValueError('Review must return exactly one verdict for every supplied fact ID.')
    return [f for f in facts if verdicts[f['evidence_id']].supported]


def gaps(facts, review, brief=None):
    categories = {f['category'] for f in facts}
    missing = [c for c in ('identity', 'industry', 'geography', 'signal') if c not in categories]
    if not any(f['category'] == 'signal' and f['publication_date'] for f in facts):
        missing.append('dated_signal')
    if review.industry_match is not True:
        missing.append('industry_match')
    if review.geography_match is not True:
        missing.append('geography_match')
    if brief and 'manufacturing operations' in brief.geography.lower():
        geographic = ' '.join(f['excerpt'] for f in facts if f['category'] == 'geography')
        if not re.search(r'\b(plant\w*|factor(?:y|ies)|manufactur\w*|production facilit\w*)\b', geographic, re.I):
            missing.append('manufacturing_operations_in_geography')
    return list(dict.fromkeys(missing))


def assemble(entry, interpretation):
    facts = entry['verified_facts']
    signals = {f['evidence_id'] for f in facts if f['category'] == 'signal' and f['publication_date']}
    if not set(interpretation.signal_evidence_ids) <= signals:
        raise ValueError('Interpretation must reference admitted dated business signals only.')
    refs = interpretation.signal_evidence_ids
    components = {key: {'assessment': None, 'reason': 'Not established by this research pass.', 'evidence_ids': []}
                  for key in ('problem', 'capability_fit', 'timing', 'buyer_access')}
    for key in ('problem', 'capability_fit'):
        components[key] = {'assessment': interpretation.relevance,
                           'reason': interpretation.cgs_relevance, 'evidence_ids': refs}
    # A recent event alone does not establish a purchasing deadline.
    return Research(company=entry['company'], domain=entry['domain'], identity='confirmed', eligibility='eligible',
        eligibility_reason='Industry and operating geography supported by admitted facts and a separate model review.',
        evidence=facts, people=None, problem_hypothesis=interpretation.problem_hypothesis,
        cgs_relevance=interpretation.cgs_relevance, alternative_explanation=interpretation.alternative_explanation,
        confidence='medium', unanswered_questions=interpretation.unanswered_questions, conflicts=[],
        components=components, suggested_next_investigation=interpretation.next_action)


def rank_urls(results, entry, brief, today):
    terms = set(re.findall(r'\b\w{4,}\b', brief.capability.lower() + ' ' + ' '.join(brief.signals).lower()))
    ranked = {}
    for row in results:
        try:
            url = public_url(row['url'])
        except (ValueError, KeyError):
            continue
        if domain(url) != entry['domain']:
            continue
        path = urlsplit(url).path.lower()
        if re.search(r'\.(png|jpg|jpeg|svg|gif|zip)$|privacy|cookie|terms-of|login|impressum|legal', path):
            continue
        text = (row.get('title', '') + ' ' + row.get('discovery_hint', '') + ' ' + path).lower()
        score = len(terms & set(re.findall(r'\b\w{4,}\b', text)))
        score += 4 * bool(re.search(r'news|story|stories|press|release|article', path))
        score += 3 * bool(re.search(r'\bmanufactur\w*|\bfactor(?:y|ies)\b|\bplant\b|facilit', text))
        score += 2 * (str(today.year) in text or str(today.year-1) in text)
        years = [int(y) for y in re.findall(r'(?<!\d)(20\d{2})(?!\d)', path)]
        score -= 10 * bool(years and max(years) < today.year-1)
        score -= 3 * bool(re.search(r'annual.report|\.pdf$', path))
        if len(path.strip('/')) < 4:
            score -= 8
        ranked[url] = max(ranked.get(url, -100), score)
    return [url for url in sorted(ranked, key=lambda url: -ranked[url]) if ranked[url] >= 1]


def article_links(sources):
    result = []
    for source in sources:
        text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', source['content'])
        for title, link in re.findall(r'\[([^\]]{8,1500})\]\(([^\s)]+)\)', text):
            try:
                url = public_url(urljoin(source['url'], link))
            except ValueError:
                continue
            result.append({'url': url, 'title': ' '.join(title.split())[:500]})
    return result
