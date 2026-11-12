"""The tool-using agents on Google's Agent Development Kit (ADK): the same AgentSpec, run by an ADK Runner.

    task --> Runner(LlmAgent) --(model turn)--> ProviderLlm --> our provider (api/llm.py) --> the AI company
                   |   ^
                   v   |  tool calls dispatched by ADK to SpecTool, which runs our Tool and its checks
                session history, events, callbacks (budget and step limit before every model request)

ADK owns the run: the Runner, the LlmAgent, the session that holds the conversation, the event stream, tool dispatch and
the before-model callback. The model is our own provider wrapped as an ADK model (ProviderLlm), so every AI company sees
exactly the bytes the hand-written loop sends: the same system prompt and tool declarations, the same history (Gemini
thought signatures and Claude content blocks go back unchanged), Claude prompt caching, the error kinds and the usage
meter. What the loop in api/agent_runtime.py guarantees is kept: the finish tool checks the answer and a rejection goes
back to the model; the step, tool-call and budget brakes; a cut-off turn is never used; outside failures are tolerated
twice; repair_args; results shortened by fit(); the same trace. tests/v2/test_agent_runtime.py runs on both runtimes.

One ADK rule shapes the loop: a model turn without a tool call ends an ADK invocation. The legacy loop answers such a
turn (and a cut-off one) with a note and goes on; here that note is the next user message in the same session.

Single-step agents (one answer in a fixed JSON shape) run as an LlmAgent without tools: run_json.

Chosen with AGENT_RUNTIME (api/agent_runtime.py). Measured against the legacy loop with agents-cli: docs/ADK_EVAL.md.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
from typing import Any, AsyncGenerator, Callable

from google.adk.agents import LlmAgent
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.run_config import RunConfig
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.tools.base_tool import BaseTool
from google.genai import types
from pydantic import PrivateAttr

from api import agent_runtime as R
from api.llm import LLMError, Usage

APP = "cgs_workspace"
USER = "workspace"
UNKNOWN = "unknown_tool"   # a call to a tool the agent does not have is routed here, so the model hears why
VALUE = "__value__"        # ADK tool results are dicts; a result that is not one travels under this key


def agent_name(name: str) -> str:
    """ADK names must be identifiers: "Evidence agent" -> "evidence_agent"."""
    out = re.sub(r"\W+", "_", name.strip().lower()).strip("_")
    return out if out and not out[0].isdigit() else f"agent_{out}"


class _Run:
    """Everything one agent run keeps: the conversation in our provider's format, the counters and the trace."""

    def __init__(self, spec: R.AgentSpec, provider, may_call: Callable[[], bool]):
        self.spec, self.provider, self.may_call = spec, provider, may_call
        self.tools = {t.name: t for t in spec.tools + [spec.finish]}
        self.declarations = [t.declaration() for t in self.tools.values()]
        self.messages: list[dict] = []
        self.consumed = 0                  # contents of the ADK request already turned into self.messages
        self.calls: dict[str, tuple] = {}  # ADK function call id -> (our ToolCall, step)
        self.trace: list[dict] = []
        self.tokens_in = self.tokens_out = 0
        self.steps = self.tool_calls = self.tool_failures = self.cut_offs = 0
        self.last = ""                     # how the last model turn ended: calls | text | cut_off
        self.done, self.result, self.reason = False, None, ""
        self.error: BaseException | None = None

    # ----- the model's view of the conversation -----
    def take_in(self, contents: list[types.Content]) -> None:
        """Add to our conversation what ADK appended since the last model turn: tool results and user messages.
        Model turns are skipped: our own Turn objects are already in self.messages, raw and unchanged."""
        for content in contents[self.consumed:]:
            if content.role == "model":
                continue
            responses = [p.function_response for p in content.parts or [] if p.function_response]
            if responses:
                self._results(responses)
            for p in content.parts or []:
                if p.text and not p.function_response:
                    self.messages.append({"role": "user", "text": p.text})
        self.consumed = len(contents)

    def _results(self, responses) -> None:
        rows = []
        for fr in responses:
            if fr.id not in self.calls:
                continue
            call, step = self.calls[fr.id]
            content = fr.response or {}
            if isinstance(content, dict) and set(content) == {VALUE}:
                content = content[VALUE]
            rows.append((list(self.calls).index(fr.id), step, (call, R.fit(content), isinstance(content, dict) and "error" in content)))
        if not rows:
            return
        rows.sort(key=lambda x: x[0])  # the order the model asked for them, as the legacy loop sends them
        step = rows[0][1]
        last = step == self.spec.max_steps - 1
        self.messages.append({"role": "tool", "results": [x[2] for x in rows],
                              **({"note": f"This is your last step. Call {self.spec.finish.name} now with what you have."} if last else {})})

    # ----- brakes, before every model request -----
    async def before_model(self, callback_context, llm_request) -> LlmResponse | None:
        if self.done:
            return _empty()
        if self.steps >= self.spec.max_steps:
            self.reason = f"Stopped after {self.spec.max_steps} steps without an accepted answer."
            return _empty()
        if not self.may_call():
            self.reason = "The daily AI budget is used up."
            return _empty()
        return None

    def outcome(self) -> R.Outcome:
        if self.done:
            return R.Outcome("done", self.result, "", self.trace, Usage(self.tokens_in, self.tokens_out))
        reason = self.reason or f"Stopped after {self.spec.max_steps} steps without an accepted answer."
        self.trace.append({"type": "stop", "reason": reason})
        return R.Outcome("incomplete", None, reason, self.trace, Usage(self.tokens_in, self.tokens_out))


