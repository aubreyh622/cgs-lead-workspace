"""K1 knowledge lookup (a small RAG): find passages in the CGS library that agents may quote.

Sources:
  - knowledge/**/*.md and *.txt         real CGS documents; each blank-line-separated paragraph is one passage. Optional
                                        header lines "Source: <url>" and "Type: service|case|whitepaper|..." (written by
                                        api/fetch_cgs_site.py into knowledge/cgs-public/) give the page address and kind
  - api/seed/cgs_library_demo.json     SYNTHETIC demo library. KNOWLEDGE_DEMO=auto (default): used only while there are no
                                        real documents; on: always; off: never. KNOWLEDGE_DIR moves the real folder (tests
                                        point it at an empty place, so they never depend on local content)

Search (KNOWLEDGE_SEARCH):
  bm25    keyword scoring only: no extra service or key, easy to check
  hybrid  keyword scoring plus meaning search (embeddings, api/embeddings.py), merged by reciprocal-rank fusion: a
          passage ranked high by either side comes up. It finds "operating model for an acquired plant" when the passage
          says "integration after a merger". Needs KNOWLEDGE_EMBEDDINGS and its key
  auto    (default) hybrid when embeddings are configured and the library has at least HYBRID_MIN passages, else bm25
If meaning search fails (network, quota), the search falls back to keywords for 10 minutes (1 minute after a rate
limit) and says so in status().
Short acronyms count as words: AI, ML, HR, BI, UX, and IT when written in capitals ("it" the pronoun is ignored).
Agents may only quote a passage word for word, and the code checks that they did.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import time

from api.settings import ROOT

DEMO = Path(__file__).parent / "seed" / "cgs_library_demo.json"
DEMO_SOLUTIONS = Path(__file__).parent / "seed" / "cgs_solutions_demo.md"  # SYNTHETIC stand-in for CGS's solutions document
ACRONYMS = {"ai", "ml", "hr", "bi", "ux", "ui", "vr", "ar", "ev", "rd", "pe", "it"}
CASED = {"it", "ar", "pe"}  # also ordinary words: an acronym only when written in capitals
HYBRID_MIN = 40  # auto: below this many passages, keywords alone do well
RRF_K = 60       # reciprocal-rank fusion constant (the usual value)
MIN_SIMILARITY = 0.3  # a passage found by meaning alone must be at least this close to the query
RETRY_AFTER = 600     # seconds of keyword-only search after meaning search failed
RETRY_AFTER_LIMIT = 60  # after a rate limit (HTTP 429) only a minute
STOP = set("a an and are as at be by for from has have in is it its of on or that the their this to was we were which with will our your".split())


@dataclass(frozen=True)
class Passage:
    id: str
    doc_id: str
    title: str
    type: str
    text: str
    synthetic: bool
    source: str = ""  # the public page the passage comes from, when known


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[A-Za-z0-9]+", text):
        low = w.lower()
        if len(low) > 2 and low not in STOP:
            out.append(low)
        elif low in ACRONYMS and (low not in CASED or w.isupper()):
            out.append(low)
    return out


def embeddable(p: "Passage") -> bool:
    """May this passage's text go to the embeddings provider? Public pages and synthetic text yes; private CGS documents
    only with KNOWLEDGE_EMBED_PRIVATE=allowed."""
    return p.synthetic or p.source.startswith("http") or os.environ.get("KNOWLEDGE_EMBED_PRIVATE", "").strip().lower() == "allowed"


def search_mode(n_passages: int, embedder) -> str:
    mode = os.environ.get("KNOWLEDGE_SEARCH", "auto").strip().lower()
    if embedder is None or mode == "bm25":
        return "bm25"
    return "hybrid" if mode == "hybrid" or n_passages >= HYBRID_MIN else "bm25"


class Library:
    def __init__(self, passages: list[Passage], embedder=None):
        self.passages = passages
        self.by_id = {p.id: p for p in passages}
        self.embedder = embedder
        self.mode = search_mode(len(passages), embedder)
        self._vectors: dict[str, list[float]] | None = None  # passage id -> vector, filled on the first hybrid search
        self.semantic_error = ""
        self._retry_at = 0.0
        self._tf = [Counter(tokens(p.text + " " + p.title)) for p in passages]
        n = len(passages) or 1
        df = Counter(t for tf in self._tf for t in tf)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self._avg = sum(sum(tf.values()) for tf in self._tf) / n

    @classmethod
    def load(cls, folder: Path | None = None, demo: bool | None = None, embedder=None) -> "Library":
        folder = folder if folder is not None else Path(os.environ.get("KNOWLEDGE_DIR") or ROOT / "knowledge")
        out = []
        if folder.exists():
            for f in sorted(list(folder.rglob("*.md")) + list(folder.rglob("*.txt"))):
                if f.name.startswith("_") or any(part.startswith("_") for part in f.relative_to(folder).parts[:-1]):
                    continue  # instructions, not library content
                if "solution" in f.stem.lower() and f.suffix == ".md":
                    out += solutions(f, synthetic=False)  # CGS's solutions document: one solution per "## " section
                    continue
                out += document(f)
        if demo is None:
            mode = os.environ.get("KNOWLEDGE_DEMO", "auto").strip().lower()
            demo = mode == "on" or (mode not in ("on", "off") and not out)  # auto: only while no real documents
        if demo:
            for d in json.loads(DEMO.read_text(encoding="utf-8"))["documents"]:
                out += [Passage(f"{d['id']}#p{i + 1}", d["id"], d["title"], d["type"], t, True) for i, t in enumerate(d["passages"])]
            out += solutions(DEMO_SOLUTIONS, synthetic=True)
        return cls(out, embedder)

    def keyword(self, query: str, types: tuple[str, ...] | None = None) -> list[tuple[float, Passage]]:
        q = set(tokens(query))
        scored = []
        for p, tf in zip(self.passages, self._tf):
            if types and p.type not in types:
                continue
            length = sum(tf.values()) or 1
            s = sum(self._idf.get(t, 0) * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * length / self._avg)) for t in q if t in tf)
            if s > 0:
                scored.append((s, p))
        scored.sort(key=lambda x: -x[0])
        return scored

    def meaning(self, query: str, types: tuple[str, ...] | None = None) -> list[tuple[float, Passage]]:
        """Passages by closeness of meaning to the query (cosine of embeddings), at least MIN_SIMILARITY."""
        from api.embeddings import cosine
        if self._vectors is None:
            todo = [p for p in self.passages if embeddable(p)]
            vecs = self.embedder.embed([f"{p.title}\n{p.text}" for p in todo], "document")
            self._vectors = {p.id: v for p, v in zip(todo, vecs)}
        qv = self.embedder.embed([query], "query")[0]
        scored = [(cosine(qv, self._vectors[p.id]), p) for p in self.passages
                  if p.id in self._vectors and not (types and p.type not in types)]
        scored = [x for x in scored if x[0] >= MIN_SIMILARITY]
        scored.sort(key=lambda x: -x[0])
        return scored

    def search(self, query: str, k: int = 3, types: tuple[str, ...] | None = None) -> list[dict]:
        kw = self.keyword(query, types)
        if self.mode != "hybrid" or time.monotonic() < self._retry_at:
            return [self._row(p, round(s, 2)) for s, p in kw[:k]]
        try:
            sem = self.meaning(query, types)
            self.semantic_error = ""
        except Exception as e:  # network, quota, bad key: keywords still work; try meaning search again later
            self.semantic_error = f"{type(e).__name__}: {e}"[:200]
            self._retry_at = time.monotonic() + (RETRY_AFTER_LIMIT if getattr(e, "rate_limited", False) else RETRY_AFTER)
            return [self._row(p, round(s, 2)) for s, p in kw[:k]]
        fused: dict[str, float] = {}
        how: dict[str, set] = {}
        for side, ranked in (("keyword", kw), ("meaning", sem)):
            for rank, (_, p) in enumerate(ranked[:50]):
                fused[p.id] = fused.get(p.id, 0) + 1 / (RRF_K + rank + 1)
                how.setdefault(p.id, set()).add(side)
        top = sorted(fused, key=lambda i: -fused[i])[:k]
        return [{**self._row(self.by_id[i], round(fused[i] * 1000, 2)), "matched": "+".join(sorted(how[i]))} for i in top]

    @staticmethod
    def _row(p: Passage, score: float) -> dict:
        return {"id": p.id, "title": p.title, "type": p.type, "text": p.text, "synthetic": p.synthetic, "score": score,
                **({"source": p.source} if p.source else {})}

    def status(self) -> dict:
        return {"search": self.mode, "embeddings": getattr(self.embedder, "name", None),
                "embeddable": sum(1 for p in self.passages if embeddable(p)) if self.embedder else 0,
                **({"meaning_search_failed": self.semantic_error} if self.semantic_error else {})}


TYPES = {"service": "service", "case": "case"}  # other kinds (whitepaper, report, about, approach) are "document"


def document(f: Path) -> list[Passage]:
    """One file, one passage per paragraph. Header lines (title, Source:, Type:) and headings are not passages."""
    text = f.read_text(encoding="utf-8")
    head = {k.lower(): v.strip() for k, v in re.findall(r"^(Source|Type):\s*(.+)$", text, flags=re.M)}
    title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.strip()), f.stem)
    paras = [x.strip() for x in re.split(r"\n\s*\n", text)
             if len(x.strip()) > 40 and not x.strip().startswith(("#", "Source:", "Type:"))]
    kind = TYPES.get(head.get("type", "").lower(), "document")
    return [Passage(f"{f.stem}#p{i + 1}", f.stem, title, kind, x, False, head.get("source", "")) for i, x in enumerate(paras)]


def solutions(path: Path, synthetic: bool) -> list[Passage]:
    """A solutions document: each '## ' section is one solution (type 'solution'), each paragraph under it one passage."""
    out = []
    if not path.exists():
        return out
    for section in re.split(r"^## ", path.read_text(encoding="utf-8"), flags=re.M)[1:]:
        title, _, body = section.partition("\n")
        doc = "sol-" + "-".join(re.findall(r"[a-z0-9]+", title.lower()))[:60]
        paras = [x.strip() for x in re.split(r"\n\s*\n", body) if len(x.strip()) > 40]
        out += [Passage(f"{doc}#p{i + 1}", doc, ("[Demo] " if synthetic else "") + "Solution: " + title.strip(), "solution", x, synthetic)
                for i, x in enumerate(paras)]
    return out


_cache: dict[str, Library] = {}


def library() -> Library:
    if "lib" not in _cache:
        from api.embeddings import from_env
        _cache["lib"] = Library.load(embedder=from_env())
    return _cache["lib"]
