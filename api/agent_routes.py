"""Endpoints for the autonomous agents: the task board, each person's inbox, and the controls (patrol, run, pause)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from api import autonomy as AU
from api import jobs as J
from api import models as m
from api import services as S
from api.actions import Actor, demo_today

router = APIRouter(prefix="/api", tags=["agents"])


@router.get("/agents", summary="What each agent does, whether they are paused, today's AI budget and the queue")
def agents(request: Request):
    with request.app.state.db.session() as s:
        today = demo_today(s, request.app.state.settings)
        jobs = list(s.scalars(select(m.AgentJob).where(m.AgentJob.created_on == today)))
        team = {}
        for kind, (agent, ai, what) in J.KINDS.items():
            team.setdefault(agent, {"agent": agent, "does": [], "doneToday": 0})["does"].append(what)
        for j in jobs:
            if j.status == "done":
                team[j.agent]["doneToday"] += 1
        return {"paused": bool(J.setting(s, "agents_paused")), "today": today.isoformat(),
                "aiCallsToday": J.ai_calls_today(s, today), "aiBudget": int(J.setting(s, "agent_ai_budget")),
                "lastPatrol": J.setting(s, "last_patrol"), "patrolHour": int(J.setting(s, "patrol_hour")),
                "queued": s.scalar(select(func.count()).select_from(m.AgentJob).where(m.AgentJob.status == "queued")), "worker": bool(getattr(request.app.state, "worker", None)),
                "managerMode": J.setting(s, "manager_mode"), "agents": list(team.values())}


@router.get("/agents/jobs", summary="The task board: who is working on what, and why")
def board(request: Request, status: str | None = None, opportunity: str | None = None, limit: int = 100):
    with request.app.state.db.session() as s:
        q = select(m.AgentJob).order_by(m.AgentJob.created_at.desc()).limit(limit)
        if status:
            q = q.where(m.AgentJob.status == status)
        if opportunity:
            q = q.where(m.AgentJob.opportunity_id == opportunity)
        return [AU.job_view(j) for j in s.scalars(q)]


@router.post("/agents/patrol", summary="Patrol now: look at every opportunity and queue the work that is due")
def patrol(request: Request, run: bool = False, c: S.Ctx = Actor):
    found = AU.patrol(AU.agent_ctx(request.app, c.s))
    c.s.commit()
    out = {"queued": [AU.job_view(j) for j in found]}
    if run:
        out["ran"] = AU.run_pending(request.app)
    return out


@router.post("/agents/run", summary="Work through the queue now instead of waiting for the background worker")
def run(request: Request, limit: int = 50, c: S.Ctx = Actor):
    return {"ran": AU.run_pending(request.app, limit)}


class AgentSettings(BaseModel):
    paused: bool | None = None
    ai_budget: int | None = Field(None, ge=0, le=2000, description="AI calls the agents may make per day")
    patrol_hour: int | None = Field(None, ge=0, le=23)
    manager_mode: Literal["off", "shadow", "on"] | None = Field(None, description="off, shadow (decide and compare, queue nothing) or on")


@router.patch("/agents", summary="Pause or resume all agents, or change the daily AI budget or patrol hour")
def configure(body: AgentSettings, c: S.Ctx = Actor):
    for key, val in (("agents_paused", body.paused), ("agent_ai_budget", body.ai_budget), ("patrol_hour", body.patrol_hour),
                     ("manager_mode", body.manager_mode)):
        if val is not None:
            J.set_setting(c.s, key, val)
    if body.paused is not None:
        S.log(c, None, "h", f"Agents {'paused' if body.paused else 'resumed'} by {c.actor}")
    if body.manager_mode is not None:
        S.log(c, None, "h", f"Manager agent set to {body.manager_mode} by {c.actor}")
    return {"paused": bool(J.setting(c.s, "agents_paused")), "aiBudget": int(J.setting(c.s, "agent_ai_budget")),
            "patrolHour": int(J.setting(c.s, "patrol_hour")), "managerMode": J.setting(c.s, "manager_mode")}


@router.get("/inbox", summary="What the agents prepared. owner=name shows that person's items plus the team's")
def inbox(request: Request, owner: str | None = None, status: str = "new"):
    with request.app.state.db.session() as s:
        q = select(m.InboxItem).order_by(m.InboxItem.created_at.desc())
        if status != "all":
            q = q.where(m.InboxItem.status == status)
        if owner:
            q = q.where(or_(m.InboxItem.owner == owner, m.InboxItem.owner.is_(None)))
        return [AU.item_view(i) for i in s.scalars(q)]


@router.get("/manager/decisions", summary="The manager agent's decisions, newest first (shadow mode included)")
def manager_decisions(request: Request, day: str | None = None, scope: Literal["review", "triage"] | None = None,
                      opportunity: str | None = None, limit: int = Query(100, le=500)):
    from datetime import date as _date
    with request.app.state.db.session() as s:
        q = select(m.ManagerDecision).order_by(m.ManagerDecision.id.desc()).limit(limit)
        if day:
            q = q.where(m.ManagerDecision.on == _date.fromisoformat(day))
        if scope == "triage":
            q = q.where(m.ManagerDecision.scope == "triage")
        elif scope == "review":
            q = q.where(or_(m.ManagerDecision.scope == "review", m.ManagerDecision.scope.is_(None)))
        if opportunity:
            q = q.where(m.ManagerDecision.opportunity_id == opportunity)
        return [{"on": d.on.isoformat(), "mode": d.mode, "scope": d.scope or "review", "kind": d.kind, "opportunityId": d.opportunity_id,
                 "reason": d.reason, "outcome": d.outcome, "detail": d.detail, "runId": d.run_id} for d in s.scalars(q)]


@router.get("/manager/compare", summary="Shadow comparison: the manager's decisions next to the rules' work on one day")
def manager_compare(request: Request, day: str | None = None):
    from datetime import date as _date
    from api import manager_agent as MA
    with request.app.state.db.session() as s:
        if day:
            when = _date.fromisoformat(day)
        else:
            when = s.scalar(select(m.ManagerDecision.on).where(or_(m.ManagerDecision.scope == "review", m.ManagerDecision.scope.is_(None)))
                            .order_by(m.ManagerDecision.on.desc()).limit(1))
            if when is None:
                return {"day": None, "note": "The manager agent has not made any decisions yet."}
        return MA.compare(s, when)


@router.get("/manager/scorecard", summary="How people received the agents' work: per kind of work and owner, evidence calibration, reliability")
def manager_scorecard(request: Request, days: int = Query(60, ge=1, le=365), owner: str | None = None):
    from api import scorecard as SC
    with request.app.state.db.session() as s:
        return SC.build(s, demo_today(s, request.app.state.settings), days, owner)


@router.get("/lessons", summary="Lessons for the agents: proposed, approved, rejected and retired, with their effect since approval")
def lessons(request: Request, status: str | None = None, agent: str | None = None):
    from api import lessons as LS
    with request.app.state.db.session() as s:
        today = demo_today(s, request.app.state.settings)
        q = select(m.AgentLesson).order_by(m.AgentLesson.id.desc())
        if status:
            q = q.where(m.AgentLesson.status == status)
        if agent:
            q = q.where(m.AgentLesson.agent == agent)
        return [LS.view(s, x, today) for x in s.scalars(q)]


@router.get("/lessons/findings", summary="What stands out in the scorecard: what the weekly lessons review would read")
def lesson_findings(request: Request):
    from api import lessons as LS
    with request.app.state.db.session() as s:
        return LS.findings(s, demo_today(s, request.app.state.settings))


class LessonIn(BaseModel):
    agent: str = Field(..., description="Evidence agent, Outreach agent, Next-step agent or Manager agent")
    text: str = Field(..., description="One or two sentences to the agent: in which situation, what to do")
    why: str = Field(..., description="What was seen that the lesson answers")


@router.post("/lessons", summary="A person proposes a lesson for an agent (it still needs approval)")
def propose_lesson(body: LessonIn, c: S.Ctx = Actor):
    from api import lessons as LS
    try:
        lesson = LS.propose(c.s, c.today, body.agent, body.text, "person", body.why, c.actor)
    except LS.Invalid as e:
        raise S.ActionError(str(e))
    S.log(c, None, "h", f"{c.actor} proposed lesson {LS.label(lesson)} for the {lesson.agent.lower()}")
    return LS.view(c.s, lesson, c.today)


class LessonDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field("", description="Required to reject; the manager reads it before it proposes again")


def _may_decide_lessons(c: S.Ctx) -> None:
    if not S.may_manage_permissions(c):
        raise S.ActionError("Only the workspace admins (CGS_ADMINS) may approve, reject or retire lessons.", 403, "forbidden")


@router.post("/lessons/{lesson_id}/decision", summary="Approve a lesson (it joins the agent's prompt) or reject it")
def decide_lesson(lesson_id: int, body: LessonDecision, c: S.Ctx = Actor):
    from api import lessons as LS
    _may_decide_lessons(c)
    try:
        lesson = LS.decide(c.s, c.today, lesson_id, body.decision, body.reason, c.actor)
    except LS.Invalid as e:
        raise S.ActionError(str(e))
    S.log(c, None, "h", f"{c.actor} {lesson.status} lesson {LS.label(lesson)} for the {lesson.agent.lower()}"
                       + (f": {lesson.decision_reason}" if lesson.decision_reason else ""))
    return LS.view(c.s, lesson, c.today)


class LessonRetire(BaseModel):
    reason: str


@router.post("/lessons/{lesson_id}/retire", summary="Take an approved lesson out of the agent's prompt")
def retire_lesson(lesson_id: int, body: LessonRetire, c: S.Ctx = Actor):
    from api import lessons as LS
    _may_decide_lessons(c)
    try:
        lesson = LS.retire(c.s, c.today, lesson_id, body.reason, c.actor)
    except LS.Invalid as e:
        raise S.ActionError(str(e))
    S.log(c, None, "h", f"{c.actor} retired lesson {LS.label(lesson)}: {lesson.retired_reason}")
    return LS.view(c.s, lesson, c.today)


@router.post("/lessons/review", summary="Run the manager's lessons review now instead of waiting for the week (live mode)")
def review_lessons(request: Request, c: S.Ctx = Actor):
    if c.demo:
        raise S.ActionError("The lessons review needs a real AI model (live mode).", 409, "demo_mode")
    job = J.enqueue(c.s, c.today, "lessons_review", None, "person", f"Asked by {c.actor}", requested_by=c.actor)
    c.s.commit()
    if not job:
        return {"queued": False, "note": "A lessons review is already on the board."}
    return {"queued": True, "job": AU.job_view(job)}


@router.get("/opportunities/{opp_id}/memory", summary="The manager agent's memory of one opportunity: its summary and the promises each side made")
def memory(opp_id: str, request: Request):
    from api import memory as MEM
    with request.app.state.db.session() as s:
        if not s.get(m.Opportunity, opp_id):
            raise S.ActionError("Opportunity not found.", 404, "not_found")
        return MEM.view(MEM.get(s, opp_id), demo_today(s, request.app.state.settings))


class CloseCommitment(BaseModel):
    status: Literal["done", "dropped"] = Field(..., description="done: it happened; dropped: it no longer applies")
    why: str = Field(..., description="What shows it happened, or why it no longer applies")


@router.post("/opportunities/{opp_id}/commitments/{cid}", summary="A person closes a promise in the manager's memory (done or dropped)")
def close_commitment(opp_id: str, cid: str, body: CloseCommitment, c: S.Ctx = Actor):
    from api import memory as MEM
    o = S.opp(c, opp_id)
    try:
        item = MEM.close_commitment(c.s, o.id, cid, body.status, body.why, c.today, c.actor)
    except MEM.Invalid as e:
        raise S.ActionError(str(e))
    S.log(c, o.id, "h", f"{c.actor} marked a promise {body.status}: {item['who']} — {item['what']} ({body.why.strip()})")
    return MEM.view(MEM.get(c.s, o.id), c.today)


class ItemAction(BaseModel):
    action: Literal["apply", "send", "done", "dismiss"]
    payload_hash: str | None = Field(None, description="The payloadHash you saw. Send it, and apply refuses any other version")
    to: str | None = Field(None, description="send only: the address you approved (see GET /api/opportunities/{id}/outreach/email?itemId=...)")


@router.post("/inbox/{item_id}", summary="Apply, finish or dismiss what an agent prepared; an email draft can be approved and sent at once (action send)")
def act(item_id: str, body: ItemAction, c: S.Ctx = Actor):
    return AU.apply_item(c, item_id, body.action, body.payload_hash, body.to)
