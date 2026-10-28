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
def manager_decisions(request: Request, day: str | None = None, limit: int = Query(100, le=500)):
    from datetime import date as _date
    with request.app.state.db.session() as s:
        q = select(m.ManagerDecision).order_by(m.ManagerDecision.id.desc()).limit(limit)
        if day:
            q = q.where(m.ManagerDecision.on == _date.fromisoformat(day))
        return [{"on": d.on.isoformat(), "mode": d.mode, "kind": d.kind, "opportunityId": d.opportunity_id, "reason": d.reason,
                 "outcome": d.outcome, "detail": d.detail, "runId": d.run_id} for d in s.scalars(q)]


@router.get("/manager/compare", summary="Shadow comparison: the manager's decisions next to the rules' work on one day")
def manager_compare(request: Request, day: str | None = None):
    from datetime import date as _date
    from api import manager_agent as MA
    with request.app.state.db.session() as s:
        if day:
            when = _date.fromisoformat(day)
        else:
            when = s.scalar(select(m.ManagerDecision.on).order_by(m.ManagerDecision.on.desc()).limit(1))
            if when is None:
                return {"day": None, "note": "The manager agent has not made any decisions yet."}
        return MA.compare(s, when)


class ItemAction(BaseModel):
    action: Literal["apply", "done", "dismiss"]
    payload_hash: str | None = Field(None, description="The payloadHash you saw. Send it, and apply refuses any other version")


@router.post("/inbox/{item_id}", summary="Apply, finish or dismiss what an agent prepared")
def act(item_id: str, body: ItemAction, c: S.Ctx = Actor):
    return AU.apply_item(c, item_id, body.action, body.payload_hash)
