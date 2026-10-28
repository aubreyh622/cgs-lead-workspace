"""K1 knowledge lookup (a small RAG): find passages in the CGS library that agents may quote.

Sources:
  - knowledge/*.md and knowledge/*.txt  real CGS documents; each blank-line-separated paragraph is one passage
  - api/seed/cgs_library_demo.json     SYNTHETIC demo library, used while KNOWLEDGE_DEMO is not "off"

Search is plain keyword scoring (BM25), which needs no extra service or key and is easy to check.
It can later be swapped for embeddings without changing the agents.
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

from api.settings import ROOT

DEMO = Path(__file__).parent / "seed" / "cgs_library_demo.json"
DEMO_SOLUTIONS = Path(__file__).parent / "seed" / "cgs_solutions_demo.md"  # SYNTHETIC stand-in for CGS's solutions document
STOP = set("a an and are as at be by for from has have in is it its of on or that the their this to was we were which with will our your".split())


@dataclass(frozen=True)
class Passage:
    id: str
    doc_id: str
    title: str
    type: str
    text: str
    synthetic: bool


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP]


class Library:
    def __init__(self, passages: list[Passage]):
        self.passages = passages
        self.by_id = {p.id: p for p in passages}
        self._tf = [Counter(tokens(p.text + " " + p.title)) for p in passages]
        n = len(passages) or 1
        df = Counter(t for tf in self._tf for t in tf)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self._avg = sum(sum(tf.values()) for tf in self._tf) / n

    @classmethod
    def load(cls, folder: Path | None = None, demo: bool | None = None) -> "Library":
        folder = folder if folder is not None else ROOT / "knowledge"
        demo = demo if demo is not None else os.environ.get("KNOWLEDGE_DEMO", "on").strip().lower() != "off"
        out = []
        if folder.exists():
            for f in sorted(list(folder.glob("*.md")) + list(folder.glob("*.txt"))):
                if f.name.startswith("_"):
                    continue  # instructions, not library content
                if "solution" in f.stem.lower() and f.suffix == ".md":
                    out += solutions(f, synthetic=False)  # CGS's solutions document: one solution per "## " section
                    continue
                paras = [p.strip() for p in re.split(r"\n\s*\n", f.read_text(encoding="utf-8")) if len(p.strip()) > 40]
                title = next((ln.lstrip("# ").strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()), f.stem)
                out += [Passage(f"{f.stem}#p{i + 1}", f.stem, title, "document", p, False) for i, p in enumerate(paras)]
        if demo:
            for d in json.loads(DEMO.read_text(encoding="utf-8"))["documents"]:
                out += [Passage(f"{d['id']}#p{i + 1}", d["id"], d["title"], d["type"], t, True) for i, t in enumerate(d["passages"])]
            out += solutions(DEMO_SOLUTIONS, synthetic=True)
        return cls(out)

    def search(self, query: str, k: int = 3, types: tuple[str, ...] | None = None) -> list[dict]:
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
        return [{"id": p.id, "title": p.title, "type": p.type, "text": p.text, "synthetic": p.synthetic, "score": round(s, 2)} for s, p in scored[:k]]


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
        _cache["lib"] = Library.load()
    return _cache["lib"]
