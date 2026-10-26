"""Deterministic provenance and eligibility gates, followed by configured scoring."""
from datetime import date, datetime, timezone, timedelta
import re

from .models import Lead, Observation, Priority, Research, Review, Source, domain, stable_id


def normalized(value):
    return " ".join(value.split())


def supports_date(value, text):
    text = normalized(text or "").casefold().replace(".", "")
    text = re.sub(r"\b0([1-9])\b", r"\1", text)
    forms = (value.isoformat(), value.strftime("%B %d, %Y"), value.strftime("%b %d, %Y"),
             value.strftime("%d %B %Y"), value.strftime("%d %b %Y"), value.strftime("%d.%m.%y"))
    return any(re.sub(r"\b0([1-9])\b", r"\1", f.casefold().replace(".", "")) in text for f in forms)


def issues_for(research: Research, sources: list[Source], brief, today: date):
    issues = []
    source_map = {s.source_id: s for s in sources}
    evidence_map = {e.evidence_id: e for e in research.evidence}
    if len(evidence_map) != len(research.evidence):
        issues.append("Evidence IDs must be unique.")
    valid = set()
    for e in research.evidence:
        source = source_map.get(e.source_id)
        if not source or normalized(e.excerpt) not in normalized(source.content):
            issues.append(f"{e.evidence_id}: excerpt is not in the stored source.")
            continue
        valid.add(e.evidence_id)
        if source.retrieved_at.tzinfo is None or source.retrieved_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            issues.append(f"{e.evidence_id}: retrieval date is in the future.")
        if e.publication_date:
            if (not e.date_excerpt or normalized(e.date_excerpt) not in normalized(source.content)
                    or not supports_date(e.publication_date, e.date_excerpt)):
                issues.append(f"{e.evidence_id}: publication date needs a matching source date excerpt.")
            if e.publication_date > today:
                issues.append(f"{e.evidence_id}: publication date is in the future.")
            if e.category in ("problem", "signal", "timing") and (today - e.publication_date).days > brief.signal_max_age_days:
                issues.append(f"{e.evidence_id}: business signal is stale under this brief.")
    if research.domain in brief.excluded_domains:
        issues.append("Company domain is excluded.")
    if research.identity != "confirmed":
        issues.append("Company identity needs resolution.")
    if research.eligibility == "ineligible":
        issues.append("Company is outside the brief: " + research.eligibility_reason)
    identity = [e for e in research.evidence if e.category == "identity" and e.evidence_id in valid]
    if not any(domain(source_map[e.source_id].url) == research.domain
               and research.company.casefold() in e.excerpt.casefold() for e in identity):
        issues.append("Official-site identity evidence must name the company.")
    if not any(e.category in ("problem", "signal") and e.evidence_id in valid for e in research.evidence):
        issues.append("No supported business event or problem signal yet.")
    for name, c in research.components.items():
        if c.assessment is not None and (not c.evidence_ids or not set(c.evidence_ids) <= valid):
            issues.append(f"{name}: assessment lacks supported evidence references.")
    if any(research.components[k].assessment is None for k in ("problem", "capability_fit")):
        issues.append("Core problem or capability fit remains unknown.")
    if research.components["timing"].assessment is not None and not any(
            e.category in ("timing", "problem", "signal") and e.publication_date and e.evidence_id in valid
            for e in research.evidence):
        issues.append("Timing assessment needs a dated business signal.")
    issues.extend("Conflicting source: " + c for c in research.conflicts)
    return list(dict.fromkeys(issues))


def person_issues(person, research, sources, brief, today):
    """An undated official leadership page can support a role checked today.

    A publication date, when supplied, must be supported. Unverified people do
    not prevent useful research about the company itself.
    """
    issues = []
    source_map = {s.source_id: s for s in sources}
    evidence_map = {e.evidence_id: e for e in research.evidence}
    valid = {e.evidence_id for e in research.evidence if e.source_id in source_map
             and normalized(e.excerpt) in normalized(source_map[e.source_id].content)}
    if person:
        es = [evidence_map[k] for k in person.evidence_ids if k in valid]
        text = " ".join(e.excerpt for e in es).casefold()
        if (len(es) != len(person.evidence_ids) or person.name.casefold() not in text
                or person.role.casefold() not in text or person.company.casefold() != research.company.casefold()):
            issues.append(f"{person.name}: role, employer and name need exact source support.")
        if person.role_as_of:
            date_text = person.role_date_excerpt or ""
            if (not date_text or not any(date_text in source_map[e.source_id].content for e in es)
                    or not supports_date(person.role_as_of, date_text)
                    or (today - person.role_as_of).days > brief.role_max_age_days
                    or person.role_as_of > today):
                issues.append(f"{person.name}: current role date requires verification.")
        elif not any(e.category == "person" and domain(source_map[e.source_id].url) == research.domain
                     and person.name.casefold() in e.excerpt.casefold()
                     and person.role.casefold() in e.excerpt.casefold()
                     and source_map[e.source_id].retrieved_at.date() == today for e in es):
            issues.append(f"{person.name}: an undated role needs a current official leadership-page excerpt.")
        if person.verified_at != today:
            issues.append(f"{person.name}: role was not checked on the research date.")
    return issues


