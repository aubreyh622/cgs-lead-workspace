"""What was settled in the last days (GET /api/settled, the Overview's "Done this week", REDESIGN_PLAN G5).

Read only, from records the workspace already keeps: Step 4 and Step 8 decisions, emails sent, client replies, contact plan
steps done, and agent work a person applied. Nothing here is a new record, so nothing can disagree with the pages."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from api import models as m
from api import services as S

STEP4 = {"Accepted": "Accepted", "Rejected": "Rejected", "Returned for research": "Sent back", "Brought back": "Brought back"}


def settled(c: S.Ctx, days: int = 7, mine: bool = True) -> dict:
    """Newest first. mine: only what the person acting decided, or what happened on their own opportunities."""
    if not 1 <= days <= 90:
        raise S.ActionError("Choose 1 to 90 days.")
    s, me, since = c.s, c.actor, c.today - timedelta(days=days - 1)
    opps = {o.id: o for o in s.scalars(select(m.Opportunity))}
    ok = lambda o, by=None: not mine or (o is not None and o.owner == me) or by == me
    out: list[dict] = []

    def add(on, kind, title, o, by=None, line=""):
        out.append({"on": on.isoformat(), "kind": kind, "title": title, "line": line, "by": by,
                    "opportunity": {"id": o.id, "stage": o.stage, "company": o.company.name, "owner": o.owner} if o else None})

    for d in s.scalars(select(m.Decision).where(m.Decision.on >= since)):
        o = opps.get(d.opportunity_id)
        if not o or not ok(o, d.by):
            continue
        if d.step == 4:
            add(d.on, STEP4.get(d.decision, d.decision), f"{o.company.name}: {STEP4.get(d.decision, d.decision).lower()} at Your decision", o, d.by, d.note)
        elif d.step == 8:
            kind = "Qualified" if d.decision.startswith("Approved") else d.decision
            add(d.on, kind, f"{o.company.name}: {d.decision.lower()}", o, d.by, d.note)
    for r in s.scalars(select(m.Outreach).where((m.Outreach.sent_on >= since) | (m.Outreach.reply_on >= since))):
        o = opps.get(r.opportunity_id)
        if not o or not ok(o):
            continue
        if r.sent_on and r.sent_on >= since:
            add(r.sent_on, "Email sent", f"{o.company.name}: first email to {o.contact.name if o.contact and o.contact.name else 'the contact'}", o,
                o.owner, r.sent_via or "")
        if r.reply_on and r.reply_on >= since:
            add(r.reply_on, "Client replied", f"{o.company.name}: the client wrote back", o, None, o.reply_label or "")
        for st in ((r.plan or {}).get("plan") or {}).get("steps") or []:
            done = st.get("doneOn") if st.get("status") == "done" else None
            if not done or done < since.isoformat():
                continue
            on = date.fromisoformat(done[:10])
            if st.get("channel") == "email" and r.sent_on == on:
                continue  # the same email as "Email sent"
            add(on, "Plan step done", f"{o.company.name}, step {st.get('n')}: {str(st.get('channel', '')).replace('_', ' ')} to {st.get('to', '')}",
                o, st.get("doneBy"))
    out.sort(key=lambda x: x["on"], reverse=True)
    return {"since": since.isoformat(), "days": days, "count": len(out), "items": out}
