"""Explicit offline fixtures. Never called by live provider error handling."""
from datetime import date

from .assessment import make_lead
from .models import Brief, Research, Rubric, Source


def fixture(today=date(2026, 10, 20), number=1):
    company = f"Synthetic Factory {number}"
    iso = today.isoformat()
    texts = [f"{company} manufactures industrial equipment and is headquartered in the United States.",
             f"{iso}: {company} reports disconnected AI pilots delaying production improvements and starts a strategy review."]
    # Independent reserved .example domains avoid accidental real-company requests.
    host = f"synthetic-factory-{number}.example"
    sources = [Source(source_id=f"s{i}", url=f"https://{host}/{'about' if i == 0 else 'news'}",
                      retrieved_at=iso + "T12:00:00+00:00", content=text) for i, text in enumerate(texts)]
    evidence = [dict(evidence_id=kind, category=kind, observed_fact=texts[0],
                     source_id="s0", excerpt=texts[0]) for kind in ("identity", "industry", "geography")]
    evidence.append(dict(evidence_id="problem", category="problem", observed_fact=texts[1], source_id="s1",
                         excerpt=texts[1], publication_date=iso, date_excerpt=texts[1]))
    r = Research(company=company, domain=host, identity="confirmed", eligibility="eligible",
                 eligibility_reason="Synthetic manufacturing example in the specified geography.", evidence=evidence,
                 people=None, problem_hypothesis="Fragmented pilots may indicate a need for coordinated first-mile planning.",
                 cgs_relevance="The observed coordination gap may fit strategy and capability assessment support.",
                 alternative_explanation="The internal transformation team may already have an effective resolution plan.",
                 confidence="medium", unanswered_questions=["Is external advice needed?", "Who owns the problem?"], conflicts=[],
                 components={k: dict(assessment="high" if k != "buyer_access" else None,
                                     reason="Synthetic evidence illustrates a direct adoption and planning problem." if k != "buyer_access"
                                     else "No current named buyer or relationship was verified.",
                                     evidence_ids=["problem"] if k != "buyer_access" else [])
                             for k in ("problem", "capability_fit", "timing", "buyer_access")},
                 suggested_next_investigation="Ask a reviewer whether the problem merits further investigation.")
    return r, sources


def scenario(number):
    research, sources = fixture(number=number)
    group = (number - 1) % 5
    if group == 1:
        for name in ("problem", "capability_fit", "timing"):
            research.components[name].assessment = "medium"
            research.components[name].reason = "A single synthetic signal warrants investigation; the magnitude of the need is unconfirmed."
    elif group == 2:
        for name in ("problem", "capability_fit"):
            research.components[name].assessment = "low"
            research.components[name].reason = "The synthetic company is already solving this internally; relevance to external advice is weak."
    elif group == 3:
        research.identity = "ambiguous"
        research.unanswered_questions.append("Which similarly named company is the correct entity?")
    elif group == 4:
        research.conflicts = ["Two synthetic sources disagree on whether the transformation remains active."]
    return research, sources


def load_demo(store, brief: Brief, rubric: Rubric, count=5):
    from .worker import new_payload
    payload = new_payload(brief, rubric, [], target=count)
    payload["mode"] = "synthetic"
    run_id = store.create_run(payload)
    token = store.claim(run_id)
    for i in range(1, count + 1):
        research, sources = scenario(i)
        store.add_lead(make_lead(run_id, research, sources, brief, rubric, date(2026, 10, 20), "synthetic"))
    store.finish(run_id, "completed", f"{count} fictional offline cards. Mock demonstration only.", token)
    return run_id
