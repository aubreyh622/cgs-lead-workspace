"""What happened for a person since a moment: the feed the page polls (GET /api/updates) to show notices and keep a
notification history. Read from what the workspace already records, nothing new is written:

- inbox: what an agent prepared for this person or the whole team (drafts, client replies, new candidates, facts, ...);
- research: a brief that is ready, or research that stopped (for leads this person owns, or not owned yet);
- email: an email this person approved that was sent, or was not sent.

Each item names the opportunity and its stage, so the page links it to the right place."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from api import models as m
from api.undo import aware

MAX_ITEMS = 100


def _iso(t: datetime | None) -> str | None:
    t = aware(t)
    return t.isoformat() if t else None


def feed(s, me: str, since: datetime) -> list[dict]:
    """Newest first, at most MAX_ITEMS. since is a UTC time."""
    items: list[dict] = []
    opp = lambda oid: s.get(m.Opportunity, oid) if oid else None

    rows = s.scalars(select(m.InboxItem).where(m.InboxItem.created_at > since, or_(m.InboxItem.owner == me, m.InboxItem.owner.is_(None)))
                     .order_by(m.InboxItem.created_at.desc()).limit(MAX_ITEMS))
    for it in rows:
        o = opp(it.opportunity_id)
        items.append({"id": f"inbox:{it.id}", "at": _iso(it.created_at), "kind": "inbox", "itemKind": it.kind, "itemId": it.id,
                      "opportunityId": it.opportunity_id, "stage": o.stage if o else None, "company": o.company.name if o else None,
                      "agent": it.agent, "text": it.title, "open": it.status == "new"})

    rows = s.scalars(select(m.ResearchTask).where(m.ResearchTask.ended_at > since).order_by(m.ResearchTask.ended_at.desc()).limit(MAX_ITEMS))
    for t in rows:
        o = opp(t.opportunity_id)
        if o is None or (o.owner and o.owner != me):
            continue
        name = o.company.name
        text = (f"Brief ready: {name}" + ("" if name.endswith(".") else ".") if t.status == "done"
                else f"Research on {name} paused: {t.error or 'it reached a limit.'}" if t.status == "paused"
                else f"Research on {name} stopped: {t.error or 'no brief was produced.'}")
        items.append({"id": f"research:{t.id}", "at": _iso(t.ended_at), "kind": "research", "status": t.status,
                      "opportunityId": o.id, "stage": o.stage, "company": name, "agent": "Research agent", "text": text})

    rows = s.scalars(select(m.OutboundEmail).where(m.OutboundEmail.ended_at > since, m.OutboundEmail.by == me,
                                                   m.OutboundEmail.status.in_(("sent", "failed", "expired", "unknown", "returned")))
                     .order_by(m.OutboundEmail.ended_at.desc()).limit(MAX_ITEMS))
    rows = list(rows) + list(s.scalars(select(m.OutboundEmail).where(m.OutboundEmail.created_at > since, m.OutboundEmail.approver == me,
                                                                      m.OutboundEmail.status == "approval").limit(MAX_ITEMS)))
    for r in rows:
        o = opp(r.opportunity_id)
        if r.status == "approval":
            items.append({"id": f"approve:{r.id}", "at": _iso(r.created_at), "kind": "email", "status": r.status,
                          "opportunityId": r.opportunity_id, "stage": o.stage if o else None, "company": o.company.name if o else None,
                          "agent": None, "text": f"{r.by} asks you to approve the email to {r.to}."})
            continue
        text = (f"Email sent to {r.to}." if r.status == "sent"
                else f"The email to {r.to} was returned. {r.error}" if r.status == "returned"
                else f"The email to {r.to} may not have gone out. {r.error}" if r.status == "unknown"
                else f"The email to {r.to} was not sent: {r.error or 'it could not go out.'}")
        items.append({"id": f"email:{r.id}", "at": _iso(r.ended_at), "kind": "email", "status": r.status,
                      "opportunityId": r.opportunity_id, "stage": o.stage if o else None, "company": o.company.name if o else None,
                      "agent": None, "text": text})

    items.sort(key=lambda x: x["at"] or "", reverse=True)
    return items[:MAX_ITEMS]


def since_from(raw: str | None, days: int) -> datetime:
    """The moment to read from: the page's last check, or `days` back (at most 30) for the history."""
    if raw:
        try:
            # a "+" sent unencoded in a query string arrives as a space
            return aware(datetime.fromisoformat(raw.strip().replace(" ", "+").replace("Z", "+00:00")))
        except ValueError:
            pass
    return datetime.now(timezone.utc) - timedelta(days=max(1, min(30, days)))
