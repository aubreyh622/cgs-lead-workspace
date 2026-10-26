"""The prototype (JavaScript) and the backend (Python) must calculate identical results.

The fixture holds the prototype's calc() output for all 14 sample opportunities.
Northstar is compared on its submitted revenue (the pack snapshot), as in the prototype seed.
"""
import json
from pathlib import Path

from backend.rules import calculate
from api.seed import SEED

READINESS = {"ready": "Ready", "more": "More evidence needed", "notready": "Not ready"}


def test_backend_matches_prototype_for_every_sample():
    expected = dict(json.loads((Path(__file__).parent / "fixtures/prototype_rules_results.json").read_text()))
    leads = {l["id"]: l for l in json.loads(SEED.read_text(encoding="utf-8"))["leads"]}
    assert len(expected) == 14
    for oid, fe in expected.items():
        L = leads[oid]
        be = calculate({"scores": L["scores"], "conditions": L["conditions"], "blocker": L["blocker"], "revenue": L["revenue"]})
        for k in ("ql", "qu", "pl", "pu", "coverage"):
            assert abs(fe[k] - be[k]) < 1e-6, (oid, k)
        assert READINESS[fe["readiness"]] == be["readiness"], oid
        assert fe["priority"] == be["priority"], oid
        assert [g["key"] for g in fe["gaps"]] == [g["key"] for g in be["gaps"]], oid
