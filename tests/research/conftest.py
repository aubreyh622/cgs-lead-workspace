import json
from datetime import date
from pathlib import Path

import pytest

from lead_agent.models import Brief, Rubric
from lead_agent.storage import Store

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def brief():
    return Brief.model_validate_json((ROOT / "config/problem_brief.json").read_text())


@pytest.fixture
def rubric():
    return Rubric.model_validate_json((ROOT / "config/rubric.json").read_text())


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "pilot.sqlite3")
