import json
import threading
import time
from datetime import datetime, timezone
from lead_agent.demo import fixture


class PreparedProvider:
    gemini_key = "fixture-gemini-secret"
    tavily_key = "fixture-tavily-secret"

    def __init__(self, model="gemini-test", *, blocked=False, failure=None):
        self.model = model
        self.entered = threading.Event()
        self.release = threading.Event()
        self.blocked = blocked
        self.failure = failure
        self.calls = {"search": 0, "extract": 0, "model": 0}

    def check(self):
        pass

    def search(self, query, *, start_date=None):
        self.calls["search"] += 1
        self.entered.set()
        if self.blocked:
            assert self.release.wait(5), "Test did not release the blocked provider."
        if self.failure:
            raise self.failure
        return {"results": [{"url": "https://synthetic-factory-1.example/news"}],
                "usage": {"credits": 0.2}}

    def read_page(self, url):
        self.calls["extract"] += 1
        _, sources = fixture(today=datetime.now(timezone.utc).date())
        source = sources[1] if url.endswith("/news") else sources[0]
        return {"results": [{"url": url, "raw_content": source.content}], "usage": {"credits": 0.2}}

    def research(self, prompt, source_ids, *, quote_ids=None):
        self.calls["model"] += 1
        context, _ = json.JSONDecoder().raw_decode(prompt.split("Input: ", 1)[1])
        research, _ = fixture(today=datetime.now(timezone.utc).date())
        record = research.model_dump(mode="json")
        aliases = {}
        for observation in record["evidence"]:
            passage = next(q for q in context["evidence_bank"] if observation["excerpt"] in q["excerpt"])
            aliases[observation.pop("evidence_id")] = passage["quote_id"]
            observation.pop("excerpt")
            observation.pop("source_id")
            observation["quote_id"] = passage["quote_id"]
        for component in record["components"].values():
            component["evidence_ids"] = [aliases.get(x, x) for x in component["evidence_ids"]]
        return json.dumps(record), {"promptTokenCount": 10, "candidatesTokenCount": 5}



def wait_terminal(service, run_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = service.status(run_id)
        if result["status"] not in ("pending", "running", "stopping"):
            return result
        time.sleep(0.01)
    raise AssertionError("Research task did not finish.")
