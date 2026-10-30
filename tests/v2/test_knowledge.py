"""Step 6d checks: the K1 knowledge lookup (small RAG) and how agents may use it."""
from fastapi.testclient import TestClient

from api.knowledge import Library
from api.live_agents import check_draft, check_proposals
from api.main import create_app
from api.settings import Settings

NOTE = "Elena said the two AI inspection pilots work in isolation, but we have no plan for scaling them across the twelve plants."


def test_demo_library_finds_the_right_service():
    lib = Library.load(folder=None, demo=True)
    top = lib.search("AI pilots that have not scaled across plants after a merger", k=2)
    assert top[0]["id"].startswith("svc-first-mile") and all(p["synthetic"] for p in top)
    assert lib.search("month-end close reconciliations")[0]["id"] == "svc-finance#p1"


def test_real_documents_replace_the_demo(tmp_path):
    (tmp_path / "services.md").write_text("# CGS services\n\nDigital strategy for mid-market manufacturers, from first use cases to the operating model.\n\nShort.", encoding="utf-8")
    lib = Library.load(folder=tmp_path, demo=False)
    assert [p.id for p in lib.passages] == ["services#p1"]  # the title line and very short paragraphs are skipped
    assert all(not p.synthetic for p in lib.passages) and lib.search("manufacturers digital strategy")[0]["title"] == "CGS services"


def test_fit_may_quote_the_library_but_nothing_else_may():
    lib = Library.load(folder=None, demo=True)
    passage = lib.search("AI pilots that have not scaled", k=1)[0]
    quote = passage["text"].split(". ")[0] + "."
    raw = [
        {"kind": "cond", "key": "fit", "value": "true", "quote": quote, "why": "", "evidence_type": "approved_research", "source": passage["id"]},
        {"kind": "score", "key": "N", "value": "4", "quote": quote, "why": "", "evidence_type": "approved_research", "source": passage["id"]},
        {"kind": "cond", "key": "fit", "value": "true", "quote": "We guarantee results.", "why": "", "evidence_type": "approved_research", "source": "made-up#p1"},
        {"kind": "cond", "key": "problem", "value": "true", "quote": "we have no plan for scaling them across the twelve plants", "why": "", "evidence_type": "client_statement", "source": "notes"},
    ]
    kept, dropped = check_proposals(raw, NOTE, {passage["id"]: passage})
    assert [(p["key"], p["source"]) for p in kept] == [("fit", passage["id"]), ("problem", "notes")]
    assert "synthetic demo document" in kept[0]["why"] and dropped == {"library_misuse": 2}


def test_draft_may_cite_a_library_passage_but_must_cite_a_fact():
    ids = {"f1", "case-industrial-ai#p1"}
    text = "Corvane bought Arden. CGS built a sequenced roadmap that moved two pilots to production across all plants within a year."
    check_draft("Hi", "Hi [[f1|after Arden]]. We [[case-industrial-ai#p1|moved two pilots to production]] elsewhere. Alex, CGS Advisors", ids, text)
    try:
        check_draft("Hi", "We [[case-industrial-ai#p1|moved two pilots to production]]. Alex", ids, text)
        raise AssertionError("expected a failure")
    except Exception as e:
        assert "does not cite any fact" in str(e)


def test_knowledge_endpoint(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "k.sqlite3")))
    r = api.get("/api/knowledge", params={"q": "ERP order-to-cash process"}).json()
    assert r["passages"][0]["id"] == "case-erp-om#p1" and r["synthetic"] is True


def test_public_pages_keep_their_address_and_kind(tmp_path, monkeypatch):
    sub = tmp_path / "cgs-public"
    sub.mkdir()
    (sub / "it-transformation.md").write_text(
        "# IT Transformation\nSource: https://www.cgsadvisors.com/it-transformation/\nType: service\n\n"
        "## How we help\n\nWe help a new CIO build a plan to mature the IT function within the first hundred days.\n", encoding="utf-8")
    (tmp_path / "_drafts").mkdir()
    (tmp_path / "_drafts" / "notes.md").write_text("# Notes\n\nThis paragraph is a private draft and must never be loaded as library text.", encoding="utf-8")
    monkeypatch.delenv("KNOWLEDGE_DEMO", raising=False)
    lib = Library.load(folder=tmp_path)  # auto: real documents exist, so no demo
    assert [p.id for p in lib.passages] == ["it-transformation#p1"]
    hit = lib.search("plan for a new CIO", k=1)[0]
    assert hit["type"] == "service" and hit["source"] == "https://www.cgsadvisors.com/it-transformation/" and not hit["synthetic"]


