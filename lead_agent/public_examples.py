"""Small manually researched examples, not autonomous agent outputs.

Stored source text consists of short, exact excerpt snapshots collected on 20 Oct
2026. They deliberately leave unsupported attributes unknown. All remain reviewable.
"""
from datetime import date

from .assessment import make_lead
from .models import Research, Source, now
from .worker import new_payload

SNAPSHOTS = [
    {
        "company": "Ford", "domain": "ford.com",
        "sources": [
            ("https://corporate.ford.com/", "Ford Motor Company\nAmerican automotive manufacturing"),
            ("https://www.fromtheroad.ford.com/us/en/articles/2026/ford-establishes-product-creation-industrialization-organization",
             "15.04.26\nuniting its advanced technology and global industrial teams")
        ],
        "observations": [
            ("identity", "s0", "Ford Motor Company", "The official corporate page identifies the Ford entity."),
            ("industry", "s0", "American automotive manufacturing", "The official site describes automotive manufacturing."),
            ("problem", "s1", "uniting its advanced technology and global industrial teams", "Ford announced an organizational change joining technology and industrial teams.")
        ],
        "date": "2026-04-15", "date_excerpt": "15.04.26",
        "hypothesis": "Joining technology and industrial teams may create first-mile coordination and operating-model work; a current unmet advisory need is not established.",
        "alternative": "Ford's announced internal organization may already have sufficient strategy and delivery capacity.",
        "next": "Verify headquarters, current program gaps and the accountable transformation owner; do not assume demand for consultants."
    },
    {
        "company": "Lear Corporation", "domain": "lear.com",
        "sources": [
            ("https://www.lear.com/newsroom/lear-reports-second-quarter-2026-results",
             "July 31, 2026\nLear Corporation\nAdvanced Manufacturing Integration Center"),
            ("https://www.lear.com/newsroom/palantir-and-lear-announce-five-year-partnership-expansion-to-accelerate-automotive-technology-transformation-ybzjj",
             "September 4, 2025\nfive-year expansion of its partnership")
        ],
        "observations": [
            ("identity", "s0", "Lear Corporation", "The retrieved company announcement names Lear Corporation."),
            ("problem", "s0", "Advanced Manufacturing Integration Center", "Lear reported opening a center focused on manufacturing integration."),
            ("other", "s1", "five-year expansion of its partnership", "The earlier announcement describes an expanded existing technology partnership.")
        ],
        "date": "2026-07-31", "date_excerpt": "July 31, 2026",
        "hypothesis": "An expanding digital manufacturing program may merit investigation, but successful internal execution and an incumbent provider weaken an assumption of unmet first-mile need.",
        "alternative": "The existing program and technology partner may already cover the relevant work.",
        "next": "Treat this as an incumbent-provider counterexample. Verify a specific unresolved problem before prioritizing outreach."
    },
    {
        "company": "Dana", "domain": "dana.com",
        "sources": [
            ("https://www.dana.com/newsroom/press-releases/dana-incorporated-completes-sale-of-off-highway-business/",
             "Dana Incorporated\nJan. 02, 2026\nongoing transformation strategy"),
            ("https://www.dana.com/company/", "Dana continued to make substantial progress on its enterprise strategy")
        ],
        "observations": [
            ("identity", "s0", "Dana Incorporated", "The official announcement identifies Dana Incorporated."),
            ("problem", "s0", "ongoing transformation strategy", "The divestiture announcement connects the transaction to a transformation strategy."),
            ("other", "s1", "Dana continued to make substantial progress on its enterprise strategy", "The corporate history page describes previous enterprise-strategy activity.")
        ],
        "date": "2026-01-02", "date_excerpt": "Jan. 02, 2026",
        "hypothesis": "A major divestiture may require a revised technology and operating model; the current scope of any unmet planning need is unknown.",
        "alternative": "Separation planning and advisers already retained for the transaction may cover the necessary work.",
        "next": "Verify present separation and technology-planning gaps, company geography and existing advisory coverage."
    },
    {
        "company": "BorgWarner", "domain": "borgwarner.com",
        "sources": [
            ("https://www.borgwarner.com/company", "About BorgWarner\nWe design and manufacture the systems"),
            ("https://www.borgwarner.com/company/history", "our Charging Forward 2027 strategy")
        ],
        "observations": [
            ("identity", "s0", "About BorgWarner", "The official company page identifies BorgWarner."),
            ("industry", "s0", "We design and manufacture the systems", "The company describes manufacturing activity."),
            ("problem", "s1", "our Charging Forward 2027 strategy", "The corporate history page names a transformation strategy, without establishing a newly dated problem.")
        ],
        "date": None, "date_excerpt": None,
        "hypothesis": "A propulsion transformation could involve strategy and capability changes, but generic strategic ambition does not establish a current business problem.",
        "alternative": "The strategy may be mature and fully supported; an undated history page is weak timing evidence.",
        "next": "Find a fresh, specific business problem and verify headquarters before assigning priority."
    },
    {
        "company": "Magna", "domain": "magna.com",
        "sources": [
            ("https://www.magna.com/company/company-information", "About Magna\ndeep manufacturing expertise"),
            ("https://www.magna.com/stories/tech-talks/2026/how-ai-is-transforming-adas-development",
             "August 24, 2026\nautomate requirements management")
        ],
        "observations": [
            ("identity", "s0", "About Magna\ndeep manufacturing expertise", "The official company page identifies Magna and describes manufacturing expertise."),
            ("industry", "s0", "deep manufacturing expertise", "The official description refers to manufacturing expertise."),
            ("problem", "s1", "automate requirements management", "The company article describes a possible application of AI in requirements work; it does not establish a purchasing need.")
        ],
        "date": "2026-08-24", "date_excerpt": "August 24, 2026",
        "hypothesis": "AI-enabled engineering change may create strategy or capability questions, but a general technology article provides limited evidence of an unmet organizational problem.",
        "alternative": "The article may showcase existing capability rather than signal a need for external support; geographic eligibility also needs checking.",
        "next": "Check headquarters against the pilot geography and look for a specific unresolved scaling or adoption problem."
    }
]


