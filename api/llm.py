"""The "universal plug" between agents and AI companies.

Agents call provider.json(...) and never talk to Gemini or OpenAI directly. To switch company,
change AI_PROVIDER and AI_MODEL in .env and add that company's key. No agent code changes.

Supported: gemini (default), openai. Add another company by writing one more class with json().
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os

import httpx

from lead_agent.providers import ProviderError, post, redact

DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "openai": ""}
KEY_NAMES = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}


class LLMError(Exception):
    """The AI call failed. kind: auth | quota | temporary | config | invalid_output."""

    def __init__(self, message: str, kind: str = "config"):
        super().__init__(message)
        self.kind = kind


@dataclass
class Usage:
    tokens_in: int | None = None
    tokens_out: int | None = None


class Provider:
    name = "base"

    def __init__(self, model: str, key: str, transport=post):
        self.model, self.key, self.transport = model, key, transport

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    def json(self, system: str, user: str, schema: dict, schema_name: str) -> tuple[dict, Usage]:
        try:
            text, usage = self._call(system, user, schema, schema_name)
        except ProviderError as e:
            raise LLMError(redact(str(e), [self.key]), e.kind) from None
        except httpx.HTTPError as e:
            raise LLMError(f"Could not reach the AI service ({type(e).__name__}). Check the internet connection, VPN or proxy.", "temporary") from None
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            raise LLMError("The AI returned text that is not the required JSON.", "invalid_output") from None
        return data, usage

    def _call(self, system, user, schema, schema_name):  # pragma: no cover - implemented by subclasses
        raise NotImplementedError


class GeminiProvider(Provider):
    name = "gemini"

    def _call(self, system, user, schema, schema_name):
        data = self.transport(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                              {"x-goog-api-key": self.key},
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


class OpenAIProvider(Provider):
    name = "openai"

    def _call(self, system, user, schema, schema_name):
        data = self.transport("https://api.openai.com/v1/responses", {"Authorization": "Bearer " + self.key},
                              {"model": self.model, "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                               "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}}})
        text = "".join(c.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                       for c in item.get("content", []) if c.get("type") == "output_text")
        u = data.get("usage", {})
        return text, Usage(u.get("input_tokens"), u.get("output_tokens"))


PROVIDERS = {"gemini": GeminiProvider, "openai": OpenAIProvider}


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