def test_demo_library_only_while_no_real_documents(tmp_path, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_DEMO", raising=False)
    assert all(p.synthetic for p in Library.load(folder=tmp_path).passages)  # empty folder: demo
    (tmp_path / "a.md").write_text("# A\n\nA real CGS paragraph about operating model design for mid-market firms.", encoding="utf-8")
    assert not any(p.synthetic for p in Library.load(folder=tmp_path).passages)
    monkeypatch.setenv("KNOWLEDGE_DEMO", "on")
    assert any(p.synthetic for p in Library.load(folder=tmp_path).passages)


def test_site_fetch_keeps_content_and_drops_menus():
    from api.fetch_cgs_site import page_blocks, slug, to_markdown
    html = ("<html><head><title>Innovation | CGS Advisors</title><script>x()</script></head><body>"
            "<nav><li>Home</li></nav><h1>Innovation</h1><p>We turn ideas into<br>working pilots.</p>"
            "<p>Subscribe to our newsletter</p><footer><p>Copyright</p></footer></body></html>")
    title, blocks = page_blocks(html)
    assert title == "Innovation" and blocks == [("h1", "Innovation"), ("p", "We turn ideas into working pilots."), ("p", "Subscribe to our newsletter")]
    md = to_markdown(title, "https://x/innovation/", "service", blocks, {"Subscribe to our newsletter"})
    assert md == "# Innovation\nSource: https://x/innovation/\nType: service\n\n## Innovation\n\nWe turn ideas into working pilots.\n"
    assert slug("/2026/07/29/whitepaper-7-0-cognitive/") == "07-29-whitepaper-7-0-cognitive"


def test_short_acronyms_count_but_the_pronoun_it_does_not():
    from api.knowledge import tokens
    assert tokens("AI strategy for IT and HR") == ["ai", "strategy", "it", "hr"]
    assert tokens("it is what it is, said the ai team") == ["what", "said", "ai", "team"]


CONCEPTS = {"merger": 0, "acquisition": 0, "integration": 0, "acquired": 0, "close": 1, "finance": 1, "reconciliations": 1, "pilots": 2, "scale": 2}


class FakeEmbedder:
    """Vectors over a few concepts, so 'acquired plant' is close to 'integration after a merger' without sharing a word."""
    name = "fake:concepts"

    def __init__(self, fail=False):
        self.fail, self.documents, self.queries = fail, [], []

    def embed(self, texts, kind="document"):
        if self.fail:
            raise RuntimeError("quota")
        (self.documents if kind == "document" else self.queries).extend(texts)
        out = []
        for t in texts:
            v = [0.0, 0.0, 0.0, 0.01]
            for w in t.lower().replace(".", " ").split():
                if w in CONCEPTS:
                    v[CONCEPTS[w]] += 1
            out.append(v)
        return out


def hybrid_library(tmp_path, monkeypatch, embedder):
    monkeypatch.setenv("KNOWLEDGE_SEARCH", "hybrid")
    pub = tmp_path / "cgs-public"
    pub.mkdir()
    (pub / "integration.md").write_text("# Integration\nSource: https://www.cgsadvisors.com/x/\nType: service\n\n"
                                        "We plan the integration after a merger, from day one to the combined operating model.\n", encoding="utf-8")
    (pub / "close.md").write_text("# Finance\nSource: https://www.cgsadvisors.com/y/\nType: service\n\n"
                                  "We shorten the month-end close by fixing reconciliations in finance.\n", encoding="utf-8")
    (tmp_path / "private.md").write_text("# Private\n\nA private CGS note about acquisition integration work for a named client.\n", encoding="utf-8")
    return Library.load(folder=tmp_path, embedder=embedder)


def test_hybrid_finds_by_meaning_and_keeps_private_documents_off_the_embeddings_service(tmp_path, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_EMBED_PRIVATE", raising=False)
    emb = FakeEmbedder()
    lib = hybrid_library(tmp_path, monkeypatch, emb)
    assert lib.keyword("what to do with an acquired plant") == []  # no shared word: keywords alone find nothing
    top = lib.search("what to do with an acquired plant", k=2)
    assert top[0]["id"] == "integration#p1" and "meaning" in top[0]["matched"]
    assert not any("private CGS note" in t for t in emb.documents)  # private text never sent
    assert lib.status() == {"search": "hybrid", "embeddings": "fake:concepts", "embeddable": 2}
    both = lib.search("integration after a merger", k=1)[0]
    assert both["id"] == "integration#p1" and both["matched"] == "keyword+meaning"


def test_meaning_search_failure_falls_back_to_keywords(tmp_path, monkeypatch):
    lib = hybrid_library(tmp_path, monkeypatch, FakeEmbedder(fail=True))
    top = lib.search("month-end close reconciliations", k=1)
    assert top[0]["id"] == "close#p1" and "matched" not in top[0] and "quota" in lib.status()["meaning_search_failed"]
    lib.embedder.fail = False
    assert "matched" not in lib.search("month-end close", k=1)[0]  # waits before asking the failing service again


def test_auto_uses_keywords_for_a_small_library_or_without_embeddings(tmp_path, monkeypatch):
    from api.knowledge import search_mode
    monkeypatch.setenv("KNOWLEDGE_SEARCH", "auto")
    assert search_mode(10, FakeEmbedder()) == "bm25" and search_mode(200, FakeEmbedder()) == "hybrid"
    assert search_mode(200, None) == "bm25"
    monkeypatch.setenv("KNOWLEDGE_SEARCH", "bm25")
    assert search_mode(200, FakeEmbedder()) == "bm25"


def test_embedder_caches_document_vectors_and_reads_each_provider_format(tmp_path):
    from api.embeddings import Embedder
    calls = []

    def gemini(url, headers, payload):
        calls.append((url, len(payload["requests"])))
        return {"embeddings": [{"values": [float(len(r["content"]["parts"][0]["text"])), 1.0]} for r in payload["requests"]]}

    e = Embedder("gemini", "k", transport=gemini, cache_dir=tmp_path)
    assert e.embed(["abc", "hello"]) == [[3.0, 1.0], [5.0, 1.0]]
    assert Embedder("gemini", "k", transport=gemini, cache_dir=tmp_path).embed(["hello", "new"]) == [[5.0, 1.0], [3.0, 1.0]]
    assert [n for _, n in calls] == [2, 1] and calls[0][0].endswith("gemini-embedding-001:batchEmbedContents")  # cached text not re-sent

    q = []
    def counting(url, headers, payload):
        q.append(len(payload["requests"]))
        return gemini(url, headers, payload)
    qe = Embedder("gemini", "k", transport=counting, cache_dir=tmp_path)
    assert qe.embed(["merger"], "query") == qe.embed(["merger"], "query") and q == [1]  # a repeated search is not sent again

    def openai(url, headers, payload):
        return {"data": [{"index": i, "embedding": [float(i)]} for i in reversed(range(len(payload["input"])))]}

    assert Embedder("openai", "k", transport=openai, cache_dir=tmp_path).embed(["a", "b"], "query") == [[0.0], [1.0]]


def test_embeddings_stay_off_without_a_provider_or_key(monkeypatch):
    from api.embeddings import from_env
    monkeypatch.setenv("KNOWLEDGE_EMBEDDINGS", "off")
    assert from_env() is None
    monkeypatch.setenv("KNOWLEDGE_EMBEDDINGS", "voyage")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert from_env() is None
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    assert from_env().name == "voyage:voyage-3.5-lite"


def test_a_rate_limit_waits_one_minute_not_ten(tmp_path, monkeypatch):
    from api import knowledge as K
    from api.embeddings import EmbeddingError

    class Limited(FakeEmbedder):
        def embed(self, texts, kind="document"):
            raise EmbeddingError("Embedding request failed: HTTP 429", rate_limited=True)

    lib = hybrid_library(tmp_path, monkeypatch, Limited())
    now = [1000.0]
    monkeypatch.setattr(K.time, "monotonic", lambda: now[0])
    lib.search("month-end close")
    assert lib._retry_at == 1000.0 + K.RETRY_AFTER_LIMIT and "429" in lib.status()["meaning_search_failed"]
