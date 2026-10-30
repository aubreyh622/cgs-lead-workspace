"""Fill the CGS library from CGS Advisors' public website: python -m api.fetch_cgs_site [--out knowledge/cgs-public]

Downloads the public service, approach and whitepaper pages of https://www.cgsadvisors.com/ and writes one Markdown file
per page into knowledge/cgs-public/ (the library loader reads it; see api/knowledge.py). Each file starts with the title,
the source address and the kind of page; every paragraph becomes one passage the agents may quote word for word.

Only public pages are read. The folder is not committed (.gitignore): it is CGS's own text, so the repository does not
republish it; run this script to rebuild it. Text that repeats on many pages (menus, footers) is dropped.
"""
from __future__ import annotations

import argparse
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import re
import time

import httpx

from api.settings import ROOT

SITE = "https://www.cgsadvisors.com"
PAGES = [  # (path, kind)
    ("/about-us/", "about"),
    ("/respond-to-disruption/", "approach"),
    ("/it-transformation/", "service"),
    ("/strategic-transformation-2/", "service"),
    ("/innovation/", "service"),
    ("/inertia-removal/", "service"),
    ("/thought-leadership/", "service"),
    ("/reports/", "report"),
    ("/2026/07/29/whitepaper-7-0-cognitive-bravery-leadership-reinvention-for-the-agentic-era/", "whitepaper"),
    ("/2026/06/30/whitepaper-6-0-the-collapsible-enterprise-shifting-from-static-hierarchies-to-the-agentic-mesh/", "whitepaper"),
    ("/2026/05/31/whitepaper-5-0-the-great-decoupling-redefining-the-nature-of-work-in-the-agentic-era/", "whitepaper"),
    ("/2026/04/30/whitepaper-4-0-the-inversion-point-navigating-the-phases-of-agentic-transformation/", "whitepaper"),
    ("/2026/03/31/whitepaper-3-0-unified-agentic-enterprise-maturity-framework/", "whitepaper"),
    ("/2026/03/31/whitepaper-2-0-understanding-ai-building-blocks-the-literacy-imperative-for-the-agentic-era/", "whitepaper"),
    ("/2026/02/28/whitepaper-1-0-beyond-the-bot-why-your-firm-needs-an-intelligence-strategy-not-an-ai-strategy/", "whitepaper"),
]
SKIP_TAGS = {"script", "style", "nav", "footer", "header", "form", "noscript", "svg", "button"}
BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "blockquote"}


class TextBlocks(HTMLParser):
    """The page's readable blocks (headings, paragraphs, list items), outside menus, headers and footers."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip, self.cur, self.tag, self.blocks, self.title = 0, [], None, [], ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS and not self.skip:
            self._flush()
            self.tag = tag
        elif tag == "br":
            self.cur.append(" ")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS and tag == self.tag:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip and self.tag:
            self.cur.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.cur)).strip()
        if text:
            self.blocks.append((self.tag, text))
        self.cur, self.tag = [], None


def page_blocks(html: str) -> tuple[str, list[tuple[str, str]]]:
    p = TextBlocks()
    p.feed(html)
    p.close()
    title = re.sub(r"\s*[|–-]\s*CGS Advisors.*$", "", p.title.strip()) or "CGS Advisors"
    return title, p.blocks


def to_markdown(title: str, url: str, kind: str, blocks: list[tuple[str, str]], boilerplate: set[str]) -> str:
    lines = [f"# {title}", f"Source: {url}", f"Type: {kind}", ""]
    for tag, text in blocks:
        if text in boilerplate or len(text) < 3:
            continue
        lines.append(("## " + text) if tag in ("h1", "h2", "h3", "h4") else text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def slug(path: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", path.lower().replace("/2026/", "/")))[:80] or "home"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="knowledge/cgs-public")
    a = ap.parse_args(argv)
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    http = httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "CGS-workspace-library/1.0 (Ivey AFP prototype)"})
    pages = []
    for path, kind in PAGES:
        url = SITE + path
        try:
            r = http.get(url)
        except httpx.HTTPError as e:
            print(f"  skipped {url}: {type(e).__name__}")
            continue
        if r.status_code != 200:
            print(f"  skipped {url}: HTTP {r.status_code}")
            continue
        title, blocks = page_blocks(r.text)
        pages.append((path, kind, url, title, blocks))
        time.sleep(0.5)  # polite to the site
    seen = Counter(text for *_, blocks in pages for text in {t for _, t in blocks})
    boilerplate = {t for t, n in seen.items() if n >= 3}  # menus, footers, sign-up boxes
    total = 0
    for path, kind, url, title, blocks in pages:
        md = to_markdown(title, url, kind, blocks, boilerplate)
        paras = [p for p in md.split("\n\n") if len(p.strip()) > 40 and not p.startswith(("# ", "Source:", "Type:"))]
        (out / f"{slug(path)}.md").write_text(md, encoding="utf-8")
        total += len(paras)
        print(f"  {kind:10} {len(paras):3} passages  {title[:70]}")
    print(f"{len(pages)} pages, about {total} passages, written to {a.out}. Restart the workspace to load them.")
    return 0 if pages else 1


if __name__ == "__main__":
    raise SystemExit(main())
