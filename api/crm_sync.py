"""Keeps the CRM (Insightly) in step with the workspace, after people act. Rules only, no AI.

When: a person's action that the CRM shows (Step 4 accept, contact confirmed, email sent, reply applied, note added,
nurture, approval, close) calls mark(); the opportunity becomes sync_pending and is synced right after the request is
saved (after_request). Anything that failed or happened without a request (an email sent from Gmail, seen by the daily
mail check) is picked up by the daily crm_sync job or POST /api/crm/sync. A failed sync never undoes the person's action.

What (config/crm.json maps names):
  researching, review         nothing: a candidate becomes a CRM lead only once a person accepts it at Step 4
  identified ... nurture      one lead (found by the Workspace ID field or the email address first, so never twice),
                              its status, details and the workspace notes; the research brief as the lead's first note
  qualification               organization + contact + opportunity at the qualification stage, linked; the lead set to
                              the 'converted' status and marked converted
  rejected, closed            the lead's status (Disqualified), if a lead exists
Needs CRM_MODE=write_approved and a CRM (CRM_PROVIDER). Reads (matches) need read_only or write_approved.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging

from sqlalchemy import select

from api import models as m
from api import toolkit as TK
from api.toolkit.crm import CrmError, split_name

WHO = "CRM sync"
NOT_YET = ("researching", "review")
log_ = logging.getLogger("cgs.crm")


def client(c):
    f = getattr(c, "crm_factory", None)
    return f() if f else None


def reads_on(c) -> bool:
    return c.settings.crm_mode in ("read_only", "write_approved") and client(c) is not None


def writes_on(c) -> bool:
    return c.settings.crm_mode == "write_approved" and client(c) is not None


def log(c, opp_id: str, text: str) -> None:
    c.s.add(m.Activity(opportunity_id=opp_id, on=c.today, actor_type="s", text=text))


def mark(c, o: m.Opportunity) -> None:
    """A person changed something the CRM shows: sync this opportunity once the request is saved."""
    if not writes_on(c) or o.stage in NOT_YET:
        return
    if o.stage == "rejected" and not o.crm_link:
        return  # rejected at Step 4: it never became a lead
    o.crm_sync = "sync_pending"
    dirty = getattr(c, "crm_dirty", None)
    if dirty is not None:
        dirty.add(o.id)


# ---------- what the CRM gets ----------
def evidence_url(o: m.Opportunity) -> str:
    for f in (o.brief.facts if o.brief else []) or []:
        if str(f.get("src") or "").startswith("http"):
            return f["src"]
    return ""


def lead_record(o: m.Opportunity) -> dict:
    ct = o.contact
    first, last = split_name(ct.name if ct else None)
    tier = (o.brief.priority or {}).get("tier") if o.brief else None
    hyp = (o.brief.hypothesis if o.brief else "") or ""
    return {"first": first, "last": last, "title": ct.title if ct and ct.title != "Contact to confirm" else "",
            "email": (ct.email if ct else "") or "", "company": o.company.name,
            "website": f"https://{o.company.domain}" if o.company.domain else "", "industry": o.company.industry,
            "rating": o.rating, "status": "active" if o.stage == "qualification" else o.stage,  # converted by convert(), not here
            "source": o.source_type, "owner": o.owner,
            "description": (f"{o.title}. {hyp}".strip()[:1900] + f"\n\nCGS workspace: {o.id}, owner {o.owner or 'not set'}."),
            "workspace_id": o.id, "target_problem": o.target_problem or "", "evidence_url": evidence_url(o),
            "priority_tier": tier if isinstance(tier, (int, float)) else None,
            "lead_type": None}  # filled from config by the connector's default below


def brief_note(o: m.Opportunity) -> str:
    b = o.brief
    lines = [f"Research brief from the CGS lead workspace ({o.id}): {o.title}"]
    if b:
        if b.hypothesis:
            lines.append(f"Why now: {b.hypothesis}")
        if b.relevance:
            lines.append(f"Why CGS: {b.relevance}")
        facts = [f for f in b.facts or [] if f.get("text")][:6]
        if facts:
            lines.append("Facts:")
            lines += [f"- {f['text']}" + (f" ({f['src']}" + (f", {f['date']}" if f.get("date") else "") + ")" if f.get("src") else "") for f in facts]
        if b.unknowns:
            lines.append("Still unknown: " + "; ".join(str(u) for u in b.unknowns[:5]))
    lines.append(f"Accepted at Step 4 by a person; owner {o.owner or 'not set'}, rating {o.rating}.")
    return "\n".join(lines)


def qualification_note(o: m.Opportunity) -> str:
    lines = [f"Approved as Qualification in the CGS lead workspace ({o.id})."]
    need = {k: v for k, v in (o.need or {}).items() if v}
    if need:
        lines.append("Need: " + "; ".join(f"{k}: {v}" for k, v in need.items()))
    scores = {k: v for k, v in (o.scores or {}).items() if v is not None}
    if scores:
        lines.append("Scores: " + ", ".join(f"{k} {v}" for k, v in scores.items()))
    if o.revenue:
        lines.append(f"Revenue estimate: {o.revenue['low']:,} to {o.revenue['high']:,}")
    return "\n".join(lines)


def deal_record(o: m.Opportunity) -> dict:
    ct = o.contact
    first, last = split_name(ct.name if ct else None)
    need = o.need or {}
    return {"workspace_id": o.id, "company": o.company.name, "website": f"https://{o.company.domain}" if o.company.domain else "",
            "first": first, "last": last, "title": ct.title if ct and ct.title != "Contact to confirm" else "", "email": (ct.email if ct else "") or "",
            "name": f"{o.company.name} - {o.title}", "owner": o.owner,
            "details": (need.get("problem") or o.target_problem or (o.brief.hypothesis if o.brief else "") or "")[:1900]}


# ---------- the sync ----------
def sync_one(c, o: m.Opportunity, crm) -> list[str]:
    """Bring the CRM in line with this opportunity. Returns what was done. Raises CrmError."""
    link = o.crm_link
    if link is None:
        link = m.CrmLink(opportunity_id=o.id, provider=crm.name, notes_sent=[])
        c.s.add(link)
        o.crm_link = link
    did: list[str] = []
    rec = lead_record(o)
    rec["lead_type"] = crm.cfg.get("leadType") or None
    if o.stage in NOT_YET:
        return did
    if not link.lead_id:
        if o.stage == "rejected":
            return did
        found = crm.find_lead(workspace_id=o.id, email=rec["email"])
        if found:
            link.lead_id, link.lead_url, link.mailbox = found["id"], found["url"], found["mailbox"]
            did.append(f"linked to the existing lead {found['name'] or found['id']} (no duplicate created)")
            if found["converted"]:
                link.converted = True
            else:
                link.status = crm.save_lead(rec, link.lead_id)["status"]
        else:
            new = crm.save_lead(rec)
            link.lead_id, link.lead_url, link.mailbox, link.status = new["id"], new["url"], new["mailbox"], new["status"]
            crm.add_note("lead", link.lead_id, "Research brief (CGS workspace)", brief_note(o))
            did.append(f"lead created ({new['status']})")
    elif not link.converted:
        cur = crm.get_lead(link.lead_id)
        if cur is None:
            raise CrmError(f"The CRM lead {link.lead_id} was deleted. Clear the link (POST /api/opportunities/{o.id}/crm/unlink) to create a new one.")
        if cur["converted"]:
            link.converted = True
            did.append("the lead was converted in the CRM; it is no longer updated")
        else:
            out = crm.save_lead(rec, link.lead_id)
            if out["status"] != link.status:
                did.append(f"status {link.status or '?'} -> {out['status']}")
            link.status, link.mailbox = out["status"], out["mailbox"] or link.mailbox
    if o.stage == "qualification" and not link.deal_id:
        r = crm.convert(link.lead_id, deal_record(o))
        link.organisation_id, link.contact_id, link.deal_id, link.deal_url = r["organisation"], r["contact"], r["opportunity"], r["url"]
        link.converted, link.status = True, crm.cfg["statuses"].get("converted")
        crm.add_note("opportunity", link.deal_id, "Qualification (CGS workspace)", qualification_note(o))
        did.append("converted: organization, contact and opportunity at the qualification stage")
    sent = list(link.notes_sent or [])
    for n in c.s.scalars(select(m.Note).where(m.Note.opportunity_id == o.id).order_by(m.Note.on, m.Note.id)):
        if n.id in sent:
            continue
        where = ("opportunity", link.deal_id) if link.deal_id else ("lead", link.lead_id)
        crm.add_note(where[0], where[1], f"{n.type} ({n.on.isoformat()}, {n.added_by or 'workspace'})", n.text)
        sent.append(n.id)
        link.notes_sent = list(sent)  # a new list, so the JSON column is saved
        did.append(f"note copied: {n.type}")
    return did


def sync(c, ids: list[str] | None = None) -> dict:
    """Sync the opportunities waiting for it (sync_pending or sync_failed), or the given ones. Commits after each."""
    out = {"synced": 0, "failed": 0, "errors": [], "off": False}
    raw = client(c)
    if c.settings.crm_mode != "write_approved" or raw is None:
        return {**out, "off": True}
    c.s.commit()  # the caller's own work is saved first: a failed sync rolls back only its own changes
    crm = TK.metered(raw, WHO, "crm", c)
    q = select(m.Opportunity).where(m.Opportunity.id.in_(ids)) if ids else select(m.Opportunity).where(
        m.Opportunity.crm_sync.in_(("sync_pending", "sync_failed")))
    for o in list(c.s.scalars(q)):
        try:
            did = sync_one(c, o, crm)
        except (CrmError, TK.LimitReached) as e:
            c.s.rollback()
            o = c.s.get(m.Opportunity, o.id)
            link = o.crm_link or m.CrmLink(opportunity_id=o.id, provider=raw.name, notes_sent=[])
            if o.crm_link is None:
                c.s.add(link)
            link.last_error, o.crm_sync = str(e)[:500], "sync_failed"
            log(c, o.id, f"Insightly sync failed: {e} The workspace is unchanged; it will try again (POST /api/crm/sync).")
            out["failed"] += 1
            out["errors"].append(f"{o.company.name}: {e}")
        else:
            link = o.crm_link
            link.last_error, link.synced_at = None, datetime.now(timezone.utc)
            o.crm_sync = "synced" if link.lead_id else "local_saved"
            if did:
                log(c, o.id, "Insightly: " + "; ".join(did) + ".")
            out["synced"] += 1
        c.s.commit()
    return out


def sync_all(c) -> dict:
    """Mark every opportunity the CRM should show as pending and sync them (fills an empty CRM from the workspace)."""
    if not writes_on(c):
        return {"synced": 0, "failed": 0, "errors": [], "off": True}
    for o in c.s.scalars(select(m.Opportunity).where(m.Opportunity.stage.not_in(NOT_YET))):
        if o.stage == "rejected" and not o.crm_link:
            continue
        o.crm_sync = "sync_pending"
    c.s.commit()
    return sync(c)


def after_request(app, ids: set[str], today) -> None:
    """Called once a person's request is saved: sync what it changed. Errors are recorded, never raised."""
    if not ids:
        return
    from api.services import Ctx
    try:
        with app.state.db.session() as s:
            c = Ctx(s=s, actor=WHO, today=today, settings=app.state.settings, crm_factory=lambda: getattr(app.state, "crm", None))
            sync(c, sorted(ids))
    except Exception:  # the person's action is already saved; the daily crm_sync job retries
        log_.exception("CRM sync after a request failed")


