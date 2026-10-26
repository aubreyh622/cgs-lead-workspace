"""P3: explicit acceptance, with case and receipt committed in one qualification DB."""
from __future__ import annotations

from datetime import date
import json
from typing import Literal
from uuid import uuid4

from pydantic import Field

from .contracts import Contract, HandoffReceipt, Id, Text
from .opportunity_service import fingerprint
from .research_service import ResearchNotFound, ResearchServiceError
from backend.rules import CONDITIONS, WEIGHTS
from server import iso_now, uid


class SubmitHandoff(Contract):
    request_id: Id
    revision: int = Field(ge=1, strict=True)


class DecideHandoff(Contract):
    request_id: Id
    opportunity_revision: int = Field(ge=1, strict=True)
    status: Literal["accepted", "needs_research", "rejected"]
    reviewer: Id
    reason: str = Field(default="", max_length=4000)
    owner_id: Id | None = None
    next_action: Text | None = None
    due_date: date | None = None
    relationship_checked: bool = Field(default=False, strict=True)
    relationship_note: str = Field(default="", max_length=4000)
    contact_index: int | None = Field(default=None, ge=0, strict=True)


class ConfirmContact(Contract):
    revision: int = Field(ge=0, strict=True)
    contact: str = Field(min_length=1, max_length=300)
    role: str = Field(min_length=1, max_length=300)
    source_rationale: Text


class RecordContacted(Contract):
    revision: int = Field(ge=0, strict=True)
    contacted_on: date
    note: Text


