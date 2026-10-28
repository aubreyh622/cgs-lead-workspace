"""The workspace ledger: one table per kind of record.

Plain-language map (see docs/DATABASE.md for the full version):
  companies, contacts          who we might work with
  opportunities                one possible piece of work at one company (a company can have several)
  research_briefs, research_tasks   Steps 2-3 output and progress
  candidates                   Step 1 recommendations before anyone picks them
  outreach, notes              Step 5 messages and Step 6 conversations
  proposals, evidence          AI suggestions, and what a person confirmed
  tasks, suggestions           agreed actions, and AI-suggested actions not yet agreed
  packs, decisions             Step 7 qualification packs and Step 4 / Step 8 human decisions
  feedback, direction_versions, direction_proposals   the learning loop
  agent_runs, activity         every AI call, and an append-only history of every change
  agent_jobs, inbox_items      the agents' task board, and what they prepared for people

Unknown scores and conditions are stored as null (pending), never as zero.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    domain: Mapped[str] = mapped_column(String(200), default="", index=True)
    industry: Mapped[str] = mapped_column(String(200), default="")
    hq: Mapped[str] = mapped_column(String(200), default="")
    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="company")


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[Optional[str]] = mapped_column(String(200))          # null = contact to confirm
    title: Mapped[str] = mapped_column(String(200), default="Contact to confirm")
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(100))
    confirmed_on: Mapped[Optional[date]] = mapped_column(Date)
    confirmed_note: Mapped[Optional[str]] = mapped_column(Text)        # where the name and role were verified


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    contact_id: Mapped[Optional[str]] = mapped_column(ForeignKey("contacts.id"))
    title: Mapped[str] = mapped_column(String(300))
    team: Mapped[str] = mapped_column(String(200), default="")
    # researching | review | identified | contacted | active | approval | qualification | nurture | rejected | closed
    stage: Mapped[str] = mapped_column(String(30), index=True)
    owner: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    rating: Mapped[int] = mapped_column(Integer, default=3)            # 1-5 lead rating from Step 4
    source_type: Mapped[str] = mapped_column(String(20), default="ai")  # ai | import | manual
    source_note: Mapped[str] = mapped_column(Text, default="")
    direction_version: Mapped[str] = mapped_column(String(40), default="")
    next_action: Mapped[Optional[str]] = mapped_column(Text)
    next_due: Mapped[Optional[date]] = mapped_column(Date)
    last_progress: Mapped[Optional[date]] = mapped_column(Date)
    snoozed_until: Mapped[Optional[date]] = mapped_column(Date)
    reply_label: Mapped[Optional[str]] = mapped_column(String(20))      # courtesy | meet | need | defer | decline | pending
    # Qualification state, read by the rules engine. Values: number, true/false, or null = pending.
    scores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    blocker: Mapped[Optional[str]] = mapped_column(String(40))
    revenue_low: Mapped[Optional[int]] = mapped_column(Integer)
    revenue_high: Mapped[Optional[int]] = mapped_column(Integer)
    need: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)   # team, process, problem, impact, target, service
    crm_sync: Mapped[str] = mapped_column(String(20), default="local_saved")  # local_saved | sync_pending | synced | sync_failed
    revision: Mapped[int] = mapped_column(Integer, default=1)          # guards against two people saving over each other
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    company: Mapped[Company] = relationship(back_populates="opportunities")
    contact: Mapped[Optional[Contact]] = relationship()
    brief: Mapped[Optional["ResearchBrief"]] = relationship(back_populates="opportunity", uselist=False)

    @property
    def revenue(self) -> Optional[dict]:
        return {"low": self.revenue_low, "high": self.revenue_high} if self.revenue_low else None


class ResearchBrief(Base):
    __tablename__ = "research_briefs"
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), primary_key=True)
    facts: Mapped[list] = mapped_column(JSON, default=list)            # [{id, text, src, date}]
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    relevance: Mapped[str] = mapped_column(Text, default="")
    alternative: Mapped[str] = mapped_column(Text, default="")
    unknowns: Mapped[list] = mapped_column(JSON, default=list)
    flags: Mapped[list] = mapped_column(JSON, default=list)            # [{type, text}]
    priority: Mapped[dict] = mapped_column(JSON, default=dict)         # tier, authority, relationship, fit, confidence + reasons
    recommendation: Mapped[dict] = mapped_column(JSON, default=dict)   # {decision: accept|return|reject, reason}
    suggested: Mapped[dict] = mapped_column(JSON, default=dict)        # {owner, next, due}
    usage: Mapped[dict] = mapped_column(JSON, default=dict)            # {searches, pages, calls}
    frozen_on: Mapped[Optional[date]] = mapped_column(Date)            # set when accepted at Step 4
    opportunity: Mapped[Opportunity] = relationship(back_populates="brief")


class ResearchTask(Base):
    __tablename__ = "research_tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | paused | failed | done
    progress: Mapped[int] = mapped_column(Integer, default=0)            # 0-5 sub-steps
    pause_reason: Mapped[Optional[str]] = mapped_column(String(20))     # user | budget
    error: Mapped[Optional[str]] = mapped_column(Text)
    returned_with: Mapped[Optional[str]] = mapped_column(Text)          # Step 4 reviewer question
    budget_extended: Mapped[bool] = mapped_column(Boolean, default=False)
    external_run_id: Mapped[Optional[str]] = mapped_column(String(64))  # Aubrey's research run, in live mode
    log: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    round: Mapped[int] = mapped_column(Integer)
    company: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(200), default="")
    person: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    why_person: Mapped[str] = mapped_column(Text, default="")
    why: Mapped[str] = mapped_column(Text, default="")
    signal: Mapped[str] = mapped_column(String(200), default="")
    signal_date: Mapped[str] = mapped_column(String(40), default="")
    flags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="new")     # new | sent | skipped
    skip_reason: Mapped[Optional[str]] = mapped_column(String(200))
    skip_scope: Mapped[Optional[str]] = mapped_column(String(20))
    direction_version: Mapped[str] = mapped_column(String(40), default="")
    demo_star: Mapped[bool] = mapped_column(Boolean, default=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text)  # the search result a live nomination came from


class Outreach(Base):
    __tablename__ = "outreach"
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="none")    # none | draft | sent | followup | replied
    angle: Mapped[Optional[str]] = mapped_column(Text)
    channel: Mapped[Optional[str]] = mapped_column(String(60))
    tone: Mapped[str] = mapped_column(String(20), default="standard")
    subject: Mapped[Optional[str]] = mapped_column(Text)
    body: Mapped[Optional[str]] = mapped_column(Text)
    sent_on: Mapped[Optional[date]] = mapped_column(Date)
    sent_via: Mapped[Optional[str]] = mapped_column(String(60))
    reply_on: Mapped[Optional[date]] = mapped_column(Date)
    reply_text: Mapped[Optional[str]] = mapped_column(Text)


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    type: Mapped[str] = mapped_column(String(40))                      # Meeting notes | Email | Email reply | Phone call
    on: Mapped[date] = mapped_column(Date)
    text: Mapped[str] = mapped_column(Text)                            # original text is always kept, even if analysis fails
    added_by: Mapped[str] = mapped_column(String(100), default="")


class Proposal(Base):
    """An AI suggestion waiting for a person: a score, a condition or a blocker, always with a quote."""
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    note_id: Mapped[Optional[str]] = mapped_column(ForeignKey("notes.id"))
    agent_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("agent_runs.id"))
    kind: Mapped[str] = mapped_column(String(10))                      # score | cond | blocker
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[Any] = mapped_column(JSON)
    quote: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default="")
    evidence_type: Mapped[str] = mapped_column(String(30))             # client_statement | internal_note | approved_research | inference
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | accepted | edited | rejected
    final_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text)                # required for edited and rejected
    decided_by: Mapped[Optional[str]] = mapped_column(String(100))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Evidence(Base):
    """Confirmed qualification evidence. Append-only: a later change adds a new row."""
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    proposal_id: Mapped[Optional[str]] = mapped_column(ForeignKey("proposals.id"))
    kind: Mapped[str] = mapped_column(String(10))                      # score | cond | blocker | revenue
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    quote: Mapped[str] = mapped_column(Text, default="")
    evidence_type: Mapped[str] = mapped_column(String(30), default="internal_note")
    how: Mapped[str] = mapped_column(String(20))                       # accepted | edited | manual review
    reason: Mapped[str] = mapped_column(Text, default="")
    by: Mapped[str] = mapped_column(String(100), default="")
    on: Mapped[date] = mapped_column(Date)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    side: Mapped[str] = mapped_column(String(10))                      # CGS | Client
    assignee: Mapped[str] = mapped_column(String(100))
    due: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="open")    # open | done


class Suggestion(Base):
    """An AI-suggested next action (at most 3 at a time). Becomes a Task only after a person confirms owner and date."""
    __tablename__ = "suggestions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    question: Mapped[str] = mapped_column(Text, default="")
    gap_key: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(12), default="open")    # open | converted | dismissed


class Pack(Base):
    """A qualification pack: a frozen snapshot submitted for Step 8."""
    __tablename__ = "packs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    submitted_by: Mapped[str] = mapped_column(String(100))
    submitted_on: Mapped[date] = mapped_column(Date)
    snapshot: Mapped[dict] = mapped_column(JSON)                       # scores, conditions, blocker, revenue, need
    rule_version: Mapped[str] = mapped_column(String(40))
    exception: Mapped[Optional[str]] = mapped_column(Text)             # reason for submitting when not Ready
    stale_reason: Mapped[Optional[str]] = mapped_column(Text)          # set when the opportunity changes after submission
    status: Mapped[str] = mapped_column(String(20), default="open")    # open | approved | returned | nurture | closed | superseded


class Decision(Base):
    """A human decision at Step 4 or Step 8. Append-only."""
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    step: Mapped[int] = mapped_column(Integer)                         # 4 or 8
    decision: Mapped[str] = mapped_column(String(40))
    by: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(60), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    pack_id: Mapped[Optional[str]] = mapped_column(ForeignKey("packs.id"))
    handover_from: Mapped[Optional[str]] = mapped_column(String(100))
    handover_to: Mapped[Optional[str]] = mapped_column(String(100))
    on: Mapped[date] = mapped_column(Date)


class Feedback(Base):
    """Why a person picked, skipped, accepted, returned or rejected something. Append-only; feeds the learning loop."""
    __tablename__ = "feedback"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(200))                  # company name
    opportunity_id: Mapped[Optional[str]] = mapped_column(String(64))
    step: Mapped[int] = mapped_column(Integer)                         # 1 or 4
    outcome: Mapped[str] = mapped_column(String(20))                   # Skipped | Accepted | Returned | Rejected
    reason: Mapped[str] = mapped_column(String(200))
    comment: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(20))                     # opportunity | theme | consultant | cgs
    by: Mapped[str] = mapped_column(String(100))
    on: Mapped[date] = mapped_column(Date)
    minutes: Mapped[Optional[float]] = mapped_column(Float)            # null = not measured, never zero
    direction_version: Mapped[str] = mapped_column(String(40))


class DirectionVersion(Base):
    """An approved research direction. Never edited; each approval adds a new version."""
    __tablename__ = "direction_versions"
    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    scope: Mapped[str] = mapped_column(String(20), default="shared")   # shared | personal
    owner: Mapped[Optional[str]] = mapped_column(String(100))          # set for personal versions
    problem: Mapped[str] = mapped_column(Text)
    signals: Mapped[list] = mapped_column(JSON)
    buyer_roles: Mapped[list] = mapped_column(JSON)
    exclusions: Mapped[list] = mapped_column(JSON)
    excluded_domains: Mapped[list] = mapped_column(JSON)
    approved_by: Mapped[str] = mapped_column(String(100))
    approved_on: Mapped[date] = mapped_column(Date)
    basis: Mapped[str] = mapped_column(String(40), default="mock")     # mock until CGS approves a direction
    change: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DirectionProposal(Base):
    __tablename__ = "direction_proposals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(20))                      # exclude | direction
    field: Mapped[Optional[str]] = mapped_column(String(40))           # whitelisted field only
    target: Mapped[Optional[str]] = mapped_column(String(200))
    domain: Mapped[Optional[str]] = mapped_column(String(200))
    scope: Mapped[str] = mapped_column(String(20))
    origin: Mapped[str] = mapped_column(String(20))                    # ai | manual | feedback
    title: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text)
    before: Mapped[Any] = mapped_column(JSON, nullable=True)
    after: Mapped[Any] = mapped_column(JSON, nullable=True)
    feedback_ids: Mapped[list] = mapped_column(JSON, default=list)
    base_version: Mapped[Optional[str]] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    decided_by: Mapped[Optional[str]] = mapped_column(String(100))
    created_on: Mapped[date] = mapped_column(Date)
    decided_on: Mapped[Optional[date]] = mapped_column(Date)


class AgentRun(Base):
    """Every AI call: which agent, model and prompt version, and whether it worked."""
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent: Mapped[str] = mapped_column(String(60))
    opportunity_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20))                    # completed | failed
    calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[Optional[int]] = mapped_column(Integer)
    tokens_out: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    on: Mapped[date] = mapped_column(Date)


class Activity(Base):
    """Append-only history. actor_type: h = person, a = AI agent, s = system / rules engine."""
    __tablename__ = "activity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    on: Mapped[date] = mapped_column(Date)
    actor_type: Mapped[str] = mapped_column(String(1))
    text: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class AgentJob(Base):
    """One piece of agent work on the task board. Agents find work three ways: an event happened (a reply, a returned pack),
    the patrol found something due (no reply for a week, a nurture review date), or another agent asked for help (handoff)."""
    __tablename__ = "agent_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)          # see api/autonomy.py JOBS
    agent: Mapped[str] = mapped_column(String(60))
    opportunity_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(12))                   # event | patrol | handoff | person
    reason: Mapped[str] = mapped_column(Text)                          # why this work was picked up, in plain words
    requested_by: Mapped[str] = mapped_column(String(60), default="")  # the agent or person that asked
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)  # queued | running | done | failed | skipped
    parent_id: Mapped[Optional[str]] = mapped_column(String(64))
    depth: Mapped[int] = mapped_column(Integer, default=0)             # 0 = found by the patrol or an event; 1+ = handoff chain
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class InboxItem(Base):
    """What an agent prepared for a person: a draft, new facts, a suggested contact, a reminder. Nothing takes effect
    until a person applies it; dismissing it changes nothing."""
    __tablename__ = "inbox_items"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[Optional[str]] = mapped_column(String(80), index=True)  # null = the whole team
    opportunity_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(20))                     # draft | facts | contact | nurture | actions | ready | candidates | direction
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="new", index=True)  # new | applied | done | dismissed
    decided_by: Mapped[Optional[str]] = mapped_column(String(80))
    created_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