# ---------- reads for people and agents ----------
def view(o: m.Opportunity) -> dict | None:
    k = o.crm_link
    if k is None:
        return None
    return {"provider": k.provider, "leadId": k.lead_id, "leadUrl": k.lead_url, "status": k.status, "converted": k.converted,
            "opportunityId": k.deal_id, "opportunityUrl": k.deal_url, "lastError": k.last_error,
            "syncedAt": k.synced_at.isoformat() if k.synced_at else None}


def matches(c, o: m.Opportunity) -> dict:
    """What the CRM already holds for this company and person: shown at Step 4 so nobody adds a duplicate."""
    if not reads_on(c):
        return {"off": True, "mode": c.settings.crm_mode}
    crm = TK.metered(client(c), "Workspace (person)", "crm", c)
    email = (o.contact.email if o.contact else "") or ""
    try:
        found = crm.matches(company=o.company.name, email=email)
    except CrmError as e:
        return {"off": False, "error": str(e)}
    mine = o.crm_link.lead_id if o.crm_link else None
    found["leads"] = [x for x in found["leads"] if x["id"] != mine]
    open_leads = [x for x in found["leads"] if not x["converted"] and x["status"] not in (crm.cfg["statuses"].get("rejected"),)]
    found["summary"] = ("Nothing in the CRM for this company or person." if not any(found[k] for k in ("leads", "contacts", "organizations", "opportunities"))
                        else f"In the CRM: {len(found['leads'])} lead(s) ({len(open_leads)} open), {len(found['contacts'])} contact(s), "
                             f"{len(found['organizations'])} organization(s), {len(found['opportunities'])} opportunit{'y' if len(found['opportunities']) == 1 else 'ies'}.")
    return {"off": False, "linked": view(o), **found}


def mail_bcc(c, o: m.Opportunity) -> str:
    """The lead's own CRM address: BCC it on an email and the CRM logs that email on the lead."""
    if not writes_on(c) or not o.crm_link:
        return ""
    return o.crm_link.mailbox or ""


def unlink(c, o: m.Opportunity) -> None:
    if o.crm_link is not None:
        c.s.delete(o.crm_link)
        o.crm_link = None
    o.crm_sync = "local_saved"
    log(c, o.id, f"CRM link cleared by {c.actor}; the next sync looks the lead up again (by Workspace ID or email) or creates it.")
    mark(c, o)
