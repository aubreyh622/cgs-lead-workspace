"""Each agent may use its own AI model (AI_MODEL_<AGENT> in .env), Claude requests cache what each step repeats, and every
request is counted by model so a quality check can price a run. Fake AI companies only: nothing reaches the internet."""
from collections import Counter
import json
import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api import llm
from api import models as m
from api import services as S
from api.check_quality import cost
from api.main import create_app
from api.settings import Settings, load_settings
from agent_fakes import turn

ME = {"X-User": "Alex Morgan"}


def test_an_agent_takes_its_own_model_from_the_env_file_and_the_others_keep_ai_model(monkeypatch):
    for key in ("AI_MODEL", "AI_MODEL_OUTREACH", "AI_MODEL_EVIDENCE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AI_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("AI_MODEL_OUTREACH", " claude-sonnet-5-5 ")
    monkeypatch.setenv("AI_MODEL_EVIDENCE", "")  # empty = AI_MODEL
    st = load_settings(env_file=None)
    assert st.agent_models == (("Outreach agent", "claude-sonnet-5-5"),)
    assert st.model_for("Outreach agent") == "claude-sonnet-5-5"
    assert st.model_for("Evidence agent") == st.model_for(None) == "claude-haiku-5-5"
    with pytest.raises(ValueError, match="No agent called 'Writer agent'"):
        Settings(agent_models=(("Writer agent", "x"),))


def test_run_agent_asks_for_the_agents_own_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    c = types.SimpleNamespace(provider_factory=None, settings=Settings(ai_provider="claude", agent_models=(("Outreach agent", "claude-sonnet-5-5"),)))
    assert S.provider(c, "Outreach agent").model == "claude-sonnet-5-5"
    assert S.provider(c, "Evidence agent").model == S.provider(c).model == "claude-haiku-5-5"


def test_a_draft_is_written_and_logged_with_the_outreach_agents_model(tmp_path, monkeypatch):
    seen = []
    good = {"subject": "Your new Ohio foundry",
            "body": "Hi Priya, I saw that [[f1|Halvorsen opened a third foundry in Ohio]]. Open to 20 minutes? Alex Morgan, CGS Advisors"}
    answers = [turn(("brief", {})), turn(("submit_draft", good))]

    def fake(url, headers, payload, **kw):
        seen.append(url)
        return answers.pop(0)
    real = llm.make_provider
    monkeypatch.setattr(S, "make_provider", lambda name, model="", **kw: real(name, model, key="k", transport=fake))
    db = tmp_path / "w.sqlite3"
    create_app(Settings(db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db, ai_provider="gemini", ai_model="base-model",
                              agent_models=(("Outreach agent", "writer-model"),)))
    r = TestClient(app).post("/api/opportunities/halvorsen/outreach/generate", json={}, headers=ME)
    assert r.status_code == 200, r.text
    assert all("models/writer-model:" in u for u in seen) and len(seen) == 2
    with app.state.db.session() as s:
        run = s.scalars(select(m.AgentRun).where(m.AgentRun.agent == "Outreach agent", m.AgentRun.opportunity_id == "halvorsen")).one()
        assert run.model == "gemini:writer-model"


def test_claude_requests_cache_the_repeated_prefix_and_count_cache_tokens():
    sent = []

    def fake(url, headers, payload, **kw):
        sent.append(payload)
        return {"content": [{"type": "tool_use", "id": "t1", "name": "finish", "input": {}}], "stop_reason": "tool_use",
                "usage": {"input_tokens": 100, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 400, "output_tokens": 50}}
    p = llm.make_provider("claude", "claude-sonnet-5-5", key="k", transport=fake)
    before = Counter(llm.METER.get(p.label, Counter()))
    t = p.chat("You are an agent.", [{"role": "user", "text": "Go."}], [{"name": "finish", "description": "Done.", "parameters": {"type": "object"}}])
    body = sent[0]
    assert body["system"] == [{"type": "text", "text": "You are an agent.", "cache_control": {"type": "ephemeral"}}]
    assert body["cache_control"] == {"type": "ephemeral"}  # the marker that moves forward with the conversation
    assert (t.usage.tokens_in, t.usage.cache_read, t.usage.cache_write, t.usage.tokens_out) == (3500, 3000, 400, 50)
    grew = Counter(llm.METER[p.label])
    grew.subtract(before)
    assert +grew == Counter({"requests": 1, "in": 3500, "cache_read": 3000, "cache_write": 400, "out": 50}) == p.used
    json.dumps(body)  # the request stays plain JSON


def test_a_run_is_priced_from_its_tokens_with_cache_reads_and_writes_at_their_own_price():
    use = {"claude:claude-sonnet-5-5": {"in": 1_000_000, "cache_read": 600_000, "cache_write": 100_000, "out": 100_000}}
    # 300K plain at $2, 100K written at $2.50, 600K read at $0.10, 100K out at $10
    assert cost(use) == pytest.approx(0.6 + 0.25 + 0.06 + 1.0)
    assert cost({"gemini:gemini-3.5-flash": {"in": 10, "out": 1}}) is None  # no price here: no guess
