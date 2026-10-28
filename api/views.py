"""Turn database rows into the JSON shapes the frontend uses, with rules-engine results attached."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from api import models as m
from backend.rules import calculate

QUAL_STAGES = ("active", "approval", "qualification", "nurture")


def iso(v):
    return v.isoformat() if isinstance(v, date) else v


def rules_input(o: m.Opportunity) -> dict:
    return {"scores": o.scores, "conditions": o.conditions, "blocker": o.blocker, "revenue": o.revenue}


def assessment(o: m.Opportunity) -> dict | None:
    return calculate(rules_input(o)) if o.stage in QUAL_STAGES else None


def summary(o: m.Opportunity) -> dict:
    c = o.contact
    return {
        "id": o.id, "companyId": o.company_id, "company": o.company.name, "title": o.title, "team": o.team,
        "contact": {"name": c.name if c else None, "title": c.title if c else "Contact to confirm"},
        "stage": o.stage, "owner": o.owner, "rating": o.rating,
        "next": {"action": o.next_action, "due": iso(o.next_due)}, "lastProgress": iso(o.last_progress),
        "crmSync": o.crm_sync, "assessment": assessment(o), "revenue": o.revenue,
    }


def detail(s: Session, o: m.Opportunity) -> dict:
    rows = lambda model, *order: s.scalars(select(model).where(model.opportunity_id == o.id).order_by(*order)).all()
    b = o.brief
    out = summary(o)
    out.update({
        "source": {"type": o.source_type, "note": o.source_note}, "directionVersion": o.direction_version,
        "replyLabel": o.reply_label, "snoozedUntil": iso(o.snoozed_until), "revision": o.revision,
        "scores": o.scores, "conditions": o.conditions, "blocker": o.blocker, "need": o.need,
        "contact": {**out["contact"], "confirmedBy": o.contact.confirmed_by if o.contact else None,
                    "confirmedOn": iso(o.contact.confirmed_on) if o.contact else None},
        "company": {"id": o.company.id, "name": o.company.name, "domain": o.company.domain, "industry": o.company.industry, "hq": o.company.hq},
        "brief": None if not b else {"facts": b.facts, "hypothesis": b.hypothesis, "relevance": b.relevance, "alternative": b.alternative,
                                     "unknowns": b.unknowns, "flags": b.flags, "priority": b.priority, "recommendation": b.recommendation,
                                     "suggested": b.suggested, "usage": b.usage, "frozenOn": iso(b.frozen_on)},
        "notes": [{"id": n.id, "type": n.type, "on": iso(n.on), "text": n.text} for n in rows(m.Note, m.Note.on)],
        "proposals": [{"id": p.id, "kind": p.kind, "key": p.key, "value": p.value, "quote": p.quote, "why": p.why,
                       "evidenceType": p.evidence_type, "status": p.status, "reason": p.reason} for p in rows(m.Proposal, m.Proposal.id)],
        "evidence": [{"kind": e.kind, "key": e.key, "value": e.value, "quote": e.quote, "evidenceType": e.evidence_type,
                      "how": e.how, "reason": e.reason, "by": e.by, "on": iso(e.on)} for e in rows(m.Evidence, m.Evidence.id)],
        "tasks": [{"id": t.id, "title": t.title, "side": t.side, "assignee": t.assignee, "due": iso(t.due), "status": t.status} for t in rows(m.Task, m.Task.due)],
        "suggestions": [{"id": x.id, "title": x.title, "reason": x.reason, "question": x.question} for x in rows(m.Suggestion, m.Suggestion.id) if x.status == "open"],
        "packs": [{"id": p.id, "submittedBy": p.submitted_by, "submittedOn": iso(p.submitted_on), "snapshot": p.snapshot, "ruleVersion": p.rule_version,
                   "exception": p.exception, "staleReason": p.stale_reason, "status": p.status,
                   "handoverTo": p.handover_to, "handoverBy": p.handover_by} for p in rows(m.Pack, m.Pack.submitted_on)],
        "decisions": [{"step": x.step, "decision": x.decision, "by": x.by, "role": x.role, "note": x.note, "on": iso(x.on),
                       "handoverFrom": x.handover_from, "handoverTo": x.handover_to} for x in rows(m.Decision, m.Decision.id)],
        "activity": [{"on": iso(a.on), "actor": a.actor_type, "text": a.text} for a in rows(m.Activity, m.Activity.id)],
    })
    out_reach = s.get(m.Outreach, o.id)
    out["outreach"] = {"status": "none"} if not out_reach else {
        "status": out_reach.status, "subject": out_reach.subject, "body": out_reach.body, "sentOn": iso(out_reach.sent_on),
        "via": out_reach.sent_via, "replyOn": iso(out_reach.reply_on), "replyText": out_reach.reply_text}
    task = s.scalars(select(m.ResearchTask).where(m.ResearchTask.opportunity_id == o.id).order_by(m.ResearchTask.started_at.desc())).first()
    out["research"] = None if not task else {"status": task.status, "progress": task.progress, "pauseReason": task.pause_reason,
                                             "error": task.error, "log": task.log}
    return out


def direction(v: m.DirectionVersion) -> dict:
    return {"version": v.version, "scope": v.scope, "owner": v.owner, "problem": v.problem, "signals": v.signals,
            "buyer_roles": v.buyer_roles, "exclusions": v.exclusions, "excluded_domains": v.excluded_domains,
            "approvedBy": v.approved_by, "approvedOn": iso(v.approved_on), "basis": v.basis, "change": v.change}