class HandoffService:
    def __init__(self, opportunities, qualification):
        self.opportunities, self.qualification = opportunities, qualification
        self.research = opportunities.store
        with qualification.lock, qualification.db as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workspace_handoffs(
                    id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL, status TEXT NOT NULL,
                    receipt TEXT NOT NULL, snapshot TEXT NOT NULL, submitted_at TEXT NOT NULL,
                    acceptance_context TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS workspace_open_handoff
                    ON workspace_handoffs(opportunity_id) WHERE status IN ('pending_acceptance','accepted');
                CREATE TABLE IF NOT EXISTS workspace_handoff_requests(
                    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, handoff_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspace_handoff_cases(
                    opportunity_id TEXT PRIMARY KEY, case_id TEXT UNIQUE NOT NULL, handoff_id TEXT UNIQUE NOT NULL);
            """)
        opportunities.handoff_service = self

    def _row(self, db, handoff_id):
        row = db.execute("SELECT * FROM workspace_handoffs WHERE id=?", (handoff_id,)).fetchone()
        if row is None:
            raise ResearchNotFound("Handoff not found.")
        return row

    def _view(self, db, row):
        receipt = json.loads(row["receipt"])
        snapshot = json.loads(row["snapshot"])
        company = snapshot["opportunity"]["research_snapshot"]["research"]
        duplicates = []
        for item in db.execute("SELECT data FROM cases"):
            case = json.loads(item[0])
            context = case.get("researchContext", {}).get("opportunity", {}).get("research_snapshot", {}).get("research", {})
            if case["company"].casefold().strip() == company["company"].casefold().strip() or context.get("domain") == company["domain"]:
                duplicates.append({"case_id": case["id"], "company": case["company"], "project": case["project"],
                    "owner": case["owner"], "stage": case["stage"], "opportunity_id": case.get("sourceOpportunityId")})
        return {"receipt": receipt, "snapshot": snapshot, "submitted_at": row["submitted_at"],
                "acceptance_context": json.loads(row["acceptance_context"]) if row["acceptance_context"] else None,
                "duplicate_candidates": duplicates,
                "case_available": bool(receipt["case_id"] and db.execute("SELECT 1 FROM cases WHERE id=?", (receipt["case_id"],)).fetchone())}

    def get(self, handoff_id):
        with self.qualification.lock:
            return self._view(self.qualification.db, self._row(self.qualification.db, handoff_id))

    def summary(self, opportunity_id):
        with self.qualification.lock:
            db = self.qualification.db
            row = db.execute("SELECT * FROM workspace_handoffs WHERE opportunity_id=? ORDER BY rowid DESC LIMIT 1", (opportunity_id,)).fetchone()
            if row is None:
                return None
            receipt = json.loads(row["receipt"])
            receipt["case_available"] = bool(receipt["case_id"] and db.execute("SELECT 1 FROM cases WHERE id=?", (receipt["case_id"],)).fetchone())
            return receipt

    def list(self):
        with self.qualification.lock:
            db = self.qualification.db
            return [self._view(db, row) for row in db.execute("SELECT * FROM workspace_handoffs ORDER BY rowid DESC").fetchall()]

    def _replay(self, db, request_id, digest):
        row = db.execute("SELECT * FROM workspace_handoff_requests WHERE request_id=?", (request_id,)).fetchone()
        if row and row["fingerprint"] != digest:
            raise ResearchServiceError("This request_id was already used for different handoff input.")
        return self._view(db, self._row(db, row["handoff_id"])) if row else None

    @staticmethod
    def _remember(db, request_id, digest, handoff_id):
        db.execute("INSERT INTO workspace_handoff_requests VALUES(?,?,?)", (request_id, digest, handoff_id))

    @staticmethod
    def _eligible(current):
        latest = current["latest_feedback"]
        if not latest or latest["feedback"]["outcome"] != "worth_exploring" or not latest["source_support_checked"]:
            raise ResearchServiceError("The latest research review must mark this worth exploring with sources checked.")

    def submit(self, opportunity_id, body):
        request = SubmitHandoff.model_validate(body)
        digest = fingerprint({"operation": "submit", "opportunity_id": opportunity_id, **request.model_dump()})
        # Lock research first, then qualification. Research is read-only in this transaction;
        # all durable handoff changes live in the qualification transaction below.
        with self.research.db() as research_db:
            research_db.execute("BEGIN IMMEDIATE")
            with self.qualification.lock, self.qualification.db as db:
                db.execute("BEGIN IMMEDIATE")
                replay = self._replay(db, request.request_id, digest)
                if replay:
                    return replay, False
                prior = db.execute("SELECT * FROM workspace_handoffs WHERE opportunity_id=? ORDER BY rowid DESC LIMIT 1", (opportunity_id,)).fetchone()
                if prior and prior["status"] == "accepted":
                    self._remember(db, request.request_id, digest, prior["id"])
                    return self._view(db, prior), False
                current = self.opportunities._record(research_db, opportunity_id)
                if current["revision"] != request.revision:
                    raise ResearchServiceError("This opportunity changed. Refresh before submitting it.")
                self._eligible(current)
                if prior:
                    previous = HandoffReceipt.model_validate_json(prior["receipt"])
                    if prior["status"] == "pending_acceptance":
                        if previous.opportunity_revision != request.revision:
                            raise ResearchServiceError("Resolve the earlier pending handoff before resubmitting this revision.")
                        self._remember(db, request.request_id, digest, prior["id"])
                        return self._view(db, prior), False
                    if request.revision <= previous.opportunity_revision:
                        raise ResearchServiceError("Record a new research review before resubmitting a returned or rejected handoff.")
                receipt = HandoffReceipt(handoff_id=str(uuid4()), opportunity_id=opportunity_id,
                                         opportunity_revision=request.revision)
                snapshot = {k: current[k] for k in ("opportunity", "revision", "created_at", "feedback_history", "latest_feedback")}
                db.execute("INSERT INTO workspace_handoffs VALUES(?,?,?,?,?,?,NULL)",
                    (receipt.handoff_id, opportunity_id, receipt.status, receipt.model_dump_json(), json.dumps(snapshot), iso_now()))
                self._remember(db, request.request_id, digest, receipt.handoff_id)
                self.qualification.audit("", "Research handoff submitted", {"handoff_id": receipt.handoff_id, "opportunity_id": opportunity_id})
                return self._view(db, self._row(db, receipt.handoff_id)), True

    def _create_case(self, db, receipt, snapshot, context, person):
        opportunity = snapshot["opportunity"]
        lead = opportunity["research_snapshot"]
        today = date.today().isoformat()
        case = {"id": receipt.case_id, "company": lead["research"]["company"], "project": opportunity["title"],
            "contact": person["name"] if person else None, "role": person["role"] if person else "Unknown",
            "owner": receipt.owner_id, "industry": "Pending confirmation", "scores": {k: None for k in WEIGHTS},
            "conditions": {k: None for k in CONDITIONS}, "revenue": None,
            "problem": "Pending client confirmation. Public research hypotheses are recorded separately.",
            "stage": "Not contacted", "contactStatus": "not_contacted", "blocker": None,
            "lastProgress": today, "lastReply": None, "tasks": [{"id": uid("t"), "title": receipt.next_action,
                "assignee": receipt.owner_id, "due": receipt.due_date.isoformat(), "side": "CGS", "status": "Open", "createdAt": iso_now()}],
            "evidence": [], "events": [{"id": uid("ev"), "date": today, "at": iso_now(), "title": "Research handoff accepted",
                "detail": "Owner and next action assigned. Public research remains unconfirmed qualification context."}],
            "versions": [], "approval": None, "sample": lead["mode"] == "synthetic", "revision": 1,
            "sourceOpportunityId": receipt.opportunity_id, "researchContext": snapshot,
            "researchHandoff": {"receipt": receipt.model_dump(mode="json"), "acceptance_context": context}}
        if person:
            case["contactProvenance"] = {"kind": "public_research", "person": person, "reviewer": receipt.reviewer,
                                         "recorded_at": iso_now()}
        self.qualification.put(case)
        db.execute("INSERT INTO workspace_handoff_cases VALUES(?,?,?)", (receipt.opportunity_id, case["id"], receipt.handoff_id))
        self.qualification.audit(case["id"], "Research handoff accepted", {"handoff_id": receipt.handoff_id,
            "opportunity_id": receipt.opportunity_id, "qualification_scores_changed": False})

    def decide(self, handoff_id, body):
        request = DecideHandoff.model_validate(body)
        digest = fingerprint({"operation": "decision", "handoff_id": handoff_id,
                              **request.model_dump(mode="json")})
        with self.research.db() as research_db:
            research_db.execute("BEGIN IMMEDIATE")
            with self.qualification.lock, self.qualification.db as db:
                db.execute("BEGIN IMMEDIATE")
                replay = self._replay(db, request.request_id, digest)
                if replay:
                    return replay, False
                row = self._row(db, handoff_id)
                prior = HandoffReceipt.model_validate_json(row["receipt"])
                if request.opportunity_revision != prior.opportunity_revision:
                    raise ResearchServiceError("The handoff revision does not match the review.")
                if prior.status != "pending_acceptance":
                    if prior.status == "accepted" and request.status == "accepted":
                        self._remember(db, request.request_id, digest, handoff_id)
                        return self._view(db, row), False
                    raise ResearchServiceError("This handoff already has a decision. Refresh its receipt.")
                case_id, context, person = None, None, None
                if request.status == "accepted":
                    current = self.opportunities._record(research_db, prior.opportunity_id)
                    if current["revision"] != prior.opportunity_revision:
                        raise ResearchServiceError("Research feedback changed. Return this handoff for research and submit the new revision.")
                    self._eligible(current)
                    if not request.relationship_checked or not request.relationship_note:
                        raise ValueError("Review existing relationships and duplicate candidates, and record your conclusion.")
                    if request.due_date is None or request.due_date < date.today():
                        raise ValueError("Acceptance needs a next-action date today or later (system date).")
                    case_id = uid("case")
                    snapshot = json.loads(row["snapshot"])
                    people = snapshot["opportunity"]["research_snapshot"]["research"]["people"] or []
                    if request.contact_index is not None:
                        if request.contact_index >= len(people):
                            raise ValueError("Select a contact from the saved public research or leave it unknown.")
                        person = people[request.contact_index]
                    context = {"relationship_checked": True, "relationship_note": request.relationship_note,
                               "selected_contact": person, "reviewer": request.reviewer, "recorded_at": iso_now()}
                receipt = HandoffReceipt(handoff_id=handoff_id, opportunity_id=prior.opportunity_id,
                    opportunity_revision=prior.opportunity_revision, status=request.status, case_id=case_id,
                    reviewer=request.reviewer, decided_at=iso_now(), reason=request.reason,
                    owner_id=request.owner_id if case_id else None, next_action=request.next_action if case_id else None,
                    due_date=request.due_date if case_id else None)
                if case_id:
                    self._create_case(db, receipt, snapshot, context, person)
                db.execute("UPDATE workspace_handoffs SET status=?,receipt=?,acceptance_context=? WHERE id=?",
                    (receipt.status, receipt.model_dump_json(), json.dumps(context) if context else None, handoff_id))
                self._remember(db, request.request_id, digest, handoff_id)
                self.qualification.audit(case_id or "", "Handoff decision: " + receipt.status,
                                         {"handoff_id": handoff_id, "reviewer": request.reviewer, "reason": request.reason})
                return self._view(db, self._row(db, handoff_id)), True

    def confirm_contact(self, case_id, body):
        request = ConfirmContact.model_validate(body)
        with self.qualification.lock:
            case = self.qualification.get(case_id)
            if not case.get("researchHandoff"):
                raise ValueError("This contact workflow is for cases received from research.")
            self.qualification.check_revision(case, request.model_dump())
            case.update(contact=request.contact, role=request.role, contactProvenance={"kind": "human_confirmation",
                "reviewer": case["owner"], "source_rationale": request.source_rationale, "recorded_at": iso_now()})
            case.pop("outreach", None)  # An existing draft may address the previous contact.
            self.qualification.record(case, "Contact confirmed by consultant", request.source_rationale)
            return case

    def record_contacted(self, case_id, body):
        request = RecordContacted.model_validate(body)
        with self.qualification.lock:
            case = self.qualification.get(case_id)
            self.qualification.check_revision(case, request.model_dump())
            if not case.get("researchHandoff") or case["stage"] != "Not contacted":
                raise ResearchServiceError("Only a not-contacted research case can record first outreach here.")
            if not case.get("contact"):
                raise ValueError("Confirm a contact before recording outreach.")
            if request.contacted_on > date.today():
                raise ValueError("Outreach cannot be recorded in the future.")
            case.update(stage="Contacted", contactStatus="contacted",
                        firstContact={"date": request.contacted_on.isoformat(), "note": request.note})
            self.qualification.record(case, "Human recorded first outreach", request.note)
            return case

    def guard_reset(self):
        with self.qualification.lock:
            if self.qualification.db.execute("SELECT 1 FROM workspace_handoff_cases LIMIT 1").fetchone():
                raise ResearchServiceError("Reset is disabled while accepted research handoffs exist; it would remove their linked cases.")
