"""The ADK runtime (api/adk_runtime.py) against the hand-written loop: for every edge case of the loop, both must send the
AI company exactly the same requests and end with the same outcome and trace. Offline: fake transports play the models."""
import asyncio
import copy
import threading
import time

import pytest

pytest.importorskip("google.adk")

from api import adk_runtime as A  # noqa: E402
from api import agent_runtime as R  # noqa: E402
from api.llm import LLMError, make_provider  # noqa: E402


class Script:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, url, headers, payload, **kw):
        self.requests.append(copy.deepcopy(payload))
        return copy.deepcopy(self.responses.pop(0))


def gemini(*parts, tokens=7):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}}],
            "usageMetadata": {"promptTokenCount": tokens, "candidatesTokenCount": 2}}


def call(name, args, **extra):
    return {"functionCall": {"name": name, "args": args, **extra}}


def text(t):
    return {"text": t}


def claude(*blocks, stop="tool_use"):
    return {"content": list(blocks), "stop_reason": stop, "usage": {"input_tokens": 5, "output_tokens": 1}}


def use(i, name, args):
    return {"type": "tool_use", "id": f"tu{i}", "name": name, "input": args}


CUT = {"content": [], "stop_reason": "max_tokens", "usage": {"input_tokens": 5, "output_tokens": 4096}}
ECHO = R.Tool("echo", "Repeat a word.", R.params({"word": {"type": "string"}}), lambda a: {"echo": a["word"]})


def spec(finish_check=lambda a: a, max_steps=4, max_tool_calls=16, tools=(ECHO,)):
    return R.AgentSpec(name="Test agent", version="t1", system="Test agent. {not_a_placeholder}", tools=list(tools),
                       finish=R.Tool("finish", "Answer.", R.params({"answer": {"type": "string"}}), finish_check),
                       max_steps=max_steps, max_tool_calls=max_tool_calls)


def both(company, responses, make_spec, may_call_n=None):
    """Run the same script on both runtimes. Returns (legacy, adk) as (outcome or exception, requests)."""
    out = []
    for runner in (R.run_legacy, A.run):
        ai = Script(*responses)
        left = {"n": may_call_n}

        def may_call():
            if left["n"] is None:
                return True
            left["n"] -= 1
            return left["n"] >= 0
        try:
            result = runner(make_spec(), make_provider(company, "m", key="k", transport=ai), "Do the task.", may_call=may_call)
        except Exception as e:  # noqa: BLE001 - compared below
            result = e
        out.append((result, ai.requests))
    return out


def same(company, responses, make_spec=spec, may_call_n=None):
    (legacy, lreq), (adk, areq) = both(company, responses, make_spec, may_call_n)
    assert areq == lreq, "the AI company must get exactly the same requests"
    if isinstance(legacy, Exception):
        assert type(adk) is type(legacy) and str(adk) == str(legacy)
        return legacy
    assert (adk.status, adk.result, adk.reason) == (legacy.status, legacy.result, legacy.reason)
    assert adk.trace == legacy.trace
    assert (adk.usage.tokens_in, adk.usage.tokens_out) == (legacy.usage.tokens_in, legacy.usage.tokens_out)
    return legacy


