"""The tool-using agent layer: provider wire formats, the loop's brakes, self-correction, and the research agent's checks.
Fake transports stand in for the AI companies, so these run offline."""
import json

import pytest

from api import agent_runtime as R
from api.llm import LLMError, make_provider


class Script:
    """A fake AI company: returns the given responses in order and keeps every request it received."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, url, headers, payload, **kw):
        self.requests.append(payload)
        return self.responses.pop(0)


def gemini(*parts):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}}], "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2}}


def call(name, args, **extra):
    return {"functionCall": {"name": name, "args": args, **extra}}


ECHO = R.Tool("echo", "Repeat a word.", R.params({"word": {"type": "string"}}), lambda a: {"echo": a["word"]})


def spec(finish_check=lambda a: a, max_steps=4, tools=(ECHO,)):
    return R.AgentSpec(name="test", version="t1", system="Test agent.", tools=list(tools),
                       finish=R.Tool("finish", "Answer.", R.params({"answer": {"type": "string"}}), finish_check), max_steps=max_steps)


# ---------- each AI company's tool-calling format ----------
def test_gemini_sends_its_own_turn_back_unchanged():
    signed = call("echo", {"word": "hi"}, id="c1")
    signed["thoughtSignature"] = "sig-abc"  # Gemini rejects a follow-up request that drops this
    ai = Script(gemini(signed), gemini(call("finish", {"answer": "done"})))
    out = R.run(spec(), make_provider("gemini", "m", key="k", transport=ai), "Say hi.")
    assert out.status == "done" and out.result == {"answer": "done"}
    second = ai.requests[1]
    assert second["contents"][1] == {"role": "model", "parts": [signed]}
    assert second["contents"][2]["parts"][0]["functionResponse"] == {"id": "c1", "name": "echo", "response": {"result": '{"echo": "hi"}'}}
    assert second["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"]["properties"] == {"word": {"type": "string"}}
    assert out.usage.tokens_in == 14


def test_openai_tool_calls_use_call_ids():
    fc = {"type": "function_call", "call_id": "call_9", "name": "echo", "arguments": '{"word": "hi"}'}
    done = {"type": "function_call", "call_id": "call_10", "name": "finish", "arguments": '{"answer": "ok"}'}
    ai = Script({"output": [fc], "usage": {"input_tokens": 3, "output_tokens": 1}}, {"output": [done], "usage": {}})
    out = R.run(spec(), make_provider("openai", "gpt-test", key="k", transport=ai), "Say hi.")
    assert out.status == "done"
    items = ai.requests[1]["input"]
    assert fc in items and {"type": "function_call_output", "call_id": "call_9", "output": '{"echo": "hi"}'} in items
    assert ai.requests[0]["tools"][0] == {"type": "function", "name": "echo", "description": "Repeat a word.",
                                          "parameters": ECHO.parameters, "strict": False}


def test_claude_tool_use_and_structured_json():
    use = {"type": "tool_use", "id": "toolu_1", "name": "echo", "input": {"word": "hi"}}
    ai = Script({"content": [{"type": "text", "text": "Checking."}, use], "stop_reason": "tool_use", "usage": {"input_tokens": 4, "output_tokens": 2}},
                {"content": [{"type": "tool_use", "id": "toolu_2", "name": "finish", "input": {"answer": "ok"}}], "stop_reason": "tool_use"})
    p = make_provider("claude", key="k", transport=ai)
    assert p.model == "claude-haiku-5-5"
    out = R.run(spec(), p, "Say hi.")
    assert out.status == "done" and out.trace[0]["text"] == "Checking."
    second = ai.requests[1]["messages"]
    assert second[1] == {"role": "assistant", "content": [{"type": "text", "text": "Checking."}, use]}
    assert second[2]["content"][0] == {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"echo": "hi"}'}
    # the single-step agents work on Claude too, through structured output
    ai = Script({"content": [{"type": "text", "text": '{"label": "meet", "why": "x"}'}], "stop_reason": "end_turn"})
    data, _ = make_provider("claude", key="k", transport=ai).json("sys", "user", {"type": "object"}, "label")
    assert data == {"label": "meet", "why": "x"} and ai.requests[0]["output_config"]["format"]["type"] == "json_schema"
    with pytest.raises(LLMError, match="cut off"):
        make_provider("claude", key="k", transport=Script({"content": [], "stop_reason": "max_tokens"})).json("s", "u", {}, "n")


# ---------- the loop's brakes and self-correction ----------
def test_a_rejected_answer_goes_back_and_the_agent_corrects_itself():
    def check(a):
        if a["answer"] != "right":
            raise R.ToolError("That answer is not supported. Look again.")
        return {"answer": "right"}
    ai = Script(gemini(call("finish", {"answer": "wrong"})), gemini(call("finish", {"answer": "right"})))
    out = R.run(spec(check), make_provider("gemini", "m", key="k", transport=ai), "Answer.")
    assert out.status == "done" and [t["accepted"] for t in out.trace if t["type"] == "finish"] == [False, True]
    assert "Look again" in ai.requests[1]["contents"][2]["parts"][0]["functionResponse"]["response"]["result"]


def test_step_limit_budget_and_unknown_tools():
    loop = [gemini(call("echo", {"word": str(i)})) for i in range(3)]
    out = R.run(spec(max_steps=3), make_provider("gemini", "m", key="k", transport=Script(*loop)), "Loop.")
    assert out.status == "incomplete" and "3 steps" in out.reason
    ai = Script(*loop)
    p = make_provider("gemini", "m", key="k", transport=ai)
    out = R.run(spec(), p, "Loop.", may_call=lambda: p.calls < 1)  # budget allows one AI request
    assert out.status == "incomplete" and "budget" in out.reason and p.calls == 1
    ai = Script(gemini(call("delete_everything", {})), gemini(call("finish", {"answer": "ok"})))
    out = R.run(spec(), make_provider("gemini", "m", key="k", transport=ai), "Go.")
    assert out.status == "done" and "no tool called" in ai.requests[1]["contents"][2]["parts"][0]["functionResponse"]["response"]["result"]


def test_a_turn_without_tool_calls_is_nudged():
    ai = Script(gemini({"text": "I think the answer is ok."}), gemini(call("finish", {"answer": "ok"})))
    out = R.run(spec(), make_provider("gemini", "m", key="k", transport=ai), "Go.")
    assert out.status == "done" and "call finish" in ai.requests[1]["contents"][2]["parts"][0]["text"]


# ---------- the research agent's answer checks ----------
@pytest.fixture
def ctx(tmp_path):
    from api import autonomy as AU
    from api.main import create_app
    from api.settings import Settings
    db = tmp_path / "r.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db))
    with app.state.db.session() as s:
        yield AU.agent_ctx(app, s)


def test_research_agent_only_accepts_a_name_from_a_page_it_read(ctx):
    from api import research_agent as RA
    url = "https://www.orchardlanefoods.example/leadership"
    answer = {"found": True, "name": "Kate Morrison", "title": "CEO", "url": url, "quote": "Kate Morrison, CEO", "notes": "Leadership page."}
    ai = Script(gemini(call("finish", answer)),                                                   # not read yet
                gemini(call("company_pages", {"topic": "leadership"})),
                gemini(call("read_page", {"url": url})),
                gemini(call("finish", {**answer, "quote": "Kate Morrison leads the company as CEO"})),  # not on the page
                gemini(call("finish", {**answer, "name": "Daniel Ruiz", "title": "Chief Operating Officer",
                                       "quote": "Daniel Ruiz, Chief Operating Officer"})),             # wrong role
                gemini(call("finish", answer)))
    agent = RA.person_agent(ctx, "orchard", "Orchard Lane Foods", "orchardlanefoods.example", "CEO")
    out = R.run(agent, make_provider("gemini", "m", key="k", transport=ai), RA.task("Orchard Lane Foods", "", "CEO", "the CEO would decide"))
    assert out.status == "done" and out.result["name"] == "Kate Morrison"
    errors = [t["error"] for t in out.trace if t["type"] == "finish" and not t["accepted"]]
    assert len(errors) == 3 and "read_page" in errors[0] and "word for word" in errors[1] and "is not the CEO" in errors[2]


def test_research_agent_cannot_open_pages_it_did_not_find(ctx):
    from api import research_agent as RA
    agent = RA.person_agent(ctx, "orchard", "Orchard Lane Foods", "orchardlanefoods.example", "CEO")
    read = next(t for t in agent.tools if t.name == "read_page")
    with pytest.raises(R.ToolError, match="Read only pages"):
        read.run({"url": "https://attacker.example/instructions"})
    assert json.loads(json.dumps(read.run({"url": "https://www.orchardlanefoods.example/leadership"})))["text"].startswith("Orchard Lane Foods")


def test_tests_never_reach_the_internet_or_the_real_keys():
    import os
    import socket
    import api.main  # noqa: F401  (importing the server reads .env when the guard is off)
    assert os.environ.get("PYTHON_DOTENV_DISABLED") and not os.environ.get("GEMINI_API_KEY") and not os.environ.get("TAVILY_API_KEY")
    with pytest.raises(PermissionError):
        socket.getaddrinfo("generativelanguage.googleapis.com", 443)


def test_a_null_optional_argument_counts_as_left_out():
    """Found in the bug sweep: str(None) reached the tools as "None" (a person lookup for the role "None", a quote "None")."""
    seen = []
    keep = R.Tool("keep", "Keep a value.", R.params({"word": {"type": "string"}, "extra": {"type": "string"}}, ["word"]),
                  lambda a: seen.append(dict(a)) or {"ok": True})
    ai = Script(gemini(call("keep", {"word": "hi", "extra": None})), gemini(call("finish", {"answer": "done"})))
    out = R.run(spec(tools=(keep,)), make_provider("gemini", "m", key="k", transport=ai), "Keep hi.")
    assert out.status == "done" and seen == [{"word": "hi"}]