def _empty() -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text="")]))


class ProviderLlm(BaseLlm):
    """Our provider (api/llm.py) as an ADK model. The conversation is kept in our neutral format, so the AI company gets
    the same request the legacy loop would send; the answer comes back to ADK as function calls and text."""
    _run: _Run = PrivateAttr()

    def bind(self, run: _Run) -> "ProviderLlm":
        self._run = run
        return self

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        r = self._run
        r.take_in(llm_request.contents)
        r.steps += 1
        step = r.steps
        try:
            turn = r.provider.chat(r.spec.system, r.messages, r.declarations)
        except BaseException as e:
            r.error = e
            raise
        r.tokens_in += turn.usage.tokens_in or 0
        r.tokens_out += turn.usage.tokens_out or 0
        usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=turn.usage.tokens_in,
                                                           candidates_token_count=turn.usage.tokens_out)
        if turn.cut_off:  # nothing of a cut-off turn is used: a tool call in it may be missing half its arguments
            r.cut_offs += 1
            r.last = "cut_off"
            r.trace.append({"type": "model", "step": step, "text": "", "calls": [], "cut_off": True})
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="")]),
                              finish_reason=types.FinishReason.MAX_TOKENS, usage_metadata=usage)
            return
        r.cut_offs = 0
        r.trace.append({"type": "model", "step": step, "text": R._short(turn.text, 500),
                        "calls": [{"tool": c.name, "args": c.args} for c in turn.calls]})
        r.messages.append({"role": "assistant", "turn": turn})
        r.last = "calls" if turn.calls else "text"
        parts = [types.Part(text=turn.text)] if turn.text or not turn.calls else []
        for i, c in enumerate(turn.calls):
            fid = f"s{step}c{i}"
            r.calls[fid] = (c, step)
            args = c.args if isinstance(c.args, dict) else {}
            name = c.name if c.name in r.tools else UNKNOWN
            parts.append(types.Part(function_call=types.FunctionCall(id=fid, name=name, args=args if name != UNKNOWN else {})))
        yield LlmResponse(content=types.Content(role="model", parts=parts), usage_metadata=usage)


class SpecTool(BaseTool):
    """One of our Tools as an ADK tool. It runs the tool and its checks exactly as the legacy loop does, and keeps the
    trace. ADK dispatches the calls of one turn as tasks, but this body never awaits, so they run one after another:
    the tools share one database session."""

    def __init__(self, run: _Run, tool: R.Tool | None):
        super().__init__(name=tool.name if tool else UNKNOWN, description=tool.description if tool else "Not a tool.")
        self._r, self._tool = run, tool

    def _get_declaration(self):
        params = self._tool.parameters if self._tool else R.params({})
        return types.FunctionDeclaration(name=self.name, description=self.description, parameters_json_schema=params)

    async def run_async(self, *, args: dict[str, Any], tool_context) -> Any:
        r, spec = self._r, self._r.spec
        if r.done:  # the answer was accepted earlier in this turn; the legacy loop stops there
            return {"error": "Not run: the answer was already accepted."}
        call, _ = r.calls[tool_context.function_call_id]
        tool = r.tools.get(call.name)
        if tool is None:
            content = {"error": f"There is no tool called {call.name!r}. Use one of {sorted(r.tools)}."}
        elif r.tool_calls >= spec.max_tool_calls and tool is not spec.finish:
            content = {"error": f"The tool-call limit ({spec.max_tool_calls}) is reached. Call {spec.finish.name} now."}
        else:
            r.tool_calls += tool is not spec.finish
            try:
                # a null optional argument counts as left out; otherwise str(None) would reach the tools as "None"
                given = {k: v for k, v in call.args.items() if v is not None} if isinstance(call.args, dict) else {}
                content = tool.run(R.repair_args(given, tool.parameters))
            except R.ToolError as e:
                content = {"error": str(e)}
            except (KeyError, TypeError, ValueError) as e:
                content = {"error": f"{call.name} could not use those arguments ({type(e).__name__}: {e})."}
            except LLMError as e:
                r.tool_failures += 1
                if e.kind not in ("temporary", "quota") or r.tool_failures > R.MAX_TOOL_FAILURES:
                    r.error = e
                    raise
                content = {"error": f"{call.name} failed just now ({e}) Try it once more or go another way; "
                                    f"if it keeps failing, call {spec.finish.name} with what you have."}
            else:
                if tool is spec.finish:
                    r.trace.append({"type": "finish", "args": call.args, "accepted": True})
                    r.done, r.result = True, content
                    tool_context.actions.skip_summarization = True  # the run ends here: no model call after the answer
                    return content if isinstance(content, dict) else {VALUE: content}
        if tool is spec.finish:
            r.trace.append({"type": "finish", "args": call.args, "accepted": False, "error": content.get("error")})
        else:
            r.trace.append({"type": "tool", "tool": call.name, "args": call.args, "result": R._short(content)})
        return content if isinstance(content, dict) else {VALUE: content}