def test_runtime_choice(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME", "legacy")
    assert R.runtime() == "legacy"
    monkeypatch.setenv("AGENT_RUNTIME", "adk")
    assert R.runtime() == "adk"
    monkeypatch.setenv("AGENT_RUNTIME", "auto")
    assert R.runtime() == "adk"  # google-adk is installed (this file is skipped otherwise)
    monkeypatch.setenv("AGENT_RUNTIME", "langchain")
    with pytest.raises(LLMError):
        R.runtime()


def test_run_goes_through_adk_when_chosen(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME", "adk")
    used = []
    real = A.run
    monkeypatch.setattr(A, "run", lambda *a, **k: used.append(1) or real(*a, **k))
    ai = Script(gemini(call("finish", {"answer": "ok"})))
    assert R.run(spec(), make_provider("gemini", "m", key="k", transport=ai), "x").status == "done" and used


def test_tool_then_answer_with_thought_signature():
    signed = call("echo", {"word": "hi"}, id="c1")
    signed["thoughtSignature"] = "sig-abc"
    out = same("gemini", [gemini(signed), gemini(call("finish", {"answer": "done"}))])
    assert out.status == "done"


def test_rejected_answer_goes_back_then_accepted():
    def check(a):
        if a["answer"] == "bad":
            raise R.ToolError("Not good enough.")
        return a
    out = same("gemini", [gemini(call("finish", {"answer": "bad"})), gemini(call("finish", {"answer": "good"}))],
               lambda: spec(check))
    assert out.result == {"answer": "good"}


def test_text_only_turn_gets_the_nudge():
    same("gemini", [gemini(text("Thinking aloud.")), gemini(call("finish", {"answer": "ok"}))])


def test_cut_off_once_then_answer_and_twice_stops():
    same("claude", [CUT, claude(use(1, "finish", {"answer": "short"}))])
    out = same("claude", [CUT, CUT])
    assert out.status == "incomplete" and "cut off twice" in out.reason


def test_step_limit_and_last_step_note():
    out = same("gemini", [gemini(call("echo", {"word": str(i)})) for i in range(4)], lambda: spec(max_steps=4))
    assert out.status == "incomplete" and "4 steps" in out.reason


def test_tool_call_limit_unknown_tool_and_parallel_calls():
    out = same("gemini", [gemini(call("echo", {"word": "a"}), call("nope", {"x": 1}), call("echo", {"word": "b"})),
                          gemini(call("echo", {"word": "c"})), gemini(call("finish", {"answer": "ok"}))],
               lambda: spec(max_tool_calls=2))
    assert out.status == "done"


def test_answer_mid_turn_stops_before_later_calls():
    seen = []
    tool = R.Tool("echo", "Repeat a word.", R.params({"word": {"type": "string"}}), lambda a: seen.append(a["word"]) or {"echo": a["word"]})
    same("gemini", [gemini(call("echo", {"word": "a"}), call("finish", {"answer": "ok"}), call("echo", {"word": "never"}))],
         lambda: spec(tools=(tool,)))
    assert seen == ["a", "a"]  # once per runtime; "never" ran on neither


def test_budget_stops_before_a_request():
    out = same("gemini", [gemini(call("echo", {"word": "a"})), gemini(call("finish", {"answer": "ok"}))], may_call_n=1)
    assert out.status == "incomplete" and "budget" in out.reason


def test_outside_failures_tolerated_twice_then_raised():
    def flaky(_a):
        raise LLMError("search timed out", "temporary")
    tool = R.Tool("echo", "Search.", R.params({"word": {"type": "string"}}), flaky)
    out = same("gemini", [gemini(call("echo", {"word": "a"})), gemini(call("echo", {"word": "b"})), gemini(call("echo", {"word": "c"}))],
               lambda: spec(tools=(tool,)))
    assert isinstance(out, LLMError)


def test_wrong_key_failure_ends_the_run_at_once():
    def broken(_a):
        raise LLMError("bad key", "auth")
    out = same("gemini", [gemini(call("echo", {"word": "a"}))], lambda: spec(tools=(R.Tool("echo", "x", R.params({"word": {"type": "string"}}), broken),)))
    assert isinstance(out, LLMError) and out.kind == "auth"


def test_bad_arguments_null_arguments_and_leaked_parameters():
    def strict(a):
        if "word" not in a:
            raise KeyError("word")
        return {"echo": a["word"], "extra": a.get("extra")}
    tool = R.Tool("echo", "Repeat.", R.params({"word": {"type": "string"}, "extra": {"type": "string"}}, ["word"]), strict)
    same("claude", [claude(use(1, "echo", {"word": None})),
                    claude(use(2, "echo", {"word": 'hi<parameter name="extra">more</parameter>'})),
                    claude(use(3, "finish", {"answer": "ok"}))], lambda: spec(tools=(tool,)))


def test_long_results_are_shortened_the_same_way():
    big = R.Tool("echo", "Big page.", R.params({"word": {"type": "string"}}), lambda a: {"text": "x" * 20000, "items": list(range(50))})
    same("gemini", [gemini(call("echo", {"word": "a"})), gemini(call("finish", {"answer": "ok"}))], lambda: spec(tools=(big,)))


def test_model_failure_is_raised_unchanged():
    def fail(url, headers, payload, **kw):
        from lead_agent.providers import ProviderError
        raise ProviderError("rate limited", "quota")
    with pytest.raises(LLMError) as e:
        A.run(spec(), make_provider("gemini", "m", key="k", transport=fail), "x")
    assert e.value.kind == "quota"


def test_tools_of_one_turn_never_overlap():
    spans = []

    def slow(a):
        start = time.perf_counter()
        time.sleep(0.05)
        spans.append((start, time.perf_counter(), threading.get_ident()))
        return {"ok": a["word"]}
    tool = R.Tool("echo", "Slow.", R.params({"word": {"type": "string"}}), slow)
    ai = Script(gemini(*[call("echo", {"word": str(i)}) for i in range(3)]), gemini(call("finish", {"answer": "ok"})))
    A.run(spec(tools=(tool,)), make_provider("gemini", "m", key="k", transport=ai), "x")
    spans.sort()
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:])) and {s[2] for s in spans} == {threading.get_ident()}


