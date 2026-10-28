"""Step 5 checks: the switchable AI plug and the first real agents (evidence agent, reply classifier), using a fake AI company.

No test calls a real AI service. The fake returns the same shapes Gemini and OpenAI return.
"""
import json

import pytest
from fastapi.testclient import TestClient

from api.llm import LLMError, make_provider
from api.evidence_agent import VERSION as EVIDENCE_AGENT_VERSION
from api.live_agents import check_proposals
from agent_fakes import turn
from api.main import create_app
from api.settings import Settings
from lead_agent.providers import ProviderError

ME = {"X-User": "Alex Morgan"}
NOTE = ("Maya said: Our month-end close takes twelve days because of manual reconciliations. "
        "We would rather bring in an outside partner than build this ourselves. "
        "The CFO owns the budget for this.")


class FakeAI:
    """Pretends to be Gemini. Records each request; returns the next scripted answer or raises."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, url, headers, payload, **kw):
        self.requests.append((url, headers, payload))
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]  # the last answer repeats
        if isinstance(a, Exception):
            raise a
        if isinstance(a, dict) and "candidates" in a and "usageMetadata" in a:  # already a full response (a tool-using agent's turn)
            return a
        text = a if isinstance(a, str) else json.dumps(a)
        return {"candidates": [{"content": {"parts": [{"text": text}]}}], "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 120}}


def live_app(tmp_path, fake):
    create_app(Settings(app_mode="demo", db_path=tmp_path / "live.sqlite3")).state.db.engine.dispose()  # synthetic records first
    app = create_app(Settings(app_mode="live", db_path=tmp_path / "live.sqlite3"))
    app.state.provider_factory = lambda: make_provider("gemini", "gemini-test-model", key="AIzaTESTKEY123", transport=fake)
    return TestClient(app)


def prop(kind, key, value, quote, etype="client_statement"):
    return {"kind": kind, "key": key, "value": value, "quote": quote, "why": "test", "evidence_type": etype}


# ---------- the plug ----------
def test_gemini_request_shape():
    fake = FakeAI({"label": "meet", "why": "x"})
    p = make_provider("gemini", "gemini-test-model", key="k1", transport=fake)
    data, usage = p.json("sys", "user", {"type": "object"}, "x")
    url, headers, payload = fake.requests[0]
    assert "models/gemini-test-model:generateContent" in url and headers == {"x-goog-api-key": "k1"}
    assert payload["generationConfig"]["responseMimeType"] == "application/json" and payload["generationConfig"]["responseJsonSchema"] == {"type": "object"}
    assert data == {"label": "meet", "why": "x"} and usage.tokens_in == 900


def test_openai_request_shape():
    seen = []

    def transport(url, headers, payload, **kw):
        seen.append((url, headers, payload))
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"label": "need", "why": "y"}'}]}],
                "usage": {"input_tokens": 50, "output_tokens": 5}}

    p = make_provider("openai", "gpt-test", key="sk-test", transport=transport)
    data, usage = p.json("sys", "user", {"type": "object"}, "reply_label")
    url, headers, payload = seen[0]
    assert url.endswith("/v1/responses") and headers["Authorization"] == "Bearer sk-test"
    assert payload["text"]["format"]["strict"] is True and data["label"] == "need" and usage.tokens_out == 5


def test_switching_only_needs_settings(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaX")
    assert make_provider("gemini").label == "gemini:gemini-3.5-flash-lite"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    with pytest.raises(LLMError, match="AI_MODEL"):
        make_provider("openai")  # OpenAI needs a model chosen in .env
    assert make_provider("openai", "gpt-test").label == "openai:gpt-test"
    with pytest.raises(LLMError, match="AI_PROVIDER"):
        make_provider("someother")
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        make_provider("gemini")


def test_errors_never_show_the_key():
    fake = FakeAI(ProviderError("HTTP 400: API key not valid: AIzaSECRET999", "auth", 400))
    p = make_provider("gemini", "m", key="AIzaSECRET999", transport=fake)
    with pytest.raises(LLMError) as e:
        p.json("s", "u", {}, "x")
    assert "AIzaSECRET999" not in str(e.value) and e.value.kind == "auth"


# ---------- local checks on what the model proposes ----------
def test_checks_keep_only_valid_grounded_proposals():
    raw = [
        prop("cond", "problem", "true", "Our month-end close takes twelve days because of manual reconciliations."),
        prop("score", "N", "3", "Our month-end close takes twelve days because of manual reconciliations."),
        prop("cond", "external", "true", "We would rather bring in an outside partner than build this ourselves."),
        prop("score", "B", "2", "The CFO owns the budget for this."),
        prop("score", "A", "4", "The CEO signed the contract yesterday."),            # invented quote
        prop("cond", "fit", "true", "The CFO owns the budget for this.", "inference"),  # inference cannot meet a condition
        prop("score", "T", "7", "The CFO owns the budget for this."),                   # invalid score
        prop("score", "B", "2.5", "The CFO owns the budget for this."),                 # 2.5 is only for History
        prop("score", "N", "4", "Our month-end close takes twelve days because of manual reconciliations."),  # duplicate key
        prop("blocker", "SERVICE_MISMATCH", "SERVICE_MISMATCH", "words that are nowhere"),
    ]
    kept, dropped = check_proposals(raw, NOTE)
    assert [(p["kind"], p["key"], p["value"]) for p in kept] == [("cond", "problem", True), ("score", "N", 3), ("cond", "external", True), ("score", "B", 2)]
    assert dropped == {"quote_not_found": 2, "inference_condition": 1, "invalid": 2, "duplicate": 1}


# ---------- the agents inside the workflow ----------
def test_live_evidence_agent_end_to_end(tmp_path):
    good = {**prop("cond", "problem", "true", "Our month-end close takes twelve days because of manual reconciliations."), "source": "notes"}
    invented = {**prop("score", "A", "4", "The CEO signed the contract yesterday."), "source": "notes"}
    fake = FakeAI(turn(("notes", {}), ("assessment", {})), turn(("propose", good), ("propose", invented)), turn(("finish", {"summary": "One item."})))
    api = live_app(tmp_path, fake)
    r = api.post("/api/opportunities/alder/notes", json={"text": NOTE}, headers=ME)
    assert r.status_code == 200, r.text
    assert r.json()["proposals"] == 1 and r.json()["droppedForInvalidQuote"] == 1
    o = api.get("/api/opportunities/alder").json()
    pending = [p for p in o["proposals"] if p["status"] == "pending"]
    assert pending[0]["key"] == "problem" and pending[0]["evidenceType"] == "client_statement"
    run = api.get("/api/agent-runs").json()[0]
    assert run["model"] == "gemini:gemini-test-model" and run["status"] == "completed" and run["prompt"] == EVIDENCE_AGENT_VERSION
    assert run["calls"] == 3 and run["steps"]
    # the agent read the notes through its tool, as data, with the rubric in the instructions; the invented quote went back to it
    _, _, payload = fake.requests[1]
    assert "Unknown is not zero" in payload["systemInstruction"]["parts"][0]["text"]
    assert "twelve days" in json.dumps(payload["contents"][2])
    _, _, payload = fake.requests[2]
    assert "not in those notes word for word" in json.dumps(payload["contents"][-1])


def test_live_failure_keeps_the_note_and_shows_the_error(tmp_path):
    api = live_app(tmp_path, FakeAI(ProviderError("HTTP 429: quota", "quota", 429)))
    before = len(api.get("/api/opportunities/alder").json()["notes"])
    r = api.post("/api/opportunities/alder/notes", json={"text": NOTE}, headers=ME)
    assert r.status_code == 503 and r.json()["code"] == "ai_quota"
    o = api.get("/api/opportunities/alder").json()
    assert len(o["notes"]) == before + 1 and not any(p["status"] == "pending" for p in o["proposals"])
    assert api.get("/api/agent-runs").json()[0]["status"] == "failed"


def test_live_invalid_output_is_an_error_not_a_guess(tmp_path):
    api = live_app(tmp_path, FakeAI("this is not json"))  # an agent that never uses its tools stops after its step limit
    r = api.post("/api/opportunities/alder/notes", json={"text": NOTE}, headers=ME)
    assert r.status_code == 503 and r.json()["code"] == "ai_invalid_output"


def test_live_reply_label_and_its_failure(tmp_path):
    api = live_app(tmp_path, FakeAI({"label": "decline", "why": "not considering outside help"}, ProviderError("down", "temporary")))
    r = api.post("/api/opportunities/bluegate/outreach/reply", json={"text": "Thanks, but we are not considering outside help."}, headers=ME)
    assert r.json()["replyLabel"] == "decline" and api.get("/api/opportunities/bluegate").json()["stage"] == "active"
    r = api.post("/api/opportunities/summit/outreach/reply", json={"text": "Let's talk next week."}, headers=ME)
    assert r.json()["replyLabel"] == "pending"  # the failure is shown; the reply still makes it Active
    assert api.get("/api/opportunities/summit").json()["stage"] == "active"


def test_missing_key_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    create_app(Settings(app_mode="demo", db_path=tmp_path / "k.sqlite3")).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=tmp_path / "k.sqlite3"))
    r = TestClient(app).post("/api/opportunities/alder/notes", json={"text": NOTE}, headers=ME)
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["error"]
