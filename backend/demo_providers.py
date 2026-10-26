"""Explicit offline simulation. These adapters contain no network or credential access."""
from datetime import datetime, timezone
import json
from urllib.parse import urlsplit

from lead_agent.demo import fixture
from .orchestrator_service import FeedbackAnalysis

COMPANIES = {f"synthetic-factory-{n}.example": f"Synthetic Factory {n}" for n in (1, 2)}
DIRECTION_SIGNAL = "Acquisition integration planning and operating-model review"


def synthetic_fixture(number):
    today = datetime.now(timezone.utc).date()
    research, sources = fixture(today=today, number=number)
    if number == 2:
        text = (f"{today.isoformat()}: Synthetic Factory 2 announces the acquisition of an equipment business "
                "and a review of production systems integration.")
        sources[1].content = text
        event = research.evidence[-1]
        event.category, event.observed_fact, event.excerpt, event.date_excerpt = "signal", text, text, text
        research.problem_hypothesis = "The acquisition may create integration and operating-model planning questions."
        research.cgs_relevance = "Strategy and first-mile integration planning could be relevant; external advisory demand is unknown."
        research.alternative_explanation = "The internal integration team or an existing advisor may already cover this work."
        for key in ("problem", "capability_fit", "timing"):
            research.components[key].assessment = "medium"
            research.components[key].reason = "A specific synthetic acquisition event supports investigation, not proven consulting demand."
    return research, sources


class SyntheticResearchProvider:
    def __init__(self, model, trace):
        self.model, self.trace = model, trace

    def check(self):
        pass

    def search(self, query, *, start_date=None):
        host = next((host for host in COMPANIES if host in query), None)
        if host is None:
            raise ValueError("Choose a listed synthetic company for offline research.")
        self.trace.append({"kind": "search", "domain": host, "query": query})
        return {"results": [{"url": f"https://{host}/news"}], "usage": {"credits": 0}}

    def read_page(self, url):
        host = urlsplit(url).hostname
        if host not in COMPANIES:
            raise ValueError("Offline source unavailable.")
        _, sources = synthetic_fixture(int(host.split('-')[-1].split('.')[0]))
        source = sources[1] if urlsplit(url).path == "/news" else sources[0]
        self.trace.append({"kind": "extract", "url": url})
        return {"results": [{"url": url, "raw_content": source.content}], "usage": {"credits": 0}}

    def research(self, prompt, source_ids, *, quote_ids=None):
        context, _ = json.JSONDecoder().raw_decode(prompt.split("Input: ", 1)[1])
        host = context["company"]["domain"]
        research, _ = synthetic_fixture(int(host.split('-')[-1].split('.')[0]))
        record = research.model_dump(mode="json")
        aliases = {}
        for observation in record["evidence"]:
            passage = next(q for q in context["evidence_bank"] if observation["excerpt"] in q["excerpt"])
            aliases[observation.pop("evidence_id")] = passage["quote_id"]
            observation.pop("source_id")
            observation.pop("excerpt")
            observation["quote_id"] = passage["quote_id"]
        for component in record["components"].values():
            component["evidence_ids"] = [aliases.get(key, key) for key in component["evidence_ids"]]
        self.trace.append({"kind": "assessment", "domain": host, "theme_version": context["brief"]["version"],
                           "signals": context["brief"]["signals"], "synthetic": True})
        return json.dumps(record), {"synthetic_model_calls": 1}


class SyntheticFeedbackProvider:
    def check(self):
        pass

    def analyze(self, inputs):
        if inputs["brief"]["signals"] == [DIRECTION_SIGNAL]:
            return FeedbackAnalysis(summary="Simulated review: the current direction already includes the fixture signal.",
                patch_json="{}", rationale="The scripted feedback does not justify a further change.",
                feedback_ids=[f["feedback_id"] for f in inputs["feedback"]]), {"synthetic_model_calls": 1}
        return FeedbackAnalysis(summary="Simulated review: the selected feedback asks for more specific integration signals.",
            patch_json=json.dumps({"signals": [DIRECTION_SIGNAL]}),
            rationale="Synthetic feedback motivates a bounded direction proposal; a human must approve it.",
            feedback_ids=[f["feedback_id"] for f in inputs["feedback"]]), {"synthetic_model_calls": 1}
