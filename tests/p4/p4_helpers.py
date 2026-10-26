import json
import threading
import time

from backend.orchestrator_service import FeedbackAnalysis


class ControlledAnalysis:
    gemini_key = "controlled-gemini-secret"

    def __init__(self, *, blocked=False, failure=None, patch=None):
        self.calls, self.inputs = 0, None
        self.blocked, self.failure, self.patch = blocked, failure, patch
        self.entered, self.release = threading.Event(), threading.Event()

    def check(self):
        pass

    def analyze(self, inputs):
        self.calls += 1
        self.inputs = inputs
        self.entered.set()
        if self.blocked:
            assert self.release.wait(5)
        if self.failure:
            raise self.failure
        patch = self.patch if self.patch is not None else {"signals": ["Specific integration planning signals"]}
        return FeedbackAnalysis(summary="Reviewers requested more specific signals.", patch_json=json.dumps(patch),
            rationale="A focused research signal follows the selected feedback.",
            feedback_ids=[f["feedback_id"] for f in inputs["feedback"]]), {"promptTokenCount": 10}


def feedback(env, scope="theme", consultant="consultant-a", request_suffix="1", minutes=None):
    opportunities, lead = env[2], env[7]
    record, _ = opportunities.create({"request_id": "op-"+request_suffix, "run_id": lead.run_id,
        "lead_id": lead.lead_id, "title": "Integration planning", "team": None,
        "problem_hypothesis": lead.research.problem_hypothesis,
        "cgs_relevance_hypothesis": lead.research.cgs_relevance,
        "alternative_explanation": lead.research.alternative_explanation, "evidence_ids": ["problem"]})
    key = record["opportunity"]["opportunity_id"]
    reviewed, _ = opportunities.review(key, {"request_id": "fb-"+request_suffix, "revision": 1,
        "consultant_id": consultant, "outcome": "research_further", "reason_code": "evidence_gap",
        "scope": scope, "comment": "Look for specific integration planning events.", "review_minutes": minutes})
    return reviewed["latest_feedback"]["feedback"]


def wait_review(manager, review_id):
    deadline = time.monotonic()+5
    while time.monotonic()<deadline:
        review = manager.get_review(review_id)
        if review["status"] != "running":
            return review
        time.sleep(.01)
    raise AssertionError("Feedback review did not finish.")