def gaps_for(research, sources):
    gaps = []
    if research.eligibility == "unknown":
        gaps.append("Confirm industry and geography before approving handoff: " + research.eligibility_reason)
    for category in ("industry", "geography"):
        if not any(e.category == category for e in research.evidence):
            gaps.append(f"No explicit {category} evidence yet.")
    if len({s.url for s in sources}) < 2:
        gaps.append("Only one source page; seek corroboration where useful.")
    if not research.people:
        gaps.append("No buyer verified yet; company research can still be reviewed.")
    if research.components["timing"].assessment is None:
        gaps.append("Timing is unknown; no verified current deadline or dated signal.")
    return gaps


def prioritize(research, rubric, issues):
    points = {"low": 0, "medium": 50, "high": 100}
    known = {k: c for k, c in research.components.items() if c.assessment is not None}
    coverage = sum(rubric.dimensions[k].weight for k in known)
    subtotal = sum(rubric.dimensions[k].weight * points[c.assessment] for k, c in known.items())
    # Missing data is excluded, never assigned zero. Show coverage and full-score bounds.
    score = round(subtotal / coverage, 1) if coverage and not issues else None
    band = ("Needs research" if score is None else "high" if score >= rubric.high_threshold
            else "medium" if score >= rubric.medium_threshold else "low")
    return Priority(rubric_version=rubric.version, score=score, coverage=round(coverage, 4),
                    possible_range=[round(subtotal, 1), round(subtotal + (1 - coverage) * 100, 1)],
                    band=band, provisional=not bool(rubric.approval_basis == "cgs" and rubric.approved_by and rubric.approved_at),
                    components=research.components)


def make_lead(run_id, research, sources, brief, rubric, today, mode="live"):
    research = research.model_copy(deep=True)
    # Resolve equivalent citation identifiers without changing the source or its text.
    source_map = {s.source_id: s for s in sources}
    url_ids = {s.url: s.source_id for s in sources}
    date_gaps = []
    for e in research.evidence:
        e.source_id = url_ids.get(e.source_id, e.source_id)
        # Numeric dates such as 10.08.26 have two plausible calendar readings.
        numeric = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{2}|\d{4})", e.date_excerpt or "")
        if e.publication_date and numeric and all(1 <= int(n) <= 12 for n in numeric.groups()[:2]) and numeric[1] != numeric[2]:
            date_gaps.append(f"{e.evidence_id}: numeric publication date is ambiguous; publication date and timing remain unknown.")
            e.publication_date = None
    if research.identity == "confirmed" and not any(e.category == "identity" for e in research.evidence) and len(research.evidence) < 30:
        grounded = next((e for e in research.evidence if e.source_id in source_map
                         and domain(source_map[e.source_id].url) == research.domain
                         and research.company.casefold() in e.excerpt.casefold()
                         and normalized(e.excerpt) in normalized(source_map[e.source_id].content)), None)
        if grounded:
            research.evidence.append(Observation(evidence_id="official-identity-" + grounded.evidence_id,
                category="identity", observed_fact=f"The supplied official company domain names {research.company}.",
                source_id=grounded.source_id, excerpt=grounded.excerpt))
    evidence_ids = {e.evidence_id for e in research.evidence}
    def evidence_references(refs):
        result = []
        for ref in refs:
            sid = url_ids.get(ref, ref)
            result.extend([ref] if ref in evidence_ids else
                          [e.evidence_id for e in research.evidence if e.source_id == sid] or [ref])
        return list(dict.fromkeys(result))
    for c in research.components.values():
        c.evidence_ids = evidence_references(c.evidence_ids)
    for p in research.people or []:
        p.evidence_ids = evidence_references(p.evidence_ids)
    # An undated event remains useful; an unsupported timing assessment is unknown.
    if research.components["timing"].assessment is not None and not any(
            e.category in ("problem", "signal", "timing") and e.publication_date for e in research.evidence):
        research.components["timing"].assessment = None
        research.components["timing"].evidence_ids = []
        research.components["timing"].reason = "Timing is unknown because no source-supported publication date was supplied."
    contact_gaps = []
    verified = []
    for person in research.people or []:
        problems = person_issues(person, research, sources, brief, today)
        if problems:
            contact_gaps.append("A proposed buyer could not be verified and was omitted: " + "; ".join(problems))
        else:
            verified.append(person)
    research.people = verified or None
    if not research.people:
        research.components["buyer_access"].assessment = None
        research.components["buyer_access"].evidence_ids = []
        research.components["buyer_access"].reason = "No verified buyer; access remains unknown."
    issues = issues_for(research, sources, brief, today)
    used = {e.source_id for e in research.evidence}
    return Lead(run_id=run_id, lead_id=stable_id(research.domain, mode), mode=mode,
                approval_basis=(brief.approval_basis if brief.approval_basis == rubric.approval_basis else "pending"),
                research_method="synthetic_fixture" if mode == "synthetic" else "agent",
                problem_brief_id=brief.brief_id, problem_brief_version=brief.version,
                research=research, sources=[s for s in sources if s.source_id in used],
                priority=prioritize(research, rubric, issues), validation_issues=issues,
                research_gaps=date_gaps + contact_gaps + gaps_for(research, [s for s in sources if s.source_id in used]),
                review=Review(decision="Needs research" if issues or research.eligibility != "eligible"
                              or any(not any(e.category == c for e in research.evidence) for c in ("industry", "geography"))
                              else "Ready for review"))
