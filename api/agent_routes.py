"""Endpoints for the autonomous agents: the task board, each person's inbox, and the controls (patrol, run, pause)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

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
                "queued": len(AU.queued(request.app)), "worker": bool(getattr(request.app.state, "worker", None)),
                "agents": list(team.values())}


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


@router.patch("/agents", summary="Pause or resume all agents, or change the daily AI budget or patrol hour")
def configure(body: AgentSettings, c: S.Ctx = Actor):
    for key, val in (("agents_paused", body.paused), ("agent_ai_budget", body.ai_budget), ("patrol_hour", body.patrol_hour)):
        if val is not None:
            J.set_setting(c.s, key, val)
    if body.paused is not None:
        S.log(c, None, "h", f"Agents {'paused' if body.paused else 'resumed'} by {c.actor}")
    return {"paused": bool(J.setting(c.s, "agents_paused")), "aiBudget": int(J.setting(c.s, "agent_ai_budget")),
            "patrolHour": int(J.setting(c.s, "patrol_hour"))}


@router.get("/inbox", summary="What the agents prepared. owner=name shows that person's items plus the team's")
def inbox(request: Request, owner: str | None = None, status: str = "new"):
    with request.app.state.db.session() as s:
        q = select(m.InboxItem).order_by(m.InboxItem.created_at.desc())
        if status != "all":
            q = q.where(m.InboxItem.status == status)
        if owner:
            q = q.where(or_(m.InboxItem.owner == owner, m.InboxItem.owner.is_(None)))
        return [AU.item_view(i) for i in s.scalars(q)]


class ItemAction(BaseModel):
    action: Literal["apply", "done", "dismiss"]


@router.post("/inbox/{item_id}", summary="Apply, finish or dismiss what an agent prepared")
def act(item_id: str, body: ItemAction, c: S.Ctx = Actor):
    return AU.apply_item(c, item_id, body.action)
