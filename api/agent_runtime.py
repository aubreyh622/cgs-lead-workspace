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
import os
import re
from typing import Any, Callable

from api.llm import LLMError, Provider, Usage

RESULT_LIMIT = 6000  # characters of one tool result the model sees
KEEP = 200           # a long text inside a result is never cut below this many characters
MAX_TOOL_FAILURES = 2  # outside failures (the search service is down) one run puts up with; the next one ends the run
CUT_OFF_NOTE = ("Your last answer was cut off before it finished, so none of it was used and no tool ran. "
                "Answer again and keep it shorter: one tool call at a time, with shorter texts.")


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


def _texts(node):
    """Every text inside a result, as (the dict or list holding it, its key or index, the text)."""
    for key, value in (node.items() if isinstance(node, dict) else enumerate(node)):
        if isinstance(value, str):
            yield node, key, value
        elif isinstance(value, (dict, list)):
            yield from _texts(value)


def fit(content, limit: int = RESULT_LIMIT) -> str:
    """One tool result as the text the model sees: at most `limit` characters, and still whole JSON. A result that is too
    long loses the end of its longest texts (a page's text, a long note), each marked with how much was cut, instead of
    being cut in the middle of the JSON, which left the model with a broken last field and no sign of what was missing."""
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    if not isinstance(content, (dict, list)):
        return text[:limit - 40] + f" ... [cut: {len(text) - limit + 40} more characters]"
    data = json.loads(text)  # a copy, so the tool's own result stays whole for the trace and the checks
    if isinstance(data, dict):
        data["truncated"] = True
    for _ in range(40):
        over = len(json.dumps(data, ensure_ascii=False)) - limit
        if over <= 0:
            return json.dumps(data, ensure_ascii=False)
        holder, key, value = max(_texts(data), key=lambda x: len(x[2]), default=(None, None, ""))
        if len(value) <= KEEP:
            break
        cut = min(len(value) - KEEP, over + 60)
        holder[key] = value[:len(value) - cut] + f" ... [cut: {cut} more characters]"
    keep = max(limit - 400, 200)  # nothing long to shorten (hundreds of short rows): show the start, as one text
    while True:
        out = json.dumps({"truncated": True, "note": "The result was too long to show in full; this is its start.",
                          "start": text[:keep]}, ensure_ascii=False)
        if len(out) <= limit or keep <= 200:
            return out
        keep -= len(out) - limit  # quotation marks inside the text take two characters each once it is a JSON string


_PARAM = re.compile(r'<parameter name="([A-Za-z_][A-Za-z0-9_]*)">')
_CLOSE = re.compile(r"(?:\s*</[A-Za-z_][A-Za-z0-9_]*>)+\s*$")


def repair_args(args: dict, schema: dict) -> dict:
    """Some models (seen live with Claude Haiku 5.5, 5 Nov 2026) write the later arguments of a call inside an earlier
    string, as <parameter name="x">...</parameter> text. Move that text back to the arguments the schema names when they
    are missing, and cut it when the named argument already holds its own value (seen live on 5 and 6 Nov 2026: the copy,
    sometimes worded a little differently, stayed in the brief). Text naming no argument of the schema is left as it is,
    so the tool's own checks still decide."""
    props = (schema or {}).get("properties") or {}
    out = dict(args)
    for key, value in args.items():
        if not isinstance(value, str) or not _PARAM.search(value):
            continue
        parts = _PARAM.split(value)  # [text before, name1, text1, name2, text2, ...]
        found, repeats = {}, False
        for name, text in zip(parts[1::2], parts[2::2]):
            if name not in props or name == key:
                continue
            text = _CLOSE.sub("", text).strip()
            if not out.get(name):
                found[name] = text
            else:  # the named argument has its own value: the leaked copy never belongs in this string
                repeats = True
        if not found and not repeats:
            continue
        out[key] = _CLOSE.sub("", parts[0]).strip()
        for name, text in found.items():
            kind = props[name].get("type")
            if kind == "array":
                try:
                    text = json.loads(text)
                except ValueError:
                    text = [text]
            elif kind == "boolean":
                text = text.lower() == "true"
            out[name] = text
    return out


RUNTIMES = ("auto", "adk", "legacy")


def adk_installed() -> bool:
    try:
        import google.adk  # noqa: F401
    except ImportError:
        return False
    return True


