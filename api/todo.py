"""What waits for one person, grouped by what they must do (GET /api/todo, the home page).

The rule behind it: a person's attention is the scarce thing, so the list holds only what needs a person, says for each
row what is needed (not an internal status), and counts honestly (a group's count is the length of its list).

- Groups, in order: problem (something went wrong), waiting (a client, an approver or an agreed date waits on you),
  decide (your click sends or decides), check (agents prepared it; nothing breaks if it waits a day), steer (what the
  agents search for and learn). Every source of work belongs to exactly one group, so nothing is listed twice.
- One piece of work, one row: an agent's "Ready to submit" note replaces the rules' own Ready row for that opportunity, a
  follow-up draft replaces the "no reply yet" alert, and a direction or lesson proposal replaces the inbox note that
  announced it.
- Health lists only what is wrong with the workspace itself (agents paused, AI budget used up, Gmail not connected, agent
  jobs that failed today). A healthy workspace takes no space.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from api import crm_sync as CRM
from api import jobs as J
from api import models as m
from api import services as S
from api.views import assessment

GROUPS = (
    ("problem", "Something went wrong", "Nothing moves on these until a person looks."),
    ("waiting", "Someone is waiting on you", "A client wrote, an approver needs a decision, or a date you agreed has passed."),
    ("decide", "Ready for your decision", "The agents and the rules did their part. Your click sends or decides."),
    ("check", "Prepared by the agents", "Worth a look. Nothing breaks if it waits a day."),
    ("steer", "Steer the agents", "What they search for next and what they learn."),
)

# Inbox kinds: group, the label saying what is needed, and the buttons offered on the row (besides Open).
INBOX = {
    "reply": ("waiting", "Client replied", ()),
    "draft": ("decide", "Review and send", ()),
    "nurture": ("decide", "Decide: nurture?", ("dismiss",)),
    "ready": ("decide", "Send for approval", ("done",)),
    "facts": ("check", "New facts", ("dismiss",)),
    "contact": ("check", "Contact found", ("dismiss",)),
    "evidence": ("check", "Evidence to review", ("dismiss",)),
    "actions": ("check", "Next actions", ("dismiss",)),
    "advice": ("check", "Manager note", ("done",)),
    "reminder": ("check", "Reminder", ("done",)),
    "candidates": ("steer", "New candidates", ("done",)),
    "direction": ("steer", "Direction change", ("done",)),
    "lessons": ("steer", "Lessons to review", ("done",)),
}
OUTREACH_STAGES = ("identified", "contacted", "nurture")
EMAIL_PROBLEMS = {"unknown": "Email may have gone out", "failed": "Email not sent", "expired": "Email not sent"}
PURPOSES = {"first": "first", "followup": "follow-up", "reconnect": "reconnect"}


def _opp(o: m.Opportunity | None) -> dict | None:
    return {"id": o.id, "stage": o.stage, "company": o.company.name} if o else None


def _row(group, label, title, sub, o=None, open=None, ref=None, act=(), key=None):
    return {"key": key, "group": group, "label": label, "title": title, "sub": sub, "opportunity": _opp(o), "open": open,
            "ref": ref, "act": list(act)}


def _mine(o: m.Opportunity | None, me: str) -> bool:
    return o is not None and o.owner == me


def todo(c: S.Ctx, follow_days: int, mail=None) -> dict:
    s, me = c.s, c.actor
    rows: list[dict] = []
    opps = {o.id: o for o in s.scalars(select(m.Opportunity))}

    # ---------- problem ----------
    latest: dict[str, m.OutboundEmail] = {}
    for e in s.scalars(select(m.OutboundEmail).order_by(m.OutboundEmail.created_at)):
        latest[e.opportunity_id] = e  # a newer email replaces an older problem
    for e in latest.values():
        o = opps.get(e.opportunity_id)
        if e.status in EMAIL_PROBLEMS and not e.resolved_at and (e.by == me or _mine(o, me)):
            what = f"the {PURPOSES.get(e.purpose, e.purpose)} email to {e.to}"
            rows.append(_row("problem", EMAIL_PROBLEMS[e.status], f"{o.company.name}: {what}" if o else what.capitalize(), e.error or "It could not go out.", o,
                             ref={"type": "email", "id": e.id}, act=("checked",), key=f"email:{e.id}"))
    last_task: dict[str, m.ResearchTask] = {}
    for t in s.scalars(select(m.ResearchTask).order_by(m.ResearchTask.started_at)):
        last_task[t.opportunity_id] = t
    for oid, t in last_task.items():
        o = opps.get(oid)
        if o and o.stage == "researching" and t.status in ("failed", "paused"):
            why = (t.error or "Research failed.") if t.status == "failed" else "Paused: it reached its budget." if t.pause_reason == "budget" else "Paused."
            rows.append(_row("problem", "Research stopped", f"Research on {o.company.name}", why, o,
                             open={"page": "companies", "params": {"tab": "research"}}, key=f"research:{t.id}"))
    for o in opps.values():
        if not _mine(o, me):
            continue
        conflicts = (CRM.view(o) or {}).get("conflicts")
        if o.crm_sync == "sync_failed":
            rows.append(_row("problem", "CRM sync failed", f"{o.company.name} did not reach the CRM",
                             "The workspace tries again at the daily sync; the opportunity page says why it failed.", o, key=f"crm:{o.id}"))
        elif conflicts:
            rows.append(_row("problem", "CRM disagrees", f"The CRM and the workspace disagree on {o.company.name}",
                             f"{', '.join(x['label'] for x in conflicts)}. The CRM keeps its value until you decide.", o, key=f"crmc:{o.id}"))

    # ---------- inbox items (waiting, decide, check, steer) ----------
    items = list(s.scalars(select(m.InboxItem).where(m.InboxItem.status == "new", or_(m.InboxItem.owner == me, m.InboxItem.owner.is_(None)))
                           .order_by(m.InboxItem.created_at.desc())))
    proposals = list(s.scalars(select(m.DirectionProposal).where(m.DirectionProposal.status == "pending")))
    lessons = list(s.scalars(select(m.AgentLesson).where(m.AgentLesson.status == "proposed")))
    ready_noted = {i.opportunity_id for i in items if i.kind == "ready"}
    drafted = {i.opportunity_id for i in items if i.kind == "draft"}
    for i in items:
        if i.kind not in INBOX or (i.kind == "direction" and proposals) or (i.kind == "lessons" and lessons):
            continue  # the proposals and lessons are listed themselves below
        group, label, act = INBOX[i.kind]
        o = opps.get(i.opportunity_id)
        if i.kind == "actions" and i.title.startswith("Pack returned"):
            group, label, act = "waiting", "Answer the approver", ()
        open = None
        if i.kind in ("draft", "reply") and o and o.stage in OUTREACH_STAGES:
            open = {"page": "outreach", "params": {"item": i.id}}
        elif o is None:
            open = {"page": "companies", "params": {"tab": "new"}} if i.kind == "candidates" else {"page": "agents", "params": {}}
        rows.append(_row(group, label, i.title, f"{i.agent}, {S.fmt(i.created_on)}" + (f". {o.company.name}" if o else ""), o, open,
                         ref={"type": "inbox", "id": i.id, "hash": i.payload_hash}, act=act, key=f"inbox:{i.id}"))

    # ---------- the rules ----------
    alerts = [a for a in S.alerts(c, follow_days) if opps.get(a["opportunityId"]) is not None and opps[a["opportunityId"]].owner == me]
    overdue = {a["opportunityId"] for a in alerts if a["type"] == "overdue"}
    for a in alerts:
        o = opps[a["opportunityId"]]
        name = o.company.name
        if a["type"] == "overdue":
            rows.append(_row("waiting", "Overdue", f"{name}: {o.next_action}", f"Due {S.fmt(o.next_due)}. {o.title}.", o, key=f"overdue:{o.id}"))
        elif a["type"] == "stale_pack":
            rows.append(_row("problem", "Pack out of date", f"Update the qualification pack for {name}", a["message"], o, key=f"stale:{o.id}"))
        elif a["type"] == "follow_up_due" and o.id not in drafted and o.id not in overdue:  # one row per piece of work
            rows.append(_row("decide", "Follow up", f"Follow up with {name}", a["message"], o, key=f"follow:{o.id}"))
        elif a["type"] == "stalled":
            rows.append(_row("check", "Stalled", f"{name} has stalled", a["message"], o, key=f"stalled:{o.id}"))
    directors = c.settings.directors
    for o in opps.values():
        if o.stage == "approval" and o.owner != me and (not directors or me in directors):
            rows.append(_row("waiting", "Approve or return", f"The qualification pack for {o.company.name}", f"{o.title}. Sent by {o.owner}.", o,
                             open={"page": "qualification", "params": {"tab": "approval", "id": o.id}}, key=f"approve:{o.id}"))
        elif o.stage == "active" and _mine(o, me) and o.id not in ready_noted and (assessment(o) or {}).get("readiness") == "Ready":
            rows.append(_row("decide", "Send for approval", f"The pack for {o.company.name}", "The rules say Ready.", o,
                             open={"page": "qualification", "params": {"tab": "active", "id": o.id}}, key=f"ready:{o.id}"))
        elif o.stage == "review":
            rows.append(_row("decide", "Accept or reject", f"Step 4: {o.company.name}", f"{o.title}. The research brief is ready.", o,
                             open={"page": "companies", "params": {"tab": "review", "id": o.id}}, key=f"review:{o.id}"))

    # ---------- steer ----------
    for p in proposals:
        rows.append(_row("steer", "Direction change", f"Leave {p.target} out of future searches" if p.type == "exclude" else (p.title or f"Change {p.field}"),
                         "A change to the research direction, waiting for a person to apply or dismiss it.", None,
                         open={"page": "agents", "params": {}}, key=f"proposal:{p.id}"))
    for l in lessons:
        rows.append(_row("steer", "Lesson to review", l.text, f"A lesson for the {l.agent.lower()}, proposed by {l.proposed_by}.", None,
                         open={"page": "agents", "params": {}}, key=f"lesson:{l.id}"))
    new_cands = s.scalar(select(func.count()).select_from(m.Candidate).where(m.Candidate.status == "new")) or 0
    if new_cands and not any(i.kind == "candidates" for i in items):
        rows.append(_row("steer", "Pick candidates", f"Pick which of {new_cands} new candidate{'s' if new_cands != 1 else ''} get researched",
                         "Found by the discovery agent. Each one you pick gets a research brief.", None,
                         open={"page": "companies", "params": {"tab": "new"}}, key="candidates"))

    groups = []
    for gid, title, why in GROUPS:
        its = [r for r in rows if r["group"] == gid]
        if its:
            groups.append({"id": gid, "title": title, "why": why, "count": len(its), "items": its})
    return {"me": me, "count": len(rows), "groups": groups, "health": health(c, mail)}


def health(c: S.Ctx, mail=None) -> list[dict]:
    """Only what is wrong with the workspace itself, each with what a person can do about it."""
    s, out = c.s, []
    if J.setting(s, "agents_paused"):
        out.append({"key": "paused", "text": "The agents are paused. Their work waits until someone resumes them.", "page": "agents"})
    elif J.paused_agents(s):
        names = ", ".join(sorted(J.paused_agents(s)))
        out.append({"key": "paused-some", "text": f"Paused: {names}. Their work waits.", "page": "agents"})
    if not c.demo and J.ai_calls_today(s, c.today) >= int(J.setting(s, "agent_ai_budget")):
        out.append({"key": "budget", "text": "Today's AI budget is used up. AI work waits until tomorrow, or raise the budget.", "page": "agents"})
    if mail is not None and mail.name == "gmail" and not mail.ready():
        out.append({"key": "gmail", "text": "Gmail is not connected, so emails cannot go out and replies are not read. "
                                            "Sign in again: python -m api.toolkit.gmail_auth", "page": "agents"})
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    failed = [j for j in s.scalars(select(m.AgentJob).where(m.AgentJob.status == "failed", m.AgentJob.finished_at >= since))]
    if failed:
        out.append({"key": "failed", "text": f"{len(failed)} agent job{'s' if len(failed) != 1 else ''} failed in the last 24 hours.",
                    "page": "agents"})
    return out
