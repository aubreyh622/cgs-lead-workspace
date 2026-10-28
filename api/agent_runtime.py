"""The agent loop: an AI model that reasons, picks tools, reads what they return, and decides what to do next.

    task --> model --(asks for tools)--> tools run --> results go back to the model --> ... --> finish

An agent is a system prompt, a set of tools and a finish tool. The finish tool's parameters are the agent's answer, and
its code checks that answer (for example: the quote must be on a page the agent actually read). A rejected answer goes
back to the model as an error, so the agent can correct itself, the way a person corrects a draft after review.

Brakes, all enforced by code: a step limit, a tool-call limit, and a budget check before every AI request.
Every step is kept in the trace, so a person can see how the agent reached its answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Callable

from api.llm import Provider, Usage

RESULT_LIMIT = 6000  # characters of one tool result the model sees


class ToolError(Exception):
    """A tool refused a request. The model sees the message and may try again."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[[dict], Any]  # returns something JSON-serializable; raises ToolError for a message back to the model

    def declaration(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


@dataclass
class AgentSpec:
    name: str
    version: str
    system: str
    tools: list[Tool]
    finish: Tool          # the answer; its run() checks it and returns the final result, or raises ToolError
    max_steps: int = 8    # model turns
    max_tool_calls: int = 16


@dataclass
class Outcome:
    status: str                       # done | incomplete
    result: Any = None                # what the finish tool returned
    reason: str = ""                  # why an incomplete run stopped
    trace: list[dict] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


def params(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required if required is not None else list(properties),
            "additionalProperties": False}


def _short(value, n=300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= n else text[:n] + "..."


def run(spec: AgentSpec, provider: Provider, task: str, may_call: Callable[[], bool] = lambda: True) -> Outcome:
    """Run one agent on one task. may_call() is asked before every AI request (the daily budget)."""
    tools = {t.name: t for t in spec.tools + [spec.finish]}
    declarations = [t.declaration() for t in tools.values()]
    messages: list[dict] = [{"role": "user", "text": task}]
    trace: list[dict] = []
    tokens_in = tokens_out = tool_calls = 0

    def outcome(status, result=None, reason=""):
        if reason:
            trace.append({"type": "stop", "reason": reason})
        return Outcome(status, result, reason, trace, Usage(tokens_in, tokens_out))

    for step in range(1, spec.max_steps + 1):
        if not may_call():
            return outcome("incomplete", reason="The daily AI budget is used up.")
        turn = provider.chat(spec.system, messages, declarations)
        tokens_in += turn.usage.tokens_in or 0
        tokens_out += turn.usage.tokens_out or 0
        trace.append({"type": "model", "step": step, "text": _short(turn.text, 500),
                      "calls": [{"tool": c.name, "args": c.args} for c in turn.calls]})
        messages.append({"role": "assistant", "turn": turn})
        if not turn.calls:
            messages.append({"role": "user", "text": f"Use a tool to continue, or call {spec.finish.name} with your answer."})
            continue
        results = []
        for call in turn.calls:
            tool = tools.get(call.name)
            if tool is None:
                content = {"error": f"There is no tool called {call.name!r}. Use one of {sorted(tools)}."}
            elif tool_calls >= spec.max_tool_calls and tool is not spec.finish:
                content = {"error": f"The tool-call limit ({spec.max_tool_calls}) is reached. Call {spec.finish.name} now."}
            else:
                tool_calls += tool is not spec.finish
                try:
                    # a null optional argument counts as left out; otherwise str(None) would reach the tools as "None"
                    args = {k: v for k, v in call.args.items() if v is not None} if isinstance(call.args, dict) else {}
                    content = tool.run(args)
                except ToolError as e:
                    content = {"error": str(e)}
                except (KeyError, TypeError, ValueError) as e:
                    content = {"error": f"{call.name} could not use those arguments ({type(e).__name__}: {e})."}
                else:
                    if tool is spec.finish:
                        trace.append({"type": "finish", "args": call.args, "accepted": True})
                        return outcome("done", content)
            if tool is spec.finish:
                trace.append({"type": "finish", "args": call.args, "accepted": False, "error": content.get("error")})
            else:
                trace.append({"type": "tool", "tool": call.name, "args": call.args, "result": _short(content)})
            results.append((call, _short(content, RESULT_LIMIT)))
        last = step == spec.max_steps - 1
        messages.append({"role": "tool", "results": results,
                         **({"note": f"This is your last step. Call {spec.finish.name} now with what you have."} if last else {})})
    return outcome("incomplete", reason=f"Stopped after {spec.max_steps} steps without an accepted answer.")
