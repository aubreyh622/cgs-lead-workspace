"""Tools that agents may use. Each wraps something the workspace already does; none sends, approves or changes a score.

Web tools share one Pages record per agent run: what the agent found and what it actually read. Answers are checked
against it, so an agent can only quote a page it opened during this run.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy import select

from api import models as m
from api.agent_runtime import Tool, ToolError, params

PREVIEW = 240        # characters of each search result the model sees before it reads the page
PAGE_LIMIT = 4000    # characters of one page the model sees
MAX_RESULTS = 8


def web(c):
    if c.search_factory:
        return c.search_factory()
    from api.search import make_search
    return make_search(as_of=c.today)


class Pages:
    """What one agent run found (url -> page) and read (urls)."""

    def __init__(self):
        self.found: dict[str, dict] = {}
        self.read: set[str] = set()

    def keep(self, pages: list[dict]) -> list[dict]:
        out = []
        for p in pages[:MAX_RESULTS]:
            if not p.get("url"):
                continue
            self.found.setdefault(p["url"], p)
            out.append({"url": p["url"], "title": p.get("title", ""), "published": p.get("published") or "undated",
                        "preview": (p.get("content") or "")[:PREVIEW]})
        return out

    def text(self, url: str) -> str:
        return (self.found.get(url) or {}).get("content", "")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def web_tools(c, pages: Pages, company: str, domain: str) -> list[Tool]:
    w = web(c)

    def company_pages(args):
        topic = str(args.get("topic", "")).strip()
        if hasattr(w, "pages_for"):  # the synthetic internet hands over the company's pages directly
            found = w.pages_for(company, domain)
            words = [t for t in topic.lower().split() if len(t) > 2]
            found.sort(key=lambda p: -sum(t in (p["url"] + " " + p["title"] + " " + p["content"]).lower() for t in words))
        else:
            found = w.search(f"{company} {topic}".strip())
        return {"pages": pages.keep(found)} if found else {"pages": [], "note": "No pages found for that topic."}

    def search_web(args):
        query = str(args.get("query", "")).strip()
        if len(query) < 3:
            raise ToolError("Give a search query of at least a few words.")
        return {"results": pages.keep(w.search(query[:200]))}

    def read_page(args):
        url = str(args.get("url", "")).strip()
        page = pages.found.get(url)
        if page is None:
            if not (domain and _host(url).endswith(domain)) or not hasattr(w, "read"):
                raise ToolError("Read only pages returned by company_pages or search_web, or pages on the company's own website.")
            page = w.read(url)
            if not page:
                raise ToolError("That page could not be read.")
            pages.found[url] = page
        pages.read.add(url)
        text = page.get("content", "")
        return {"url": url, "title": page.get("title", ""), "published": page.get("published") or "undated",
                "text": text[:PAGE_LIMIT], **({"truncated": True} if len(text) > PAGE_LIMIT else {})}

    return [
        Tool("company_pages", "Pages from and about the company: its own website (leadership, about, news) and coverage of it. "
             "Returns titles and short previews; use read_page for the full text.",
             params({"topic": {"type": "string", "description": "What you are looking for, for example 'leadership team' or 'recent news'"}}),
             company_pages),
        Tool("search_web", "Search the web. Returns titles and short previews; use read_page for the full text.",
             params({"query": {"type": "string"}}), search_web),
        Tool("read_page", "Read the full text of a page that company_pages or search_web returned.",
             params({"url": {"type": "string"}}), read_page),
    ]


def contact_tools(c, company_id: str) -> list[Tool]:
    def known_contacts(_args):
        rows = c.s.scalars(select(m.Contact).where(m.Contact.company_id == company_id))
        return {"contacts": [{"name": x.name, "title": x.title, "confirmed_by_a_person": bool(x.confirmed_by)} for x in rows if x.name]}

    return [Tool("known_contacts", "People the workspace already has as contacts at this company, with their titles.",
                 params({}), known_contacts)]
