"""The search plug: how agents reach the web.

Every search provider has one method:
    search(query: str, start_date: date | None = None, news: bool = False) -> list[dict]
returning up to N results, each {"title", "url", "content", "published"} ("published" is YYYY-MM-DD or "").

Built in:
    mock    a synthetic internet (api/seed/mock_web.json): invented companies, no key, no cost
    tavily  real web search (needs TAVILY_API_KEY)
To add another service, write a class with search() and add it to PROVIDERS. Settings live in config/search.json;
WEB_MODE in .env overrides the provider. See docs/SEARCH_INTEGRATION.md.
"""
from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path

import httpx

from api.knowledge import tokens
from api.llm import LLMError
from api.settings import ROOT
from lead_agent.providers import ProviderError, post, redact

MOCK_WEB = Path(__file__).parent / "seed" / "mock_web.json"
CONFIG = ROOT / "config" / "search.json"


def search_config() -> dict:
    cfg = {"provider": "mock", "custom_queries": [], "recency_days": 183, "news_only": True, "results_per_query": 5}
    try:
        cfg.update({k: v for k, v in json.loads(CONFIG.read_text(encoding="utf-8")).items() if k in cfg})
    except (OSError, ValueError):
        pass
    if os.environ.get("WEB_MODE"):
        cfg["provider"] = os.environ["WEB_MODE"].strip().lower()
    return cfg


class TavilySearch:
    """Real web search. One basic search costs 1 Tavily credit."""
    name = "tavily"

    def __init__(self, key: str | None = None, transport=post, results: int = 5):
        self.key = (key if key is not None else os.environ.get("TAVILY_API_KEY", "")).strip()
        self.transport, self.results = transport, results
        if not self.key or self.key.lower() in ("your-api-key", "redacted") or any(c.isspace() for c in self.key):
            raise LLMError("Add your TAVILY_API_KEY to the .env file, then restart.", "auth")

    def search(self, query: str, start_date: date | None = None, news: bool = False) -> list[dict]:
        body = {"query": query, "search_depth": "basic", "max_results": self.results, "include_answer": False,
                "include_raw_content": False, "auto_parameters": False, "include_usage": True}
        if news:
            body["topic"] = "news"
        if start_date:
            body.update(start_date=start_date.isoformat(), filter_by_published_date=False, include_published_date=True)
        try:
            data = self.transport("https://api.tavily.com/search", {"Authorization": "Bearer " + self.key}, body)
        except ProviderError as e:
            raise LLMError("Web search failed: " + redact(str(e), [self.key]), e.kind) from None
        except httpx.HTTPError as e:
            raise LLMError(f"Could not reach the search service ({type(e).__name__}).", "temporary") from None
        return [{"title": r.get("title", ""), "url": r.get("url", ""), "content": (r.get("content") or "")[:1500],
                 "published": (r.get("published_date") or "")[:10]} for r in (data.get("results") or [])[:self.results] if r.get("url")]


class MockSearch:
    """A synthetic internet: keyword search over invented pages. Free, offline, repeatable."""
    name = "mock"

    def __init__(self, path: Path = MOCK_WEB, results: int = 5, as_of: date | None = None):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.all_pages, self.simulations, self.results = data["pages"], data.get("client_simulations", {}), results
        self.as_of = as_of or date.today()

    @property
    def pages(self) -> list[dict]:
        """Pages published by the simulated date. Later news appears when the simulation clock passes its date."""
        day = self.as_of.isoformat()
        return [p for p in self.all_pages if not p["published"] or p["published"] <= day]

    def search(self, query: str, start_date: date | None = None, news: bool = False) -> list[dict]:
        q = set(tokens(query))
        scored = []
        for p in self.pages:
            if news and not p["published"]:
                continue
            if start_date and p["published"] and p["published"] < start_date.isoformat():
                continue
            words = tokens(p["title"] + " " + p["content"])
            score = sum(words.count(t) for t in q) + 2 * sum(t in tokens(p["title"]) for t in q)
            if score:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        return [dict(p) for _, p in scored[:self.results]]

    def pages_for(self, company: str, domain: str) -> list[dict]:
        name = company.lower()
        return [dict(p) for p in self.pages if (domain and domain in p["url"]) or name in (p["title"] + " " + p["content"]).lower()]


PROVIDERS = {"mock": MockSearch, "tavily": TavilySearch}


def make_search(provider: str | None = None, as_of: date | None = None):
    cfg = search_config()
    name = (provider or cfg["provider"]).lower()
    if name not in PROVIDERS:
        raise LLMError(f"Search provider {name!r} is not installed. Choose one of {tuple(PROVIDERS)} in config/search.json.", "config")
    if name == "mock":
        return MockSearch(results=int(cfg["results_per_query"]), as_of=as_of)
    return PROVIDERS[name](results=int(cfg["results_per_query"]))
