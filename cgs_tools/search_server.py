"""MCP server cgs-search: the web, as config/search.json names it (WEB_MODE overrides).

Tools (all read only, public data):
  search_info    which search service answers, and whether company_pages lists a company's pages directly
  web_search     a search: up to N results with title, url, a text preview and the published date (YYYY-MM-DD or "")
  read_page      the full text of one page
  company_pages  the pages from and about a company (the synthetic web lists them; the real web searches the name)

as_of: the simulated date for the synthetic web (pages published later are hidden); the real web ignores it.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from api.llm import LLMError
from cgs_tools.common import main, run

mcp = FastMCP("cgs-search", instructions="Web search and page reading for the CGS lead workspace. Read only; public data.")


class WebPage(BaseModel):
    title: str = ""
    url: str
    content: str = Field("", description="A preview from a search, or the page text from read_page")
    published: str = Field("", description="YYYY-MM-DD, or empty when unknown")


class SearchInfo(BaseModel):
    provider: str = Field(description="mock (the synthetic web), google or tavily")
    company_pages_direct: bool = Field(description="True when company_pages lists the company's own pages (synthetic web)")


class Results(BaseModel):
    results: list[WebPage]


class PageResult(BaseModel):
    found: bool
    page: WebPage | None = None


@lru_cache(maxsize=8)
def _search(as_of: str):
    from api.search import make_search
    return make_search(as_of=date.fromisoformat(as_of) if as_of else None, local=True)


def _day(text: str) -> date | None:
    return date.fromisoformat(text) if text else None


def _pages(rows: list[dict]) -> Results:
    return Results(results=[WebPage(title=r.get("title") or "", url=r["url"], content=r.get("content") or "",
                                    published=r.get("published") or "") for r in rows if r.get("url")])


@mcp.tool()
async def search_info() -> SearchInfo:
    """Which search service answers."""
    s = _search("")
    return SearchInfo(provider=s.name, company_pages_direct=hasattr(s, "pages_for"))


@mcp.tool()
async def web_search(query: str, start_date: str = "", news: bool = False, as_of: str = "") -> Results:
    """Search the web. start_date (YYYY-MM-DD): only results published since then. news: news results only."""
    return await run(lambda: _pages(_search(as_of).search(query, _day(start_date), news)), (LLMError,))


@mcp.tool()
async def read_page(url: str, as_of: str = "") -> PageResult:
    """The full text of one page; found is false when it cannot be read."""
    def go():
        s = _search(as_of)
        page = s.read(url) if hasattr(s, "read") else None
        return PageResult(found=bool(page), page=_pages([page]).results[0] if page and page.get("url") else None)
    return await run(go, (LLMError,))


@mcp.tool()
async def company_pages(company: str, domain: str = "", as_of: str = "") -> Results:
    """Pages from and about a company: its own website (domain) and coverage of it."""
    def go():
        s = _search(as_of)
        return _pages(s.pages_for(company, domain) if hasattr(s, "pages_for") else s.search(company))
    return await run(go, (LLMError,))


if __name__ == "__main__":
    main(mcp)
