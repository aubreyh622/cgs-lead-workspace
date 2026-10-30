"""The agents' task board: putting work on it, and putting finished work in a person's inbox.

Kept separate from api/autonomy.py (which does the work) so that workflow actions in api/services.py can hand work
to the agents without importing the agents themselves.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
import uuid

from sqlalchemy import func, select, update

from api import models as m

# kind -> (agent, uses AI, what the agent does)
KINDS = {
    "followup_draft": ("Outreach agent", True, "Writes a follow-up draft when a sent email has no reply"),
    "reconnect_draft": ("Outreach agent", True, "Writes a reconnect draft that cites the company's latest news"),
    "research_refresh": ("Research agent", True, "Checks a company again for news since the last brief"),
    "person_lookup": ("Research agent", True, "Looks for a person another agent needs (for example the CFO who approves)"),
    "gap_actions": ("Next-step agent", True, "Suggests the next actions for an opportunity that has stalled or was returned"),
    "defer_nurture": ("Reminder rules", False, "Suggests moving a deferred opportunity to nurture on the month the client named"),
    "ready_reminder": ("Reminder rules", False, "Reminds the owner that the rules say Ready and the pack is not submitted"),
    "discovery_round": ("Discovery agent", True, "Finds new candidate companies once a week"),
    "feedback_review": ("Feedback agent", True, "Reviews recent decisions once a week and may propose a direction change"),
    "mail_sync": ("Mail rules", False, "Checks the email threads for client replies and for drafts a person sent from Gmail"),
    "crm_sync": ("CRM rules", False, "Writes what people changed to the CRM (Insightly) when that could not happen at once"),
    "manager_review": ("Manager agent", True, "Looks over every open opportunity once a day and decides which agent should do what"),
    "lessons_review": ("Manager agent", True, "Once a week turns the scorecard's findings into lessons for the agents, which a person approves"),
    "manager_triage": ("Manager agent", True, "Looks closely at one opportunity when something happens: keeps its notes, decides on "
                                              "the other agents' requests, plans the next actions and writes one message to the owner"),
}
TRIGGERS = ("event", "patrol", "handoff", "person", "manager")
MAX_DEPTH = 2          # a handoff may lead to one more handoff, no further
HANDOFF_CAP = 3        # handoffs per opportunity per day
INBOX_DAYS = 14        # what an agent prepared expires after two weeks: news and drafts go out of date
DEFAULTS = {"agents_paused": False, "patrol_hour": 8, "follow_days": 7, "stall_days": 14}
DEFAULT_AI_BUDGET = 60
RETRY_SECONDS = 60     # wait before retrying a temporary AI error; doubles with each attempt


def default_ai_budget() -> int:
    """Read when needed, not at import: this module is imported before api/settings.py loads the .env file."""
    try:
        return max(0, int(os.environ.get("AGENT_AI_DAILY_BUDGET", DEFAULT_AI_BUDGET)))
    except ValueError:
        return DEFAULT_AI_BUDGET


def setting(s, key: str):
    row = s.get(m.Setting, key)
    if row:
        return row.value
    if key == "manager_mode":  # off | shadow | on, from MANAGER_MODE in .env until someone changes it in the workspace
        mode = os.environ.get("MANAGER_MODE", "off").strip().lower()
        return mode if mode in ("off", "shadow", "on") else "off"
    return default_ai_budget() if key == "agent_ai_budget" else DEFAULTS.get(key)


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
        how = {"event": "picked up", "patrol": "found on patrol", "handoff": f"asked by {requested_by}", "person": f"asked by {requested_by}",
               "manager": "assigned by the manager agent"}[trigger]
        s.add(m.Activity(opportunity_id=opp_id, on=today, actor_type="a", text=f"{job.agent} has work ({how}): {reason}"))
    s.flush()
    return job


def manager_mode(s, live: bool) -> str:
    """off | shadow | on. The manager needs a real AI model, so it is always off in demo mode."""
    return setting(s, "manager_mode") if live else "off"


def triage(s, today: date, opp_id: str, event: str, reason: str, trigger: str = "event", requested_by: str = "",
           request: dict | None = None) -> m.AgentJob | None:
    """Ask the manager to look at one opportunity. Everything that happens to it the same day goes into one triage:
    a second event adds its reason (and its request, if another agent asked for help) to the triage still waiting."""
    waiting = open_job(s, "manager_triage", opp_id)
    entry = {"event": event, "reason": reason}
    if waiting and waiting.status == "queued":
        p = dict(waiting.payload or {})
        p["events"] = list(p.get("events", [])) + [entry]
        if request:
            p["requests"] = list(p.get("requests", [])) + [request]
        waiting.payload = p
        if reason not in (waiting.reason or ""):
            waiting.reason = f"{waiting.reason}. Also: {reason}"[:1000]
        return waiting
    if waiting:  # the manager is looking at it right now: start a new triage only once that one is done
        return None
    return enqueue(s, today, "manager_triage", opp_id, trigger, reason,
                   {"events": [entry], "requests": [request] if request else []}, requested_by=requested_by)


def hand_off(s, today: date, kind: str, opp_id: str, reason: str, payload: dict | None = None, requested_by: str = "",
             parent: m.AgentJob | None = None, live: bool = False):
    """One agent asks another for help. With the manager on, the request goes to the manager, which accepts or declines it;
    otherwise (or when the manager is busy with this opportunity) it is queued at once, as before.
    Returns the job that will act on it (the triage or the work itself), or None when a limit stopped it."""
    if opp_id and manager_mode(s, live) == "on":
        request = {"id": new_id("rq"), "kind": kind, "reason": reason, "payload": payload or {}, "requested_by": requested_by,
                   "parent_id": parent.id if parent else None}
        job = triage(s, today, opp_id, "handoff", f"{requested_by} asks the {KINDS[kind][0].lower()}: {reason}",
                     requested_by=requested_by, request=request)
        if job:
            return job
    return enqueue(s, today, kind, opp_id, "handoff", reason, payload, requested_by=requested_by, parent=parent)


def carry_out_request(s, today: date, opp_id: str, request: dict, reason: str = "", by: str = "") -> m.AgentJob | None:
    """Queue the work another agent asked for. The handoff limits (chain length, per day) still apply."""
    parent = s.get(m.AgentJob, request["parent_id"]) if request.get("parent_id") else None
    who = request.get("requested_by", "")
    return enqueue(s, today, request["kind"], opp_id, "handoff", reason or request["reason"], request.get("payload") or {},
                   requested_by=f"{who} via {by}" if by else who, parent=parent)


def ask_for_next_actions(s, today: date, opp_id: str, trigger: str, reason: str, requested_by: str = "", live: bool = False,
                         event: str = "stalled") -> m.AgentJob | None:
    """Next actions for an opportunity: with the manager on, its triage plans them (one message to the owner);
    otherwise the next-step agent does."""
    if manager_mode(s, live) == "on":
        return triage(s, today, opp_id, event, reason, trigger=trigger, requested_by=requested_by)
    return enqueue(s, today, "gap_actions", opp_id, trigger, reason, requested_by=requested_by)


def to_inbox(s, today: date, job: m.AgentJob | None, kind: str, title: str, body: str = "", data: dict | None = None,
             opp: m.Opportunity | None = None, owner: str | None = None, agent: str | None = None) -> m.InboxItem:
    if opp and kind in ("actions", "ready", "reminder", "advice"):  # a newer one replaces an unread older one for the same opportunity
        for old in s.scalars(select(m.InboxItem).where(m.InboxItem.opportunity_id == opp.id, m.InboxItem.kind == kind, m.InboxItem.status == "new")):
            old.status, old.decided_by = "dismissed", "Replaced by a newer item"
    item = m.InboxItem(id=new_id("in"), owner=owner if owner is not None else (opp.owner if opp else None),
                       opportunity_id=opp.id if opp else None, job_id=job.id if job else None,
                       agent=agent or (job.agent if job else ""), kind=kind, title=title, body=body, data=data or {}, created_on=today,
                       payload_hash=payload_hash(kind, title, body, data or {}), basis={"stage": opp.stage} if opp else None,
                       expires_on=today + timedelta(days=INBOX_DAYS))
    s.add(item)
    s.flush()
    return item


def expire_inbox(s, today: date) -> int:
    """Unread items past their expiry date leave the inbox; the agents prepare a new one when it is still needed."""
    old = list(s.scalars(select(m.InboxItem).where(m.InboxItem.status == "new", m.InboxItem.expires_on < today)))
    for it in old:
        it.status, it.decided_by = "dismissed", f"Expired after {INBOX_DAYS} days unread"
    return len(old)


def payload_hash(kind: str, title: str, body: str, data: dict) -> str:
    """A fingerprint of exactly what an agent prepared. A person's approval is tied to it."""
    text = json.dumps({"kind": kind, "title": title, "body": body, "data": data}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ai_calls_today(s, today: date) -> int:
    return s.scalar(select(func.coalesce(func.sum(m.AgentRun.calls), 0)).where(m.AgentRun.on == today, ~m.AgentRun.model.like("demo%"))) or 0


def claim(s, job_id: str) -> bool:
    """Mark a queued job as running in one UPDATE, so two workers can never both run it. True if this caller got it."""
    res = s.execute(update(m.AgentJob).where(m.AgentJob.id == job_id, m.AgentJob.status == "queued")
                    .values(status="running", attempts=m.AgentJob.attempts + 1, retry_at=None)
                    .execution_options(synchronize_session=False))
    s.commit()
    return res.rowcount == 1


def retry_later(job: m.AgentJob, message: str) -> None:
    """Put a job back on the board, to run again after a growing wait (60 s, 120 s, ...)."""
    job.status, job.result = "queued", message
    job.retry_at = datetime.now(timezone.utc) + timedelta(seconds=RETRY_SECONDS * 2 ** max(0, job.attempts - 1))


def due(job_retry_at: datetime | None, now: datetime | None = None) -> bool:
    """True when a queued job may run now (it is not waiting out a retry delay)."""
    if job_retry_at is None:
        return True
    if job_retry_at.tzinfo is None:  # SQLite returns stored datetimes without a time zone; they are UTC
        job_retry_at = job_retry_at.replace(tzinfo=timezone.utc)
    return job_retry_at <= (now or datetime.now(timezone.utc))


def finish(job: m.AgentJob, status: str, result: str = "", error: str | None = None) -> None:
    job.status, job.result, job.error, job.finished_at = status, result, error, datetime.now(timezone.utc)
