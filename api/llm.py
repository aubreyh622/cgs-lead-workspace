"""The "universal plug" between agents and AI companies.

Agents never talk to Gemini, OpenAI or Claude directly. They use two calls:
    provider.json(system, user, schema, name)   one answer in a fixed JSON shape (the single-step agents)
    provider.chat(system, messages, tools)      one turn of a tool-using agent: the model may ask to run tools
To switch company, change AI_PROVIDER and AI_MODEL in .env and add that company's key. No agent code changes.

Supported: gemini (default), openai, claude. Add another company by writing one more class with _call() and _chat().

chat() history uses one neutral format for every company:
    {"role": "user", "text": "..."}
    {"role": "assistant", "turn": Turn}                        the model's turn, sent back exactly as received
    {"role": "tool", "results": [(ToolCall, "result text", failed)], "note": "optional text for the model"}   what the tools
                                                               returned; failed = the tool refused or could not do the call
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import os
import threading
from typing import Any

import httpx

from lead_agent.providers import ProviderError, post, redact

DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "openai": "", "claude": "claude-haiku-5-5"}
KEY_NAMES = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY"}


class LLMError(Exception):
    """The AI call failed. kind: auth | quota | temporary | config | invalid_output."""

    def __init__(self, message: str, kind: str = "config"):
        super().__init__(message)
        self.kind = kind


@dataclass
class Usage:
    tokens_in: int | None = None     # the whole prompt, including the parts read from or written to the cache
    tokens_out: int | None = None
    cache_read: int | None = None    # Claude: prompt tokens served from the cache (billed at a tenth or less)
    cache_write: int | None = None   # Claude: prompt tokens written to the cache (billed at 1.25 times)


# Every AI request this process made, by model: requests, tokens in and out, cache reads and writes. The quality check
# reads it to price a run; nothing else depends on it.
METER: dict[str, Counter] = {}
_METER_LOCK = threading.Lock()


def _add(counter: Counter, u: "Usage") -> None:
    counter.update({"requests": 1, "in": u.tokens_in or 0, "out": u.tokens_out or 0,
                    "cache_read": u.cache_read or 0, "cache_write": u.cache_write or 0})


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict
    native_id: bool = True  # False when the company gave no id and one was made up here


@dataclass
class Turn:
    """One model turn: its text, the tools it asked for, and its message in the company's own format.
    The raw message goes back unchanged on the next call (Gemini needs the thought signatures inside it)."""
    text: str
    calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw: Any = None
    cut_off: bool = False  # the answer hit the length limit before it finished: nothing in it may be used


class Provider:
    name = "base"

    def __init__(self, model: str, key: str, transport=post):
        self.model, self.key, self.transport = model, key, transport
        self.calls = 0  # requests sent to the AI company, counted for the agents' daily budget
        self.used = Counter()  # this provider's own share of METER

    def _count(self, usage: "Usage") -> None:
        with _METER_LOCK:
            _add(METER.setdefault(self.label, Counter()), usage)
            _add(self.used, usage)

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    def _send(self, fn, *args):
        self.calls += 1
        try:
            return fn(*args)
        except ProviderError as e:
            raise LLMError(redact(str(e), [self.key]), e.kind) from None
        except httpx.HTTPError as e:
            raise LLMError(f"Could not reach the AI service ({type(e).__name__}). Check the internet connection, VPN or proxy.", "temporary") from None

    def json(self, system: str, user: str, schema: dict, schema_name: str) -> tuple[dict, Usage]:
        text, usage = self._send(self._call, system, user, schema, schema_name)
        self._count(usage)
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            raise LLMError("The AI returned text that is not the required JSON.", "invalid_output") from None
        return data, usage

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> Turn:
        """tools: [{"name", "description", "parameters" (JSON schema)}]."""
        turn = self._send(self._chat, system, messages, tools)
        self._count(turn.usage)
        return turn

    def _call(self, system, user, schema, schema_name):  # pragma: no cover - implemented by subclasses
        raise NotImplementedError

    def _chat(self, system, messages, tools):  # pragma: no cover - implemented by subclasses
        raise NotImplementedError


def _result_text(content: str) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


class GeminiProvider(Provider):
    name = "gemini"

    @property
    def url(self) -> str:
        return f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

    def _call(self, system, user, schema, schema_name):
        data = self.transport(self.url, {"x-goog-api-key": self.key},
                              {"systemInstruction": {"parts": [{"text": system}]},
                               "contents": [{"role": "user", "parts": [{"text": user}]}],
                               "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema,
                                                    "temperature": 0, "maxOutputTokens": 8192}})
        cands = data.get("candidates") or []
        if not cands:
            raise LLMError("Gemini returned no answer. Check model access, or the content may have been blocked.", "config")
        text = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []) if not p.get("thought"))
        u = data.get("usageMetadata", {})
        return text, Usage(u.get("promptTokenCount"), u.get("candidatesTokenCount"))

    def _chat(self, system, messages, tools):
        contents = []
        for msg in messages:
            if msg["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": msg["text"]}]})
            elif msg["role"] == "assistant":
                contents.append(msg["turn"].raw)
            else:
                contents.append({"role": "user", "parts": [
                    {"functionResponse": {**({"id": c.id} if c.native_id else {}), "name": c.name, "response": {"result": _result_text(r)}}}
                    for c, r, _ in msg["results"]] + ([{"text": msg["note"]}] if msg.get("note") else [])})
        data = self.transport(self.url, {"x-goog-api-key": self.key}, {
            "systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
            "tools": [{"functionDeclarations": [{"name": t["name"], "description": t["description"], "parametersJsonSchema": t["parameters"]}
                                                for t in tools]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 4096}})
        cands = data.get("candidates") or []
        if not cands:
            raise LLMError("Gemini returned no answer. Check model access, or the content may have been blocked.", "config")
        parts = (cands[0].get("content") or {}).get("parts") or []
        calls = []
        for i, p in enumerate(parts):
            if "functionCall" in p:
                fc = p["functionCall"]
                calls.append(ToolCall(fc.get("id") or f"call-{i}", fc.get("name", ""), fc.get("args") or {}, bool(fc.get("id"))))
        text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
        u = data.get("usageMetadata", {})
        return Turn(text, calls, Usage(u.get("promptTokenCount"), u.get("candidatesTokenCount")), {"role": "model", "parts": parts})


class OpenAIProvider(Provider):
    name = "openai"
    url = "https://api.openai.com/v1/responses"

    def _call(self, system, user, schema, schema_name):
        data = self.transport(self.url, {"Authorization": "Bearer " + self.key},
                              {"model": self.model, "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                               "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}}})
        text = "".join(c.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                       for c in item.get("content", []) if c.get("type") == "output_text")
        u = data.get("usage", {})
        return text, Usage(u.get("input_tokens"), u.get("output_tokens"))

    def _chat(self, system, messages, tools):
        items: list[dict] = [{"role": "system", "content": system}]
        for msg in messages:
            if msg["role"] == "user":
                items.append({"role": "user", "content": msg["text"]})
            elif msg["role"] == "assistant":
                items.extend(msg["turn"].raw)
            else:
                items.extend({"type": "function_call_output", "call_id": c.id, "output": _result_text(r)} for c, r, _ in msg["results"])
                if msg.get("note"):
                    items.append({"role": "user", "content": msg["note"]})
        data = self.transport(self.url, {"Authorization": "Bearer " + self.key}, {
            "model": self.model, "input": items,
            "tools": [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["parameters"], "strict": False}
                      for t in tools]})
        out = data.get("output") or []
        calls = []
        for item in out:
            if item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except ValueError:
                    args = {}
                calls.append(ToolCall(item.get("call_id", ""), item.get("name", ""), args if isinstance(args, dict) else {}))
        text = "".join(c.get("text", "") for item in out if item.get("type") == "message"
                       for c in item.get("content", []) if c.get("type") == "output_text")
        u = data.get("usage", {})
        return Turn(text, calls, Usage(u.get("input_tokens"), u.get("output_tokens")), out)


def _strict(schema):
    """The schema for Claude's strict tool use: every object closed to other fields (additionalProperties false)."""
    if isinstance(schema, dict):
        out = {k: _strict(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out.setdefault("additionalProperties", False)
            out.setdefault("properties", {})
        return out
    if isinstance(schema, list):
        return [_strict(x) for x in schema]
    return schema


class ClaudeProvider(Provider):
    """Anthropic Messages API over plain HTTP, so tests can use the same fake transport as the other companies."""
    name = "claude"
    url = "https://api.anthropic.com/v1/messages"

    def _headers(self):
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01"}

    def _post(self, payload):
        """A long answer (a full research brief) can take more than a minute to write; wait for it."""
        if self.transport is post:
            return post(self.url, self._headers(), payload, read_timeout=150)
        return self.transport(self.url, self._headers(), payload)

    @staticmethod
    def _usage(data) -> Usage:
        """input_tokens counts only the part of the prompt that was neither read from nor written to the cache."""
        u = data.get("usage") or {}
        read, write = u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0
        total = None if u.get("input_tokens") is None else u["input_tokens"] + read + write
        return Usage(total, u.get("output_tokens"), read, write)

    @classmethod
    def _checked(cls, data):
        stop = data.get("stop_reason")
        if stop == "max_tokens":
            raise LLMError("The AI answer was cut off before it finished.", "invalid_output")
        if stop == "refusal":
            raise LLMError("The AI declined to answer this request.", "invalid_output")
        return data.get("content") or [], cls._usage(data)

    def _call(self, system, user, schema, schema_name):
        content, usage = self._checked(self._post({
            "model": self.model, "max_tokens": 8192, "system": system, "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}}}))
        return "".join(b.get("text", "") for b in content if b.get("type") == "text"), usage

    def _chat(self, system, messages, tools):
        msgs = []
        for msg in messages:
            if msg["role"] == "user":
                msgs.append({"role": "user", "content": msg["text"]})
            elif msg["role"] == "assistant":
                msgs.append({"role": "assistant", "content": msg["turn"].raw})
            else:
                msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": _result_text(r),
                                                          **({"is_error": True} if failed else {})}
                                                         for c, r, failed in msg["results"]]
                                                        + ([{"type": "text", "text": msg["note"]}] if msg.get("note") else [])})
        data = self._post({
            # 16000: the limit covers the model's thinking as well as its answer, and the research agent's finish call
            # carries the whole brief (it ran past 4096 in a live run)
            "model": self.model, "max_tokens": 16000, "messages": msgs,
            # Caching: an agent sends its tools, its instructions and the whole conversation again at every step. A marker
            # after the instructions keeps them (and the tools before them) cached for the whole run; the top-level one
            # moves forward with the conversation, so each step reads the earlier steps from the cache instead of paying
            # for them again. A prompt shorter than the model's minimum is simply not cached.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "cache_control": {"type": "ephemeral"},
            # strict: Claude returns arguments that match the schema exactly (each one a field of its own)
            "tools": [{"name": t["name"], "description": t["description"], "input_schema": _strict(t["parameters"]), "strict": True}
                      for t in tools]})
        if data.get("stop_reason") == "max_tokens":  # the agent loop asks for a shorter answer instead of losing the run
            return Turn("", [], self._usage(data), None, cut_off=True)
        content, usage = self._checked(data)
        calls = [ToolCall(b.get("id", ""), b.get("name", ""), b.get("input") or {}) for b in content if b.get("type") == "tool_use"]
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        return Turn(text, calls, usage, content)


PROVIDERS = {"gemini": GeminiProvider, "openai": OpenAIProvider, "claude": ClaudeProvider}


def make_provider(name: str, model: str = "", key: str | None = None, transport=post) -> Provider:
    name = (name or "gemini").strip().lower()
    if name not in PROVIDERS:
        raise LLMError(f"AI_PROVIDER must be one of {tuple(PROVIDERS)}, not {name!r}.", "config")
    model = (model or DEFAULT_MODELS[name]).strip()
    if not model:
        raise LLMError(f"Set AI_MODEL in .env to a {name} model your account can use.", "config")
    key = (key if key is not None else os.environ.get(KEY_NAMES[name], "")).strip()
    if not key or key.lower() in ("your-api-key", "redacted") or any(ch.isspace() for ch in key):
        raise LLMError(f"Add your {KEY_NAMES[name]} to the .env file, then restart.", "auth")
    return PROVIDERS[name](model, key, transport)