def runtime() -> str:
    """Which loop runs the agents: AGENT_RUNTIME in .env. "adk" = Google's Agent Development Kit (api/adk_runtime.py);
    "legacy" = the hand-written loop below; "auto" (default) = adk when google-adk is installed, else legacy."""
    name = os.environ.get("AGENT_RUNTIME", "auto").strip().lower() or "auto"
    if name not in RUNTIMES:
        raise LLMError(f"AGENT_RUNTIME must be one of {RUNTIMES}, not {name!r}.", "config")
    if name == "auto":
        return "adk" if adk_installed() else "legacy"
    if name == "adk" and not adk_installed():
        raise LLMError("AGENT_RUNTIME=adk needs Google's Agent Development Kit: pip install -r requirements.lock", "config")
    return name


def run(spec: AgentSpec, provider: Provider, task: str, may_call: Callable[[], bool] = lambda: True) -> Outcome:
    """Run one agent on one task. may_call() is asked before every AI request (the daily budget)."""
    if runtime() == "adk":
        from api import adk_runtime
        return adk_runtime.run(spec, provider, task, may_call)
    return run_legacy(spec, provider, task, may_call)


def ask(provider: Provider, system: str, user: str, schema: dict, name: str) -> tuple[dict, Usage]:
    """One answer in a fixed JSON shape, for the single-step agents (labelling a reply, the weekly feedback review, a LinkedIn
    note ...). Runs on the runtime AGENT_RUNTIME picks; either way the AI company gets provider.json's request."""
    if runtime() == "adk":
        from api import adk_runtime
        return adk_runtime.run_json(provider, system, user, schema, name)
    return provider.json(system, user, schema, name)


def run_legacy(spec: AgentSpec, provider: Provider, task: str, may_call: Callable[[], bool] = lambda: True) -> Outcome:
    """The hand-written loop. Kept as the reference the ADK runtime is measured against."""
    tools = {t.name: t for t in spec.tools + [spec.finish]}
    declarations = [t.declaration() for t in tools.values()]
    messages: list[dict] = [{"role": "user", "text": task}]
    trace: list[dict] = []
    tokens_in = tokens_out = tool_calls = cut_offs = tool_failures = 0

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
        if turn.cut_off:  # nothing of a cut-off turn is used: a tool call in it may be missing half its arguments
            cut_offs += 1
            trace.append({"type": "model", "step": step, "text": "", "calls": [], "cut_off": True})
            if cut_offs == 2:
                return outcome("incomplete", reason="The AI answer was cut off twice in a row before it finished.")
            messages.append({"role": "user", "text": CUT_OFF_NOTE})
            continue
        cut_offs = 0
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
                    args = repair_args(args, tool.parameters)
                    content = tool.run(args)
                except ToolError as e:
                    content = {"error": str(e)}
                except (KeyError, TypeError, ValueError) as e:
                    content = {"error": f"{call.name} could not use those arguments ({type(e).__name__}: {e})."}
                except LLMError as e:
                    # An outside service the tool depends on failed (the search service timed out, a rate limit).
                    # The work done so far is worth more than the failed call: tell the agent, so it can try again,
                    # go another way or finish with what it has. A wrong key or setting, or repeated failures, end the
                    # run with the real reason instead.
                    tool_failures += 1
                    if e.kind not in ("temporary", "quota") or tool_failures > MAX_TOOL_FAILURES:
                        raise
                    content = {"error": f"{call.name} failed just now ({e}) Try it once more or go another way; "
                                        f"if it keeps failing, call {spec.finish.name} with what you have."}
                else:
                    if tool is spec.finish:
                        trace.append({"type": "finish", "args": call.args, "accepted": True})
                        return outcome("done", content)
            if tool is spec.finish:
                trace.append({"type": "finish", "args": call.args, "accepted": False, "error": content.get("error")})
            else:
                trace.append({"type": "tool", "tool": call.name, "args": call.args, "result": _short(content)})
            results.append((call, fit(content), isinstance(content, dict) and "error" in content))
        last = step == spec.max_steps - 1
        messages.append({"role": "tool", "results": results,
                         **({"note": f"This is your last step. Call {spec.finish.name} now with what you have."} if last else {})})
    return outcome("incomplete", reason=f"Stopped after {spec.max_steps} steps without an accepted answer.")
