"""The agents' task board: putting work on it, and putting finished work in a person's inbox.

Kept separate from api/autonomy.py (which does the work) so that workflow actions in api/services.py can hand work
to the agents without importing the agents themselves.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import os
import uuid

from sqlalchemy import func, select

from api import models as m

# kind -> (agent, uses AI, what the agent does)
KINDS = {
    "followup_draft": ("A1 Preparation and outreach", True, "Writes a follow-up draft when a sent email has no reply"),
    "reconnect_draft": ("A1 Preparation and outreach", True, "Writes a reconnect draft that cites the company's latest news"),
    "research_refresh": ("Research agent", True, "Checks a company again for news since the last brief"),
    "person_lookup": ("Research agent", True, "Looks for a person another agent needs (for example the CFO who approves)"),
    "gap_actions": ("A4 Action and reporting", True, "Suggests the next actions for an opportunity that has stalled or was returned"),
    "defer_nurture": ("A4 Action and reporting", False, "Suggests moving a deferred opportunity to nurture on the month the client named"),
    "ready_reminder": ("A4 Action and reporting", False, "Reminds the owner that the rules say Ready and the pack is not submitted"),
    "discovery_round": ("Discovery agent", True, "Finds new candidate companies once a week"),
    "feedback_review": ("Feedback agent", True, "Reviews recent decisions once a week and may propose a direction change"),
}
TRIGGERS = ("event", "patrol", "handoff", "person")
MAX_DEPTH = 2          # a handoff may lead to one more handoff, no further
HANDOFF_CAP = 3        # handoffs per opportunity per day
DEFAULTS = {"agents_paused": False, "agent_ai_budget": int(os.environ.get("AGENT_AI_DAILY_BUDGET", "60")), "patrol_hour": 8,
            "follow_days": 7, "stall_days": 14}


def setting(s, key: str):
    row = s.get(m.Setting, key)
    return row.value if row else DEFAULTS.get(key)


def set_setting(s, key: str, value) -> None:
    row = s.get(m.Setting, key)
    if row:
        row.value = value
    else:
        s.add(m.Setting(key=key, value=value))


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def open_job(s, kind: str, opp_id: str | None) -> m.AgentJob | None:
    return s.scalars(select(m.AgentJob).where(m.AgentJob.kind == kind, m.AgentJob.opportunity_id == opp_id,
                                              m.AgentJob.status.in_(("queued", "running")))).first()


def enqueue(s, today: date, kind: str, opp_id: str | None, trigger: str, reason: str, payload: dict | None = None,
            requested_by: str = "", parent: m.AgentJob | None = None) -> m.AgentJob | None:
    """Put one piece of work on the board. Returns None (and says why in the activity log) when it is a duplicate
    or a handoff limit is reached."""
    if kind not in KINDS or trigger not in TRIGGERS:
        raise ValueError(f"unknown job {kind}/{trigger}")
    if open_job(s, kind, opp_id):
        return None
    depth = parent.depth + 1 if parent else (1 if trigger == "handoff" else 0)
    if trigger == "handoff":
        why = None
        if depth > MAX_DEPTH:
            why = f"handoff chain longer than {MAX_DEPTH}"
        elif opp_id and s.scalar(select(func.count()).select_from(m.AgentJob).where(
                m.AgentJob.opportunity_id == opp_id, m.AgentJob.trigger == "handoff", m.AgentJob.created_on == today)) >= HANDOFF_CAP:
            why = f"already {HANDOFF_CAP} handoffs today for this opportunity"
        if why:
            s.add(m.Activity(opportunity_id=opp_id, on=today, actor_type="s",
                             text=f"{requested_by} asked {KINDS[kind][0]} to help, but the request was not queued: {why}."))
            return None
    job = m.AgentJob(id=new_id("job"), kind=kind, agent=KINDS[kind][0], opportunity_id=opp_id, trigger=trigger, reason=reason,
                     requested_by=requested_by, payload=payload or {}, status="queued", parent_id=parent.id if parent else None,
                     depth=depth, created_on=today)
    s.add(job)
    if opp_id:
        how = {"event": "picked up", "patrol": "found on patrol", "handoff": f"asked by {requested_by}", "person": f"asked by {requested_by}"}[trigger]
        s.add(m.Activity(opportunity_id=opp_id, on=today, actor_type="a", text=f"{job.agent} has work ({how}): {reason}"))
    s.flush()
    return job


def to_inbox(s, today: date, job: m.AgentJob | None, kind: str, title: str, body: str = "", data: dict | None = None,
             opp: m.Opportunity | None = None, owner: str | None = None, agent: str | None = None) -> m.InboxItem:
    if opp and kind in ("actions", "ready", "reminder"):  # a newer one replaces an unread older one for the same opportunity
        for old in s.scalars(select(m.InboxItem).where(m.InboxItem.opportunity_id == opp.id, m.InboxItem.kind == kind, m.InboxItem.status == "new")):
            old.status, old.decided_by = "dismissed", "Replaced by a newer item"
    item = m.InboxItem(id=new_id("in"), owner=owner if owner is not None else (opp.owner if opp else None),
                       opportunity_id=opp.id if opp else None, job_id=job.id if job else None,
                       agent=agent or (job.agent if job else ""), kind=kind, title=title, body=body, data=data or {}, created_on=today)
    s.add(item)
    s.flush()
    return item


def ai_calls_today(s, today: date) -> int:
    return s.scalar(select(func.coalesce(func.sum(m.AgentRun.calls), 0)).where(m.AgentRun.on == today, ~m.AgentRun.model.like("demo%"))) or 0


def finish(job: m.AgentJob, status: str, result: str = "", error: str | None = None) -> None:
    job.status, job.result, job.error, job.finished_at = status, result, error, datetime.now(timezone.utc)
