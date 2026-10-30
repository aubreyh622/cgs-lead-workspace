"""Memory across opportunities: other CGS work with the same company or the same person.

No AI. An opportunity is related when it is at the same company, or when its contact is the same person (same email,
or the same full name). People change companies, so the same person is matched across companies too. A name match is
marked as such: two people can share a name, so a person checks before acting on it.

Used by: GET /api/opportunities/{id}/related, the outreach agent's brief, the manager's triage case, and the first-email
inbox item, which warns when a colleague emailed the same person recently.
"""
from __future__ import annotations

from datetime import date
import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from api import models as m

RECENT_EMAIL_DAYS = 45  # a first email to someone a colleague wrote to within this many days gets a warning
LIMIT = 10


def _name(n: str | None) -> str:
    return re.sub(r"\s+", " ", (n or "").strip().lower())


def same_person_contacts(s: Session, contact: m.Contact | None) -> dict[str, str]:
    """{contact id: "email" | "name"} for other contact rows that are this person."""
    if contact is None:
        return {}
    out: dict[str, str] = {}
    email = (contact.email or "").strip().lower()
    if email:
        for x in s.scalars(select(m.Contact).where(func.lower(m.Contact.email) == email, m.Contact.id != contact.id)):
            out[x.id] = "email"
    name = _name(contact.name)
    if len(name.split()) >= 2:  # a first name alone is not enough to say it is the same person
        last = name.split()[-1]
        for x in s.scalars(select(m.Contact).where(func.lower(m.Contact.name).contains(last), m.Contact.id != contact.id)):
            if _name(x.name) == name:  # compared after case and spacing are evened out
                out.setdefault(x.id, "name")
    return out


def related(s: Session, o: m.Opportunity, today: date) -> dict:
    people = same_person_contacts(s, o.contact)
    rows = list(s.scalars(select(m.Opportunity).where(
        m.Opportunity.id != o.id,
        or_(m.Opportunity.company_id == o.company_id, m.Opportunity.contact_id.in_(list(people) or [""])))
        .order_by(m.Opportunity.updated_at.desc()).limit(LIMIT)))
    items, recent = [], []
    for x in rows:
        r = s.get(m.Outreach, x.id)
        sent = r.sent_on if r and r.sent_on else None
        why = (["same company"] if x.company_id == o.company_id else []) + \
              ([f"same person (matched by {people[x.contact_id]})"] if x.contact_id in people else [])
        item = {"id": x.id, "company": x.company.name, "title": x.title, "stage": x.stage, "owner": x.owner,
                "contact": {"name": x.contact.name, "title": x.contact.title} if x.contact and x.contact.name else None,
                "why": why, "reply_label": x.reply_label,
                "last_email": {"sent_on": sent.isoformat(), "days_ago": (today - sent).days, "subject": r.subject} if sent else None}
        items.append(item)
        if x.contact_id in people and sent and (today - sent).days <= RECENT_EMAIL_DAYS:
            recent.append(item)
    return {"other_opportunities": items, "same_person_emailed_recently": recent}


def warning(rel: dict) -> str:
    """One sentence for the owner when the same person was emailed recently in another opportunity, else ""."""
    if not rel["same_person_emailed_recently"]:
        return ""
    x = rel["same_person_emailed_recently"][0]
    who = x["contact"]["name"] if x["contact"] else "This contact"
    match = " (same name; check it is the same person)" if any("by name" in w for w in x["why"]) else ""
    return (f"{who}{match} was already emailed {x['last_email']['days_ago']} days ago by {x['owner'] or 'a colleague'} "
            f"about {x['company']}: {x['title']} (stage {x['stage']}). Agree with them who writes before sending this one.")


def for_agent(s: Session, o: m.Opportunity, today: date) -> list[dict]:
    """The short form agents see: other CGS work with this company or person."""
    return [{k: v for k, v in x.items() if k != "id" and v not in (None, [], "")} for x in related(s, o, today)["other_opportunities"]]
