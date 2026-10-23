from datetime import datetime, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lead_agent.consume import consume
from lead_agent.demo import load_demo
from lead_agent.models import Review
from lead_agent.public_examples import load_public_examples


def test_25_record_pilot_and_mock_consumer(store, brief, rubric, tmp_path):
    run = load_demo(store, brief, rubric, 25)
    leads = store.leads(run)
    assert len(leads) == 25
    assert sum(l.review.decision == "Needs research" for l in leads) == 10
    for lead in leads:
        if lead.priority.band == "high":
            store.review(run, lead.lead_id, Review(decision="Approved for handoff", reviewer="Mock reviewer",
                         reviewed_at=datetime.now(timezone.utc), source_support_checked=True))
    records = store.handoff(run)
    assert len(records) == 5
    with pytest.raises(ValueError, match="allow-mock"):
        consume(records, tmp_path / "rejected.sqlite3")
    first = consume(records, tmp_path / "mock.sqlite3", allow_mock=True)
    second = consume(records, tmp_path / "mock.sqlite3", allow_mock=True)
    assert first["accepted"] == 5 and second["duplicates_skipped"] == 5


def test_public_examples_are_separate_and_incomplete(store, brief, rubric):
    run = load_public_examples(store, brief, rubric)
    leads = store.leads(run)
    assert len(leads) == 5
    assert all(l.research_method == "manual_public_web" for l in leads)
    assert all(l.review.decision == "Needs research" for l in leads)
    assert all(not any("excerpt is not" in e for e in l.validation_issues) for l in leads)
    assert store.handoff(run) == []


def test_app_loads_and_reviews_synthetic_cards(monkeypatch, store):
    import lead_agent.storage
    monkeypatch.setattr(lead_agent.storage, "Store", lambda _: store)
    from lead_agent.models import Brief, Rubric
    root = Path(__file__).resolve().parents[2]
    load_demo(store, Brief.model_validate_json((root / "config/problem_brief.json").read_text()),
              Rubric.model_validate_json((root / "config/rubric.json").read_text()))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "app.py"), default_timeout=20).run()
    assert not app.exception
    assert not app.selectbox  # Samples are hidden on the initial screen.
    next(b for b in app.checkbox if b.label == "Show historical and synthetic runs").check().run()
    assert not app.exception
    assert next(s for s in app.selectbox if s.label == "Company to inspect")
    next(b for b in app.button if b.label == "Prepare approved JSON handoff").click().run()
    assert not app.exception
