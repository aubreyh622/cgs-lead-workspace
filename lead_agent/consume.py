"""Local downstream contract demonstration. Does not contact anyone or write to CRM."""
import argparse
import json
import sqlite3
from pathlib import Path

from .models import Lead


def consume(records, database, *, allow_mock=False):
    leads = [Lead.model_validate(r) for r in records]
    for lead in leads:
        if lead.review.decision != "Approved for handoff" or lead.validation_issues:
            raise ValueError("The consumer accepts only approved records with no research issues.")
        if not lead.review.reviewer or not lead.review.source_support_checked:
            raise ValueError("A named source-checking reviewer is required.")
        if not allow_mock and (lead.mode != "live" or lead.approval_basis != "cgs"):
            raise ValueError("Mock or synthetic records require --allow-mock and a demonstration destination.")
        if lead.priority.score is None:
            raise ValueError("Identification priority must be present.")
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS intake(lead_id TEXT PRIMARY KEY, record TEXT NOT NULL)")
        accepted = 0
        for lead in leads:
            accepted += db.execute("INSERT OR IGNORE INTO intake VALUES(?,?)",
                                   (lead.lead_id, lead.model_dump_json())).rowcount
    return {"accepted": accepted, "duplicates_skipped": len(leads) - accepted,
            "destination": "local mock pipeline" if allow_mock else "local intake"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path)
    parser.add_argument("--database", type=Path, default=Path("data/mock_pipeline.sqlite3"))
    parser.add_argument("--allow-mock", action="store_true")
    args = parser.parse_args()
    print(json.dumps(consume(json.loads(args.records.read_text(encoding="utf-8")), args.database,
                             allow_mock=args.allow_mock)))


if __name__ == "__main__":
    main()
