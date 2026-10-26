from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .models import Lead, Review, now


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs(
                  id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL,
                  stop INTEGER DEFAULT 0, lease REAL DEFAULT 0, token TEXT, error TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS leads(
                  run_id TEXT, lead_id TEXT, record TEXT NOT NULL,
                  PRIMARY KEY(run_id, lead_id));
                CREATE TABLE IF NOT EXISTS reviews(
                  id INTEGER PRIMARY KEY, run_id TEXT, lead_id TEXT, record TEXT);
                CREATE TABLE IF NOT EXISTS research_revisions(
                  id INTEGER PRIMARY KEY, run_id TEXT, lead_id TEXT, record TEXT, replaced_at TEXT);
                CREATE TABLE IF NOT EXISTS handoffs(
                  lead_id TEXT PRIMARY KEY, run_id TEXT, record TEXT NOT NULL, created_at TEXT);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_run(self, payload):
        with self.db() as db:
            return self._create_run(db, payload)

    def _create_run(self, db, payload):
        """Allow the integration to save a run and its request record atomically."""
        run_id = str(uuid4())
        payload = {**payload, "created_at": now(), "messages": [], "sources": [],
                   "urls": [], "queries": [], "counters": {"searches": 0, "extractions": 0, "model_calls": 0},
                   "reserved_credits": 0, "reported_credits": 0, "usage": {}, "active_seconds": 0,
                   "events": [], "seed_index": 0}
        db.execute("INSERT INTO runs(id,status,payload) VALUES(?,?,?)",
                   (run_id, "pending", json.dumps(payload)))
        return run_id

    def run(self, run_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown run.")
        return {**dict(row), "payload": json.loads(row["payload"])}

    def runs(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT id,status,error,lease FROM runs ORDER BY rowid DESC")]

    def claim(self, run_id):
        token = str(uuid4())
        with self.db() as db:
            changed = db.execute("""UPDATE runs SET token=?,lease=?,status='running',stop=0,error=''
                WHERE id=? AND lease<? AND status IN ('pending','paused','failed','running')""",
                                 (token, time.time() + 120, run_id, time.time())).rowcount
            if not changed:
                raise ValueError("Run is active or has reached a terminal limit.")
        return token

    def save(self, run_id, payload, token):
        with self.db() as db:
            if not db.execute("UPDATE runs SET payload=?,lease=? WHERE id=? AND token=?",
                              (json.dumps(payload), time.time() + 120, run_id, token)).rowcount:
                raise ValueError("Worker lease was replaced.")

    def finish(self, run_id, status, error, token):
        with self.db() as db:
            db.execute("UPDATE runs SET status=?,error=?,lease=0,token=NULL WHERE id=? AND token=?",
                       (status, error, run_id, token))

    def stop(self, run_id):
        with self.db() as db:
            db.execute("UPDATE runs SET stop=1 WHERE id=? AND status IN ('pending','running')", (run_id,))

    def leads(self, run_id):
        with self.db() as db:
            return [Lead.model_validate_json(r[0]) for r in db.execute(
                "SELECT record FROM leads WHERE run_id=? ORDER BY rowid", (run_id,))]

    def add_lead(self, lead):
        with self.db() as db:
            return bool(db.execute("INSERT OR IGNORE INTO leads VALUES(?,?,?)",
                                   (lead.run_id, lead.lead_id, lead.model_dump_json())).rowcount)

    def save_research(self, lead):
        """Enrichment resets the decision; prior research and review audit remain saved."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT record FROM leads WHERE run_id=? AND lead_id=?",
                                  (lead.run_id, lead.lead_id)).fetchone()
            if previous is None:
                db.execute("INSERT INTO leads VALUES(?,?,?)",
                           (lead.run_id, lead.lead_id, lead.model_dump_json()))
                return "created"
            old = Lead.model_validate_json(previous[0])
            if old.research == lead.research and old.sources == lead.sources:
                return "unchanged"
            if db.execute("SELECT 1 FROM handoffs WHERE lead_id=?", (lead.lead_id,)).fetchone():
                raise ValueError("An exported lead is immutable. Start a separate research run for a proposed correction.")
            db.execute("INSERT INTO research_revisions(run_id,lead_id,record,replaced_at) VALUES(?,?,?,?)",
                       (lead.run_id, lead.lead_id, previous[0], now()))
            db.execute("UPDATE leads SET record=? WHERE run_id=? AND lead_id=?",
                       (lead.model_dump_json(), lead.run_id, lead.lead_id))
            return "updated"

    def review(self, run_id, lead_id, review: Review):
        if not review.reviewer or not review.reviewer.strip() or not review.reviewed_at:
            raise ValueError("A named reviewer and timestamp are required.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            lead = Lead.model_validate_json(db.execute(
                "SELECT record FROM leads WHERE run_id=? AND lead_id=?", (run_id, lead_id)).fetchone()[0])
            run = json.loads(db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()[0])
            if review.decision == "Approved for handoff":
                if lead.mode == "synthetic" and lead.approval_basis != "mock":
                    raise ValueError("Synthetic records require explicitly mock approval settings.")
                if lead.validation_issues or lead.priority.score is None:
                    raise ValueError("Resolve the research issues before approval.")
                if lead.research.eligibility != "eligible" or any(
                        not any(e.category == c for e in lead.research.evidence) for c in ("industry", "geography")):
                    raise ValueError("Confirm industry and geography eligibility before approving handoff.")
                for config in (run["brief"], run["rubric"]):
                    if not config.get("approved_by") or not config.get("approved_at") or config.get("approval_basis") == "pending":
                        raise ValueError("The brief and rubric must have approval recorded before handoff.")
                if not run["brief"].get("pipeline_owner"):
                    raise ValueError("Confirm the pipeline owner before handoff.")
                if not review.source_support_checked:
                    raise ValueError("Review the material claims against their excerpts before approving.")
                if lead.research.people and not review.role_accuracy_checked:
                    raise ValueError("Check current roles before approving a record containing people.")
            if db.execute("SELECT 1 FROM handoffs WHERE lead_id=?", (lead_id,)).fetchone():
                raise ValueError("This lead already has an immutable handoff. Coordinate any correction with its consumer.")
            lead.review = review
            db.execute("UPDATE leads SET record=? WHERE run_id=? AND lead_id=?",
                       (lead.model_dump_json(), run_id, lead_id))
            db.execute("INSERT INTO reviews(run_id,lead_id,record) VALUES(?,?,?)",
                       (run_id, lead_id, review.model_dump_json()))
        return lead

    def handoff(self, run_id):
        """Create an immutable local outbox; retries return the same saved records.

        Nothing is sent to another service. A consumer deduplicates on lead_id.
        """
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT record FROM leads WHERE run_id=?", (run_id,)).fetchall()
            for row in rows:
                lead = Lead.model_validate_json(row[0])
                if lead.review.decision == "Approved for handoff":
                    db.execute("INSERT OR IGNORE INTO handoffs VALUES(?,?,?,?)",
                               (lead.lead_id, run_id, lead.model_dump_json(), now()))
            return [json.loads(r[0]) for r in db.execute(
                "SELECT record FROM handoffs WHERE run_id=? ORDER BY lead_id", (run_id,))]
