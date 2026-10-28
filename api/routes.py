"""Read endpoints for the workspace records (step 3). Changes arrive in step 4."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from api import models as m
from api import views
from api.seed import load_demo
from api.db import Base

router = APIRouter(prefix="/api")
Stage = Literal["researching", "review", "identified", "contacted", "active", "approval", "qualification", "nurture", "rejected", "closed"]


def db(request: Request):
    return request.app.state.db


@router.get("/opportunities", tags=["opportunities"], summary="List opportunities, with rules results for active ones")
def list_opportunities(request: Request, stage: list[Stage] | None = Query(None), owner: str | None = None):
    with db(request).session() as s:
        q = select(m.Opportunity).order_by(m.Opportunity.next_due.is_(None), m.Opportunity.next_due)
        if stage:
            q = q.where(m.Opportunity.stage.in_(stage))
        if owner:
            q = q.where(m.Opportunity.owner == owner)
        return [views.summary(o) for o in s.scalars(q).all()]


@router.get("/opportunities/{opp_id}", tags=["opportunities"], summary="Everything about one opportunity")
def get_opportunity(request: Request, opp_id: str):
    with db(request).session() as s:
        o = s.get(m.Opportunity, opp_id)
        if not o:
            raise HTTPException(404, "Opportunity not found.")
        return views.detail(s, o)


@router.get("/companies/{company_id}/opportunities", tags=["opportunities"], summary="All opportunities at one company")
def company_opportunities(request: Request, company_id: str):
    with db(request).session() as s:
        return [views.summary(o) for o in s.scalars(select(m.Opportunity).where(m.Opportunity.company_id == company_id)).all()]


@router.get("/candidates", tags=["discover"], summary="Step 1 candidates")
def candidates(request: Request, status: Literal["new", "sent", "skipped"] | None = None):
    with db(request).session() as s:
        q = select(m.Candidate).order_by(m.Candidate.round.desc(), m.Candidate.id)
        if status:
            q = q.where(m.Candidate.status == status)
        return [{"id": c.id, "round": c.round, "company": c.company, "domain": c.domain, "person": c.person, "title": c.title,
                 "whyPerson": c.why_person, "why": c.why, "signal": c.signal, "signalDate": c.signal_date, "flags": c.flags,
                 "status": c.status, "skipReason": c.skip_reason, "skipScope": c.skip_scope} for c in s.scalars(q).all()]


@router.get("/direction", tags=["learning loop"], summary="Current shared research direction and its history")
def direction(request: Request):
    with db(request).session() as s:
        versions = s.scalars(select(m.DirectionVersion).where(m.DirectionVersion.scope == "shared").order_by(m.DirectionVersion.created_at.desc(), m.DirectionVersion.version.desc())).all()
        proposals = s.scalars(select(m.DirectionProposal).order_by(m.DirectionProposal.created_on.desc())).all()
        return {"current": views.direction(versions[0]) if versions else None,
                "versions": [views.direction(v) for v in versions],
                "proposals": [{"id": p.id, "type": p.type, "field": p.field, "target": p.target, "domain": p.domain, "scope": p.scope,
                               "origin": p.origin, "title": p.title, "rationale": p.rationale, "before": p.before, "after": p.after,
                               "feedbackIds": p.feedback_ids, "baseVersion": p.base_version, "status": p.status,
                               "createdOn": views.iso(p.created_on)} for p in proposals]}


@router.get("/feedback", tags=["learning loop"], summary="All feedback records, newest first")
def feedback(request: Request):
    with db(request).session() as s:
        return [{"id": f.id, "subject": f.subject, "step": f.step, "outcome": f.outcome, "reason": f.reason, "comment": f.comment,
                 "scope": f.scope, "by": f.by, "on": views.iso(f.on), "minutes": f.minutes, "directionVersion": f.direction_version}
                for f in s.scalars(select(m.Feedback).order_by(m.Feedback.on.desc())).all()]


@router.get("/agent-runs", tags=["agents"], summary="Recent AI calls")
def agent_runs(request: Request, limit: int = Query(20, le=200)):
    with db(request).session() as s:
        return [{"id": r.id, "agent": r.agent, "subject": r.subject, "model": r.model, "prompt": r.prompt_version, "status": r.status,
                 "calls": r.calls, "error": r.error, "on": views.iso(r.on)}
                for r in s.scalars(select(m.AgentRun).order_by(m.AgentRun.on.desc(), m.AgentRun.id.desc()).limit(limit)).all()]


@router.get("/knowledge", tags=["knowledge"], summary="Search the CGS library that agents may quote (K1)")
def knowledge_search(q: str = Query(..., min_length=2), k: int = Query(5, le=20)):
    from api.knowledge import library
    lib = library()
    return {"passages": lib.search(q, k), "librarySize": len(lib.passages), "synthetic": all(p.synthetic for p in lib.passages)}


@router.get("/stats", tags=["system"], summary="How many records of each kind are stored")
def stats(request: Request):
    with db(request).session() as s:
        return {t.name: s.scalar(select(func.count()).select_from(t)) for t in Base.metadata.sorted_tables}


@router.post("/demo/reset", tags=["system"], summary="Demo mode only: wipe everything and reload the synthetic sample")
def reset(request: Request):
    if request.app.state.settings.app_mode != "demo":
        raise HTTPException(403, "Reset is only available in demo mode.")
    database = db(request)
    Base.metadata.drop_all(database.engine)
    Base.metadata.create_all(database.engine)
    load_demo(database)
    return {"ok": True}
