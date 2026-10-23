from datetime import date

import pytest

from lead_agent.assessment import make_lead
from lead_agent.demo import fixture
from lead_agent.evidence import bind_passages, passage_bank
from lead_agent.models import Research


def test_code_binds_exact_source_text_and_overrides_model_authored_quotes(brief, rubric):
    research, sources = fixture()
    bank = passage_bank([s.model_dump(mode="json") for s in sources])
    record = research.model_dump(mode="json")
    for observation in record["evidence"]:
        passage = next(key for key, value in bank.items() if observation["excerpt"] in value["excerpt"])
        observation.update(quote_id=passage, excerpt="A fabricated model-authored quote", source_id="invented")
    bound = Research.model_validate(bind_passages(record, bank))
    card = make_lead("run", bound, sources, brief, rubric, date(2026, 10, 20))
    assert not card.validation_issues
    assert all(e.excerpt in next(s.content for s in sources if s.source_id == e.source_id) for e in bound.evidence)
    assert bound.components["problem"].evidence_ids == ["e4"]


def test_unknown_passage_id_cannot_supply_its_own_quote():
    with pytest.raises(ValueError, match="quote_id"):
        bind_passages({"evidence": [{"quote_id": "invented", "excerpt": "Do not accept this quote"}]}, {})


def test_passage_chunks_are_contiguous_and_keep_unicode():
    text = ("A company’s manufacturing strategy includes AI and digital capabilities.\n\n" * 120) + "© Company Corporation"
    bank = passage_bank([{"source_id": "source", "content": text}])
    assert len(bank) > 1
    assert all(15 <= len(q["excerpt"]) <= 900 and q["excerpt"] in text for q in bank.values())
    assert any("© Company Corporation" in q["excerpt"] for q in bank.values())
