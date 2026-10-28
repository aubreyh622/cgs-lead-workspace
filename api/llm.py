"""The "universal plug" between agents and AI companies.

Agents never talk to Gemini, OpenAI or Claude directly. They use two calls:
    provider.json(system, user, schema, name)   one answer in a fixed JSON shape (the single-step agents)
    provider.chat(system, messages, tools)      one turn of a tool-using agent: the model may ask to run tools
To switch company, change AI_PROVIDER and AI_MODEL in .env and add that company's key. No agent code changes.

Supported: gemini (default), openai, claude. Add another company by writing one more class with _call() and _chat().

chat() history uses one neutral format for every company:
    {"role": "user", "text": "..."}
    {"role": "assistant", "turn": Turn}                        the model's turn, sent back exactly as received
    {"role": "tool", "results": [(ToolCall, "result text")], "note": "optional text for the model"}   what the tools returned
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
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
    tokens_in: int | None = None
    tokens_out: int | None = None


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


class Provider:
    name = "base"

    def __init__(self, model: str, key: str, transport=post):
        self.model, self.key, self.transport = model, key, transport
        self.calls = 0  # requests sent to the AI company, counted for the agents' daily budget

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
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            raise LLMError("The AI returned text that is not the required JSON.", "invalid_output") from None
        return data, usage

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> Turn:
        """tools: [{"name", "description", "parameters" (JSON schema)}]."""
        return self._send(self._chat, system, messages, tools)

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
                    for c, r in msg["results"]] + ([{"text": msg["note"]}] if msg.get("note") else [])})
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
                items.extend({"type": "function_call_output", "call_id": c.id, "output": _result_text(r)} for c, r in msg["results"])
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


class ClaudeProvider(Provider):
    """Anthropic Messages API over plain HTTP, so tests can use the same fake transport as the other companies."""
    name = "claude"
    url = "https://api.anthropic.com/v1/messages"

    def _headers(self):
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01"}

    @staticmethod
    def _checked(data):
        stop = data.get("stop_reason")
        if stop == "max_tokens":
            raise LLMError("The AI answer was cut off before it finished.", "invalid_output")
        if stop == "refusal":
            raise LLMError("The AI declined to answer this request.", "invalid_output")
        u = data.get("usage") or {}
        return data.get("content") or [], Usage(u.get("input_tokens"), u.get("output_tokens"))

    def _call(self, system, user, schema, schema_name):
        content, usage = self._checked(self.transport(self.url, self._headers(), {
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
                msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": _result_text(r)}
                                                         for c, r in msg["results"]]
                                                        + ([{"type": "text", "text": msg["note"]}] if msg.get("note") else [])})
        content, usage = self._checked(self.transport(self.url, self._headers(), {
            "model": self.model, "max_tokens": 4096, "system": system, "messages": msgs,
            "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]}))
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
