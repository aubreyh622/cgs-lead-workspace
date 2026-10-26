"""P0 contracts for later integration; these do not start jobs or apply changes."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from lead_agent.models import Brief, Lead, Limits, Rubric, Seed, domain

Id = Annotated[str, Field(min_length=1, max_length=160)]
Text = Annotated[str, Field(min_length=1, max_length=4000)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)
    contract_version: Literal["1.0"] = "1.0"


class ResearchTask(Contract):
    run_id: Id
    request_id: Id
    consultant_id: Id
    knowledge_version: Id
    brief: Brief
    rubric: Rubric
    companies: list[Seed] = Field(default_factory=list, max_length=30)
    mode: Literal["company", "discovery"] = "company"
    limits: Limits
    created_at: AwareDatetime

    @model_validator(mode="after")
    def unique_companies(self):
        if self.mode == "company" and not self.companies:
            raise ValueError("Company research requires a company; discovery starts from the brief.")
        if len({s.domain for s in self.companies}) != len(self.companies):
            raise ValueError("Research a company once per task; opportunities have separate IDs.")
        return self


class OpportunityBrief(Contract):
    opportunity_id: Id
    kind: Literal["public_research"] = "public_research"
    title: Text
    team: Text | None = None
    problem_hypothesis: Text
    cgs_relevance_hypothesis: Text
    alternative_explanation: Text
    evidence_ids: list[Id] = Field(min_length=1)
    research_snapshot: Lead

    @model_validator(mode="after")
    def evidence_belongs_to_snapshot(self):
        known = {e.evidence_id for e in self.research_snapshot.research.evidence}
        if len(set(self.evidence_ids)) != len(self.evidence_ids) or not set(self.evidence_ids) <= known:
            raise ValueError("Opportunity references must be unique evidence IDs from its research snapshot.")
        return self


class ResearchFeedback(Contract):
    feedback_id: Id
    opportunity_id: Id
    consultant_id: Id
    theme_id: Id
    theme_version: Id
    outcome: Literal["worth_exploring", "research_further", "defer"]
    reason_code: Literal["relevant_signal", "evidence_gap", "service_mismatch",
                         "already_owned", "timing", "duplicate", "other"]
    scope: Literal["opportunity", "theme", "consultant", "cgs"]
    comment: str = Field(default="", max_length=4000)
    recorded_at: AwareDatetime
    review_minutes: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def explain_other(self):
        if self.reason_code == "other" and not self.comment:
            raise ValueError("Explain feedback classified as other.")
        return self


class ResearchDirectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    problem: Annotated[str, Field(min_length=20, max_length=4000)] | None = None
    signals: list[Text] | None = Field(default=None, min_length=1)
    buyer_roles: list[Text] | None = Field(default=None, min_length=1)
    exclusions: list[Text] | None = None
    excluded_domains: list[Id] | None = None

    @field_validator("excluded_domains")
    @classmethod
    def normalize_domains(cls, values):
        return sorted({domain(v) for v in values}) if values is not None else None

    @model_validator(mode="after")
    def nonempty_patch(self):
        if not any(getattr(self, k) is not None for k in type(self).model_fields):
            raise ValueError("Propose at least one explicit research-direction change.")
        return self


class ThemeChangeProposal(Contract):
    proposal_id: Id
    theme_id: Id
    base_theme_version: Id
    consultant_id: Id
    scope: Literal["theme", "consultant", "cgs"]
    feedback_ids: list[Id] = Field(min_length=1)
    patch: ResearchDirectionPatch
    rationale: Text
    status: Literal["pending_review", "approved", "rejected"] = "pending_review"
    reviewer: Id | None = None
    reviewed_at: AwareDatetime | None = None
    review_reason: str = Field(default="", max_length=4000)
    resulting_theme_version: Id | None = None

    @model_validator(mode="after")
    def explicit_review(self):
        if len(set(self.feedback_ids)) != len(self.feedback_ids):
            raise ValueError("Reference each feedback record once.")
        if self.status == "pending_review":
            if self.reviewer or self.reviewed_at or self.resulting_theme_version or self.review_reason:
                raise ValueError("A pending proposal cannot contain a completed review.")
        else:
            if not self.reviewer or not self.reviewed_at:
                raise ValueError("A decision requires a reviewer and review time.")
            if self.status == "approved":
                if not self.resulting_theme_version or self.resulting_theme_version == self.base_theme_version:
                    raise ValueError("Approval must identify a new theme version.")
            elif not self.review_reason or self.resulting_theme_version is not None:
                raise ValueError("Rejection requires a reason and cannot create a theme version.")
        return self


class HandoffReceipt(Contract):
    handoff_id: Id
    opportunity_id: Id
    opportunity_revision: int = Field(ge=1)
    status: Literal["pending_acceptance", "accepted", "needs_research", "rejected"] = "pending_acceptance"
    case_id: Id | None = None
    reviewer: Id | None = None
    decided_at: AwareDatetime | None = None
    reason: str = Field(default="", max_length=4000)
    owner_id: Id | None = None
    next_action: Text | None = None
    due_date: date | None = None
    initial_contact_status: Literal["not_contacted"] = "not_contacted"

    @model_validator(mode="after")
    def acceptance_is_explicit(self):
        if self.status == "pending_acceptance":
            if self.case_id or self.reviewer or self.decided_at:
                raise ValueError("A pending handoff cannot claim acceptance.")
        else:
            if not self.reviewer or not self.decided_at:
                raise ValueError("A handoff decision requires a reviewer and decision time.")
            if self.status == "accepted":
                if not all((self.case_id, self.owner_id, self.next_action, self.due_date)):
                    raise ValueError("Acceptance needs a case, owner, next action and due date.")
            elif self.case_id is not None or not self.reason:
                raise ValueError("A returned/rejected handoff needs a reason and no new case.")
        return self
