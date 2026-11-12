"""Embeddings for the CGS library's meaning search (the semantic half of hybrid search in api/knowledge.py).

Off unless KNOWLEDGE_EMBEDDINGS names a provider:
  gemini  GEMINI_API_KEY, model gemini-embedding-001 (768 dimensions). With GEMINI_BACKEND=agent_platform: CGS's Google
          Cloud project, signed by the service account, no key (api/google_cloud.py). Express mode has no embeddings, so
          GEMINI_BACKEND=express keeps embeddings on AI Studio with GEMINI_API_KEY.
  openai  OPENAI_API_KEY, model text-embedding-3-small
  voyage  VOYAGE_API_KEY, model voyage-3.5-lite
KNOWLEDGE_EMBEDDINGS_MODEL overrides the model.

What leaves the machine: the text of library passages that are public (a CGS web page) or synthetic, and the search
queries agents write. Passages from private CGS documents are embedded only with KNOWLEDGE_EMBED_PRIVATE=allowed (CGS
must agree first); until then they are found by keyword only.

Vectors are cached in runtime/embeddings-<provider>-<model>.json by a hash of the text, so each passage is embedded once.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import threading

import httpx

from api.settings import ROOT

BATCH = 90
MODELS = {"gemini": "gemini-embedding-001", "openai": "text-embedding-3-small", "voyage": "voyage-3.5-lite"}
KEYS = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "voyage": "VOYAGE_API_KEY"}


class EmbeddingError(Exception):
    def __init__(self, message: str, rate_limited: bool = False):
        super().__init__(message)
        self.rate_limited = rate_limited  # HTTP 429: try again in a minute, not in ten


def _post(url: str, headers: dict, payload: dict) -> dict:
    try:
        r = httpx.post(url, headers=headers, json=payload, timeout=60)
    except httpx.HTTPError as e:
        raise EmbeddingError(f"Embedding request failed: {type(e).__name__}") from None
    if r.status_code != 200:
        raise EmbeddingError(f"Embedding request failed: HTTP {r.status_code}", rate_limited=r.status_code == 429)
    return r.json()


class Embedder:
    """embed(texts, kind) -> one vector per text. kind: "document" (library passages) or "query"."""

    def __init__(self, provider: str, key: str, model: str | None = None, transport=_post, cache_dir: Path | None = None,
                 backend: str = "ai_studio"):
        if provider not in MODELS:
            raise EmbeddingError(f"Unknown embeddings provider {provider!r}; use one of {sorted(MODELS)}.")
        self.provider, self.key, self.model, self.transport = provider, key, model or MODELS[provider], transport
        self.backend = backend  # gemini only: ai_studio | agent_platform (the vectors are the same model, so the cache is shared)
        self.name = f"{provider}:{self.model}"
        self.cache_file = (cache_dir or ROOT / "runtime") / f"embeddings-{provider}-{self.model}.json".replace("/", "_")
        self._cache: dict[str, list[float]] | None = None
        self._queries: dict[str, list[float]] = {}  # query vectors kept in memory: the same search does not call again
        self._lock = threading.Lock()

    def _call(self, texts: list[str], kind: str) -> list[list[float]]:
        if self.provider == "gemini" and self.backend == "agent_platform":
            from api.google_cloud import CloudConfigError, auth_headers, model_url
            task = "RETRIEVAL_DOCUMENT" if kind == "document" else "RETRIEVAL_QUERY"
            out = []
            try:
                url, headers = model_url(self.model, "predict", "agent_platform"), auth_headers(backend="agent_platform")
            except CloudConfigError as e:
                raise EmbeddingError(str(e)) from None
            for text in texts:  # gemini-embedding-001 on Agent Platform takes one text per request
                data = self.transport(url, headers, {"instances": [{"content": text, "task_type": task}],
                                                     "parameters": {"outputDimensionality": 768}})
                try:
                    out.append(data["predictions"][0]["embeddings"]["values"])
                except (KeyError, IndexError, TypeError):
                    raise EmbeddingError("Agent Platform returned no embedding for a passage.") from None
            return out
        if self.provider == "gemini":
            task = "RETRIEVAL_DOCUMENT" if kind == "document" else "RETRIEVAL_QUERY"
            data = self.transport(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:batchEmbedContents",
                                  {"x-goog-api-key": self.key},
                                  {"requests": [{"model": f"models/{self.model}", "content": {"parts": [{"text": t}]},
                                                 "taskType": task, "outputDimensionality": 768} for t in texts]})
            return [e["values"] for e in data.get("embeddings", [])]
        headers = {"Authorization": f"Bearer {self.key}"}
        if self.provider == "openai":
            data = self.transport("https://api.openai.com/v1/embeddings", headers, {"model": self.model, "input": texts})
        else:
            data = self.transport("https://api.voyageai.com/v1/embeddings", headers,
                                  {"model": self.model, "input": texts, "input_type": kind})
        return [d["embedding"] for d in sorted(data.get("data", []), key=lambda d: d.get("index", 0))]

    def _load(self) -> dict[str, list[float]]:
        if self._cache is None:
            try:
                self._cache = json.loads(self.cache_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._cache = {}
        return self._cache

    def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]:
        if kind == "query":
            todo = [t for t in dict.fromkeys(texts) if t not in self._queries]
            if todo:
                out = self._call(todo, kind)
                if len(out) != len(todo):
                    raise EmbeddingError("The embeddings provider returned the wrong number of vectors.")
                if len(self._queries) > 500:
                    self._queries.clear()
                self._queries.update(zip(todo, out))
            return [self._queries[t] for t in texts]
        with self._lock:
            cache = self._load()
            keys = [hashlib.sha1(t.encode("utf-8")).hexdigest() for t in texts]
            todo = [(k, t) for k, t in dict(zip(keys, texts)).items() if k not in cache]
            for i in range(0, len(todo), BATCH):
                part = todo[i:i + BATCH]
                vecs = self._call([t for _, t in part], kind)
                if len(vecs) != len(part):
                    raise EmbeddingError("The embeddings provider returned the wrong number of vectors.")
                cache.update({k: v for (k, _), v in zip(part, vecs)})
                # saved after every batch: when a later batch hits a rate limit (free tiers allow ~100 texts a minute),
                # the next attempt continues from here instead of starting over and hitting the same limit again
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                self.cache_file.write_text(json.dumps(cache), encoding="utf-8")
            return [cache[k] for k in keys]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def from_env() -> Embedder | None:
    """The embedder .env asks for, or None (meaning search off)."""
    provider = os.environ.get("KNOWLEDGE_EMBEDDINGS", "").strip().lower()
    if provider not in KEYS:
        return None  # empty, "off" or unknown: keyword search only
    model = os.environ.get("KNOWLEDGE_EMBEDDINGS_MODEL", "").strip() or None
    if provider == "gemini" and os.environ.get("GEMINI_BACKEND", "").strip().lower() == "agent_platform":
        return Embedder(provider, "", model, backend="agent_platform")  # signed by the service account: no key
    key = os.environ.get(KEYS.get(provider, ""), "").strip()
    if not key:
        return None  # no key: keyword search only (GET /api/knowledge says why)
    return Embedder(provider, key, model)
