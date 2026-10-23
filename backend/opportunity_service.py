"""P2 opportunity snapshots and append-only human research feedback."""
from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import uuid4

from pydantic import Field

from .contracts import Contract, Id, Text, OpportunityBrief, ResearchFeedback
from .research_service import ResearchNotFound, ResearchServiceError
from lead_agent.models import now


class CreateOpportunity(Contract):
    request_id: Id
    run_id: Id
    lead_id: Id
    title: Text
    team: Text | None = None
    problem_hypothesis: Text
    cgs_relevance_hypothesis: Text
    alternative_explanation: Text
    evidence_ids: list[Id] = Field(min_length=1, max_length=30)


class ReviewOpportunity(Contract):
    request_id: Id
    revision: int = Field(ge=1, strict=True)
    consultant_id: Id
    outcome: Literal["worth_exploring", "research_further", "defer"]
    reason_code: Literal["relevant_signal", "evidence_gap", "service_mismatch",
                         "already_owned", "timing", "duplicate", "other"]
    scope: Literal["opportunity", "theme", "consultant", "cgs"]
    comment: str = Field(default="", max_length=4000)
    review_minutes: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    source_support_checked: bool = Field(default=False, strict=True)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class OpportunityService:
    def __init__(self, research):
        self.research, self.store = research, research.store
        self.handoff_service = None
        with self.store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workspace_opportunities(
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, lead_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, record TEXT NOT NULL, created_at TEXT NOT NULL,
                    request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspace_feedback(
                    id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL, record TEXT NOT NULL,
                    source_support_checked INTEGER NOT NULL,
                    request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL);
            """)

    @staticmethod
    def _replay(db, table, request_id, digest):
        # Table names are constants in this module, never request fields.
        row = db.execute(f"SELECT * FROM {table} WHERE request_id=?", (request_id,)).fetchone()
        if row and row["fingerprint"] != digest:
            raise ResearchServiceError("This request_id was already used for different input.")
        return row

    def _record(self, db, opportunity_id):
        row = db.execute("SELECT * FROM workspace_opportunities WHERE id=?", (opportunity_id,)).fetchone()
        if row is None:
            raise ResearchNotFound("Opportunity not found.")
        history = []
        for item in db.execute("SELECT * FROM workspace_feedback WHERE opportunity_id=? ORDER BY rowid",
                               (opportunity_id,)):
            history.append({"feedback": json.loads(item["record"]),
                            "source_support_checked": bool(item["source_support_checked"])})
        handoff = self.handoff_service.summary(opportunity_id) if self.handoff_service else None
        return {"opportunity": json.loads(row["record"]), "revision": row["revision"],
                "created_at": row["created_at"], "feedback_history": history,
                "latest_feedback": history[-1] if history else None,
                "qualification_handoff": handoff["status"] if handoff else "not_requested" if self.handoff_service else "not_implemented",
                "handoff": handoff}

    def get(self, opportunity_id):
        with self.store.db() as db:
            return self._record(db, opportunity_id)

    def list(self):
        with self.store.db() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM workspace_opportunities ORDER BY rowid DESC")]
            return [self._record(db, key) for key in ids]

    def create(self, body):
        request = CreateOpportunity.model_validate(body)
        digest = fingerprint(request.model_dump(exclude={"request_id"}))
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            replay = self._replay(db, "workspace_opportunities", request.request_id, digest)
            if replay:
                return self._record(db, replay["id"]), False
            self.store.task(request.run_id)  # Reject unmanaged historical research.
            row = db.execute("SELECT status FROM runs WHERE id=?", (request.run_id,)).fetchone()
            if row["status"] in ("pending", "running"):
                raise ResearchServiceError("Wait until research stops before saving an opportunity snapshot.")
            lead = next((x for x in self.store.leads(request.run_id) if x.lead_id == request.lead_id), None)
            if lead is None:
                raise ResearchNotFound("Company research card not found in this run.")
            opportunity = OpportunityBrief(opportunity_id=str(uuid4()), research_snapshot=lead,
                **request.model_dump(exclude={"contract_version", "request_id", "run_id", "lead_id"}))
            db.execute("INSERT INTO workspace_opportunities VALUES(?,?,?,?,?,?,?,?)",
                (opportunity.opportunity_id, request.run_id, request.lead_id, 1,
                 opportunity.model_dump_json(), now(), request.request_id, digest))
            return self._record(db, opportunity.opportunity_id), True

    def review(self, opportunity_id, body):
        request = ReviewOpportunity.model_validate(body)
        digest = fingerprint({"opportunity_id": opportunity_id,
                              **request.model_dump(exclude={"request_id"})})
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            replay = self._replay(db, "workspace_feedback", request.request_id, digest)
            if replay:
                return self._record(db, opportunity_id), False
            current = self._record(db, opportunity_id)
            if current["qualification_handoff"] == "accepted":
                raise ResearchServiceError("This opportunity has been accepted. Preserve its snapshot; start separate research for a new opportunity.")
            if request.revision != current["revision"]:
                raise ResearchServiceError("This opportunity has newer feedback. Refresh before reviewing again.")
            if request.outcome == "worth_exploring" and not request.source_support_checked:
                raise ValueError("Check the cited sources before marking an opportunity worth exploring.")
            snapshot = current["opportunity"]["research_snapshot"]
            feedback = ResearchFeedback(feedback_id=str(uuid4()), opportunity_id=opportunity_id,
                theme_id=snapshot["problem_brief_id"], theme_version=snapshot["problem_brief_version"],
                recorded_at=now(), **request.model_dump(exclude={"contract_version", "request_id",
                                                               "revision", "source_support_checked"}))
            db.execute("INSERT INTO workspace_feedback VALUES(?,?,?,?,?,?)",
                (feedback.feedback_id, opportunity_id, feedback.model_dump_json(),
                 int(request.source_support_checked), request.request_id, digest))
            db.execute("UPDATE workspace_opportunities SET revision=revision+1 WHERE id=?", (opportunity_id,))
            return self._record(db, opportunity_id), True
