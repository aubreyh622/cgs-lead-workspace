from datetime import date

from backend.research_service import StartResearch
from lead_agent.assessment import make_lead
from lead_agent.demo import fixture
from lead_agent.worker import new_payload


def sample(research):
    brief, rubric = research.theme()
    request = StartResearch(request_id="synthetic-sample", consultant_id="reviewer-local",
        company={"company": "Synthetic Factory 1", "domain": "synthetic-factory-1.example"})
    payload = new_payload(brief, rubric, [request.company.model_dump()], strategy="seeds",
                          model="gemini-test", limits=research.limits())
    payload["mode"] = "synthetic"
    run_id, token, _ = research.store.create_job(request, "fixture-only", payload,
        "brief-only:" + brief.brief_id + ":" + brief.version)
    r, sources = fixture(today=date.today())
    lead = make_lead(run_id, r, sources, brief, rubric, date.today(), "synthetic")
    research.store.add_lead(lead)
    research.store.finish(run_id, "completed", "Explicit synthetic test sample.", token)
    return lead