class JsonLlm(BaseLlm):
    """A single-step agent's model: one provider.json call (the AI company's own structured output), so the request is the
    one the legacy path sends. The parsed answer and its usage are kept here; ADK gets the answer as the model's text."""
    _call: Callable = PrivateAttr()
    _out: dict = PrivateAttr(default_factory=dict)

    def bind(self, call: Callable) -> "JsonLlm":
        self._call = call
        return self

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        try:
            data, usage = self._call()
        except BaseException as e:
            self._out["error"] = e
            raise
        self._out["result"] = (data, usage)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps(data, ensure_ascii=False))]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=usage.tokens_in,
                                                                                    candidates_token_count=usage.tokens_out))


async def _arun_json(agent: LlmAgent, user: str) -> None:
    runner = InMemoryRunner(agent=agent, app_name=APP)
    try:
        session = await runner.session_service.create_session(app_name=APP, user_id=USER)
        async for _event in runner.run_async(user_id=USER, session_id=session.id, run_config=RunConfig(max_llm_calls=1),
                                             new_message=types.Content(role="user", parts=[types.Part(text=user)])):
            pass
    finally:
        await runner.close()


def run_json(provider, system: str, user: str, schema: dict, name: str) -> tuple[dict, Usage]:
    """A single-step agent on ADK: one LlmAgent without tools, one model turn. Same contract as provider.json."""
    model = JsonLlm(model=getattr(provider, "label", "provider")).bind(lambda: provider.json(system, user, schema, name))
    agent = LlmAgent(name=agent_name(name), description=f"single step: {name}", model=model, instruction=lambda _ctx: system)
    try:
        _call_async(lambda: _arun_json(agent, user))
    except BaseException:
        if "error" in model._out:
            raise model._out["error"] from None
        raise
    if "error" in model._out:
        raise model._out["error"]
    return model._out["result"]


def _call_async(make) -> None:
    """Run a coroutine to the end from sync code: directly in a worker thread or a sync request handler, or in a thread of
    its own when this is called from inside a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(make())
        return
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pool.submit(lambda: asyncio.run(make())).result()


def build(r: _Run) -> LlmAgent:
    spec = r.spec
    return LlmAgent(
        name=agent_name(spec.name),
        description=f"{spec.name} ({spec.version})",
        model=ProviderLlm(model=getattr(r.provider, "label", "provider")).bind(r),
        instruction=lambda _ctx: spec.system,  # a callable: ADK does not fill {placeholders} in it from session state
        tools=[SpecTool(r, t) for t in r.tools.values()] + [SpecTool(r, None)],
        before_model_callback=r.before_model,
    )


async def _arun(r: _Run, task: str) -> None:
    runner = InMemoryRunner(agent=build(r), app_name=APP)
    try:
        session = await runner.session_service.create_session(app_name=APP, user_id=USER)
        message = task
        while True:
            r.last = ""
            try:
                async for _event in runner.run_async(
                        user_id=USER, session_id=session.id, run_config=RunConfig(max_llm_calls=r.spec.max_steps + 2),
                        new_message=types.Content(role="user", parts=[types.Part(text=message)])):
                    pass
            except LlmCallsLimitExceededError:
                r.reason = r.reason or f"Stopped after {r.spec.max_steps} steps without an accepted answer."
            if r.done or r.reason:
                return
            if r.last == "cut_off":
                if r.cut_offs == 2:
                    r.reason = "The AI answer was cut off twice in a row before it finished."
                    return
                message = R.CUT_OFF_NOTE
            elif r.last == "text":
                message = f"Use a tool to continue, or call {r.spec.finish.name} with your answer."
            else:  # the invocation ended after tool calls without an answer: nothing left to continue
                r.reason = r.reason or f"Stopped after {r.spec.max_steps} steps without an accepted answer."
                return
    finally:
        await runner.close()


def run(spec: R.AgentSpec, provider, task: str, may_call: Callable[[], bool] = lambda: True) -> R.Outcome:
    """Run one agent on one task with ADK. Same contract as api/agent_runtime.run."""
    r = _Run(spec, provider, may_call)
    try:
        _call_async(lambda: _arun(r, task))
    except BaseException:
        if r.error is not None:
            raise r.error from None
        raise
    if r.error is not None:
        raise r.error
    return r.outcome()