def test_works_when_called_inside_an_event_loop():
    async def inside():
        ai = Script(gemini(call("finish", {"answer": "ok"})))
        return A.run(spec(), make_provider("gemini", "m", key="k", transport=ai), "x")
    assert asyncio.run(inside()).status == "done"


def test_agent_names_are_identifiers():
    assert A.agent_name("Evidence agent") == "evidence_agent" and A.agent_name("9 lives") == "agent_9_lives"


def test_config_says_which_runtime_runs_the_agents(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from api.main import create_app
    from api.settings import Settings
    api = TestClient(create_app(Settings(app_mode="demo", db_path=tmp_path / "ws.sqlite3")))
    for name in ("adk", "legacy"):
        monkeypatch.setenv("AGENT_RUNTIME", name)
        assert api.get("/api/config").json()["agentRuntime"] == name
    monkeypatch.setenv("AGENT_RUNTIME", "other")
    assert api.get("/api/config").json()["agentRuntime"].startswith("not available")


# ---------- single-step agents: one answer in a fixed JSON shape (R.ask) ----------
LABEL_SCHEMA = {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"], "additionalProperties": False}


def json_answer(company, text):
    if company == "claude":
        return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "usage": {"input_tokens": 9, "output_tokens": 3}}
    return {"candidates": [{"content": {"parts": [{"text": text}]}}], "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 3}}


@pytest.mark.parametrize("company", ["gemini", "claude"])
def test_single_step_answer_is_the_same_on_both_runtimes(company):
    out = []
    for ask in (lambda p, *a: p.json(*a), A.run_json):
        ai = Script(json_answer(company, '{"label": "meet"}'))
        data, usage = ask(make_provider(company, "m", key="k", transport=ai), "Label the reply. {braces}", '{"reply": "Happy to talk"}',
                          LABEL_SCHEMA, "reply_label")
        out.append((data, usage.tokens_in, usage.tokens_out, ai.requests))
    assert out[0] == out[1] and out[1][0] == {"label": "meet"}


def test_single_step_failures_are_raised_unchanged():
    for text, kind in (("not json", "invalid_output"),):
        ai = Script(json_answer("gemini", text))
        with pytest.raises(LLMError) as e:
            A.run_json(make_provider("gemini", "m", key="k", transport=ai), "s", "u", LABEL_SCHEMA, "reply_label")
        assert e.value.kind == kind
    cut = {"content": [], "stop_reason": "max_tokens", "usage": {"input_tokens": 1, "output_tokens": 1}}
    with pytest.raises(LLMError, match="cut off"):
        A.run_json(make_provider("claude", "m", key="k", transport=Script(cut)), "s", "u", LABEL_SCHEMA, "reply_label")


def test_ask_goes_through_adk_when_chosen(monkeypatch):
    used = []
    real = A.run_json
    monkeypatch.setattr(A, "run_json", lambda *a: used.append(a[-1]) or real(*a))
    p = make_provider("gemini", "m", key="k", transport=Script(json_answer("gemini", '{"label": "x"}')))
    monkeypatch.setenv("AGENT_RUNTIME", "adk")
    assert R.ask(p, "s", "u", LABEL_SCHEMA, "reply_label")[0] == {"label": "x"} and used == ["reply_label"]
    monkeypatch.setenv("AGENT_RUNTIME", "legacy")
    p2 = make_provider("gemini", "m", key="k", transport=Script(json_answer("gemini", '{"label": "y"}')))
    assert R.ask(p2, "s", "u", LABEL_SCHEMA, "reply_label")[0] == {"label": "y"} and used == ["reply_label"]


def test_single_step_inside_an_event_loop():
    async def inside():
        p = make_provider("gemini", "m", key="k", transport=Script(json_answer("gemini", '{"label": "z"}')))
        return A.run_json(p, "s", "u", LABEL_SCHEMA, "reply_label")
    assert asyncio.run(inside())[0] == {"label": "z"}