def load_public_examples(store, brief, rubric):
    seeds = [{"company": x["company"], "domain": x["domain"]} for x in SNAPSHOTS]
    payload = new_payload(brief, rubric, seeds, strategy="seeds", research_date=date(2026, 10, 20))
    payload["research_method"] = "manual_public_web"
    run_id = store.create_run(payload)
    token = store.claim(run_id)
    for x in SNAPSHOTS:
        sources = [Source(source_id=f"s{i}", url=url, retrieved_at=now(), content=text)
                   for i, (url, text) in enumerate(x["sources"])]
        evidence = []
        for i, (category, sid, quote, fact) in enumerate(x["observations"]):
            e = {"evidence_id": f"e{i}", "category": category, "source_id": sid,
                 "excerpt": quote, "observed_fact": fact}
            if category == "problem" and x["date"]:
                e.update(publication_date=x["date"], date_excerpt=x["date_excerpt"])
            evidence.append(e)
        research = Research(company=x["company"], domain=x["domain"], identity="confirmed",
                            eligibility="unknown", eligibility_reason="The excerpt snapshot does not establish all geography and problem criteria.",
                            evidence=evidence, people=None, problem_hypothesis=x["hypothesis"],
                            cgs_relevance="A potential link to transformation strategy and capability assessment remains a hypothesis requiring verification.",
                            alternative_explanation=x["alternative"], confidence="low",
                            unanswered_questions=["Is there an unresolved problem requiring outside support?", "Is headquarters within the pilot geography?", "Who is the current accountable buyer?"],
                            conflicts=[], components={k: {"assessment": None, "reason": "Insufficient source support to assign this dimension from the short public snapshot.", "evidence_ids": []}
                                                     for k in ("problem", "capability_fit", "timing", "buyer_access")},
                            suggested_next_investigation=x["next"])
        lead = make_lead(run_id, research, sources, brief, rubric, date(2026, 10, 20))
        lead.research_method = "manual_public_web"
        store.add_lead(lead)
    store.finish(run_id, "completed", "Five manually researched public examples from 20 Oct 2026. Short excerpt snapshots; not autonomous agent results. Further research required.", token)
    return run_id
