"""The search plug: how agents reach the web.

Every search provider has one method:
    search(query: str, start_date: date | None = None, news: bool = False) -> list[dict]
returning up to N results, each {"title", "url", "content", "published"} ("published" is YYYY-MM-DD or "").

Built in:
    mock    a synthetic internet (api/seed/mock_web.json): invented companies, no key, no cost
    google  Google Search through Gemini (GEMINI_API_KEY, or the service account on Google Cloud); pages read from the site
    tavily  real web search (needs TAVILY_API_KEY); kept for comparison
To add another service, write a class with search() and add it to PROVIDERS. Settings live in config/search.json;
WEB_MODE in .env overrides the provider. See docs/SEARCH_INTEGRATION.md.
"""
from __future__ import annotations

from datetime import date, datetime
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

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


def iso_date(raw: str | None) -> str:
    """A published date as YYYY-MM-DD, or "" when it cannot be read. Tavily's news dates look like 'Tue, 27 Oct 2026 10:00:00 GMT'."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


def real_url(url: str) -> str:
    """The page a search result points to. Search services sometimes return a Google redirect link
    (google.com/url?...): unwrap it when it names the page, otherwise drop the result ("")."""
    p = urlparse(url.strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    if not (host == "google.com" or host.startswith("google.")) or p.path != "/url":
        return url.strip()
    q = parse_qs(p.query)
    for key in ("url", "q"):
        target = (q.get(key) or [""])[0]
        if target.startswith(("http://", "https://")):
            return target
    return ""


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
                "include_raw_content": False, "auto_parameters": False, "include_usage": True, "include_published_date": True}
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
        out = []
        for r in (data.get("results") or [])[:self.results]:
            url = real_url(r.get("url") or "")
            if url:
                out.append({"title": r.get("title", ""), "url": url, "content": (r.get("content") or "")[:1500],
                            "published": iso_date(r.get("published_date"))})
        return out

    def read(self, url: str) -> dict | None:
        """The full text of one page (Tavily extract; one credit per five pages)."""
        try:
            data = self.transport("https://api.tavily.com/extract", {"Authorization": "Bearer " + self.key},
                                  {"urls": [url], "extract_depth": "basic", "format": "text"})
        except ProviderError as e:
            raise LLMError("Reading the page failed: " + redact(str(e), [self.key]), e.kind) from None
        except httpx.HTTPError as e:
            raise LLMError(f"Could not reach the search service ({type(e).__name__}).", "temporary") from None
        r = next(iter(data.get("results") or []), None)
        return {"url": url, "title": "", "content": (r.get("raw_content") or "")[:8000], "published": ""} if r else None


_DATE = re.compile(r"\b(20\d\d-[01]\d-[0-3]\d)\b")
_REDIRECT_HOSTS = ("vertexaisearch.cloud.google.com",)


def _resolve(url: str) -> str:
    """Google Search grounding hands out redirect links; the page itself is where the redirect points."""
    try:
        r = httpx.get(url, follow_redirects=False, timeout=httpx.Timeout(10, connect=5), headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError:
        return ""
    return r.headers.get("location", "") if r.is_redirect else ""


def fetch_page(url: str) -> dict | None:
    """The readable text of one web page (headings, paragraphs, list items), fetched directly. None when it cannot be read."""
    from api.fetch_cgs_site import TextBlocks
    try:
        r = httpx.get(url, follow_redirects=True, timeout=httpx.Timeout(20, connect=10), headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError:
        return None
    if r.status_code >= 400 or "html" not in r.headers.get("content-type", "html"):
        return None
    p = TextBlocks()
    try:
        p.feed(r.text)
        p.close()
    except Exception:  # noqa: BLE001 - a broken page is a page that cannot be read
        return None
    text = "\n".join(t for _, t in p.blocks)
    return {"url": url, "title": p.title.strip(), "content": text[:8000], "published": ""} if text else None


USER_AGENT = "CGS-Workspace/2.0 (lead research)"


class GoogleSearch:
    """Real web search through Google Search, as a tool of Gemini (grounding). Signed like every Gemini request
    (api/google_cloud.py: an API key on AI Studio, the service account on Google Cloud). The pages are the ones Google
    returned (grounding chunks), never URLs the model wrote; the preview is what the model said each page supports.
    Pages are then read directly from the site (read). Cost: one Gemini request per search, billed as grounded."""
    name = "google"

    def __init__(self, key: str | None = None, transport=post, results: int = 5, model: str | None = None, resolver=_resolve,
                 reader=fetch_page):
        from api.google_cloud import CloudConfigError, express_key, gemini_backend
        try:
            self.backend = gemini_backend()
        except CloudConfigError as e:
            raise LLMError(str(e), "config") from None
        if key is None:
            key = "" if self.backend == "agent_platform" else express_key() if self.backend == "express" else os.environ.get("GEMINI_API_KEY", "")
        self.key = key.strip()
        if self.backend != "agent_platform" and not self.key:
            raise LLMError("Google Search runs through Gemini: add your GEMINI_API_KEY to the .env file, then restart.", "auth")
        from api.llm import DEFAULT_MODELS
        self.model = (model or os.environ.get("SEARCH_MODEL") or os.environ.get("GEMINI_MODEL") or DEFAULT_MODELS["gemini"]).strip()
        self.transport, self.results, self.resolver, self.reader = transport, results, resolver, reader
        self._urls: dict[str, str] = {}

    def _prompt(self, query: str, start_date: date | None, news: bool) -> str:
        return (f"Use Google Search to find up to {self.results} web pages for this query: {query!r}."
                + (" Only news articles." if news else "")
                + (f" Prefer pages published on or after {start_date.isoformat()}." if start_date else "")
                + " For every page you use, write one sentence that starts with the page's publication date as YYYY-MM-DD "
                  "(or 'undated') and then says what the page reports. Report only what the pages say; add nothing of your own.")

    def _real(self, uri: str) -> str:
        host = (urlparse(uri).hostname or "").lower()
        if not host.endswith(_REDIRECT_HOSTS):
            return real_url(uri)
        if uri not in self._urls:
            self._urls[uri] = real_url(self.resolver(uri) or "")
        return self._urls[uri]

    def search(self, query: str, start_date: date | None = None, news: bool = False) -> list[dict]:
        from api.google_cloud import auth_headers, model_url
        body = {"contents": [{"role": "user", "parts": [{"text": self._prompt(query, start_date, news)}]}],
                "tools": [{"google_search": {}}], "generationConfig": {"temperature": 0}}
        try:
            data = self.transport(model_url(self.model, "generateContent", self.backend), auth_headers(self.key, self.backend), body)
        except ProviderError as e:
            raise LLMError("Web search failed: " + redact(str(e), [self.key]), e.kind) from None
        except httpx.HTTPError as e:
            raise LLMError(f"Could not reach Google Search ({type(e).__name__}).", "temporary") from None
        cand = next(iter(data.get("candidates") or []), {})
        meta = cand.get("groundingMetadata") or {}
        said: dict[int, list[str]] = {}
        for sup in meta.get("groundingSupports") or []:
            text = ((sup.get("segment") or {}).get("text") or "").strip()
            for i in sup.get("groundingChunkIndices") or []:
                if text and text not in said.setdefault(i, []):
                    said[i].append(text)
        out, seen = [], set()
        for i, chunk in enumerate(meta.get("groundingChunks") or []):
            web = chunk.get("web") or {}
            url = self._real(web.get("uri") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            content = " ".join(said.get(i, []))
            found = _DATE.search(content)
            out.append({"title": web.get("title") or (urlparse(url).hostname or ""), "url": url,
                        "content": content[:1500], "published": found.group(1) if found else ""})
            if len(out) >= self.results:
                break
        return out

    def read(self, url: str) -> dict | None:
        """The full text of one page, read from the site itself (no search cost)."""
        return self.reader(url)


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

    def read(self, url: str) -> dict | None:
        return next((dict(p) for p in self.pages if p["url"] == url), None)

    def pages_for(self, company: str, domain: str) -> list[dict]:
        name = company.lower()
        return [dict(p) for p in self.pages if (domain and domain in p["url"]) or name in (p["title"] + " " + p["content"]).lower()]


PROVIDERS = {"mock": MockSearch, "google": GoogleSearch, "tavily": TavilySearch}


def make_search(provider: str | None = None, as_of: date | None = None, local: bool = False):
    """The search client. TOOLS_TRANSPORT=mcp: a client of the cgs-search MCP server instead (local=True always builds the
    client here; the server itself does that)."""
    cfg = search_config()
    name = (provider or cfg["provider"]).lower()
    if name not in PROVIDERS:
        raise LLMError(f"Search provider {name!r} is not installed. Choose one of {tuple(PROVIDERS)} in config/search.json.", "config")
    if not local:
        from cgs_tools import client as MCP
        if MCP.wanted():
            return MCP.SearchClient(name, as_of)
    if name == "mock":
        return MockSearch(results=int(cfg["results_per_query"]), as_of=as_of)
    return PROVIDERS[name](results=int(cfg["results_per_query"]))
