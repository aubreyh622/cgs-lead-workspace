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
