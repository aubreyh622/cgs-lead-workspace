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
  the next step               one CRM task per opportunity (linked to the lead, then to the CRM opportunity), renamed
                              and moved when the next step changes, marked done when there is none
Needs CRM_MODE=write_approved and a CRM (CRM_PROVIDER). Reads (matches) need read_only or write_approved.

No silent overwrite. After each write the link remembers what the CRM held (written). Before the next write the lead is
read: a field that changed in the CRM since then, and differs from the workspace, is not written; it becomes a
conflict a person decides on the opportunity's page (use the workspace value, or keep the CRM's; a kept field is
written again only once the workspace changes it). A lead the sync finds already in the CRM keeps its own values; the
workspace fills only the empty fields and writes a field again when it changes it.

The read-back (pull: the daily crm_sync job, POST /api/crm/pull) reads every linked lead, CRM opportunity and task:
edits made in the CRM become conflicts, a lead converted or deleted there, the CRM opportunity's stage and state (WON,
LOST) and a task done there are written to the opportunity's activity. It never changes a stage in the workspace.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging

from sqlalchemy import select

from api import models as m
from api import toolkit as TK
from api.toolkit.crm import FIELDS, CrmError, norm, split_name

WHO = "CRM sync"
NOT_YET = ("researching", "review")
TASK_STAGES = ("identified", "contacted", "active", "approval", "nurture", "qualification")
FIELD_LABELS = {"first": "first name", "last": "last name", "title": "job title", "email": "email address", "company": "company name",
                "website": "website", "industry": "industry", "rating": "rating", "description": "description", "status": "lead status",
                "target_problem": "target problem", "evidence_url": "evidence link", "priority_tier": "priority tier"}
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
    hyp = ((o.brief.hypothesis if o.brief else "") or "").strip()
    # The title once: a hypothesis that already starts with it ("Finance transformation may need...") is enough.
    about = hyp if hyp.lower().startswith((o.title or "").lower().strip()) else f"{o.title}. {hyp}".strip()
    return {"first": first, "last": last, "title": ct.title if ct and ct.title != "Contact to confirm" else "",
            "email": (ct.email if ct else "") or "", "company": o.company.name,
            "website": f"https://{o.company.domain}" if o.company.domain else "", "industry": o.company.industry,
            "rating": o.rating, "status": "active" if o.stage == "qualification" else o.stage,  # converted by convert(), not here
            "source": o.source_type, "owner": o.owner,
            "description": (about[:1900] + f"\n\nCGS workspace: {o.id}, owner {o.owner or 'not set'}."),
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


# ---------- no silent overwrite ----------
def desired_fields(crm, rec: dict) -> dict:
    """What the workspace wants the CRM lead to say, in the connector's comparable form."""
    want = {k: norm(rec.get(k)) for k in FIELDS if k != "status"}
    want["status"] = crm.cfg["statuses"].get(rec["status"], "") if rec.get("status") else ""
    return want


def compare(link: m.CrmLink, want: dict, remote: dict, dry: bool = False) -> tuple[set, dict]:
    """Which fields not to write (skip) and where the CRM and the workspace disagree (found: field -> (crm, workspace)).
    A field disagrees when the CRM changed it since the workspace last wrote it and holds something else than the
    workspace wants. dry: only look (the read-back); otherwise a kept field the workspace changed since is released."""
    written, kept = link.written or {}, dict(link.kept or {})
    skip, found = set(), {}
    for k in FIELDS:
        d, r = want.get(k, ""), remote.get(k, "")
        if k in kept:
            if kept[k] == d:
                skip.add(k)
                continue
            kept.pop(k)  # the workspace changed this field since a person kept the CRM's value: the new value goes out
        base = written.get(k, r)  # nothing remembered (a link made before the read-back existed): the CRM as it is
        if r == base or r == d or d == "":
            continue
        skip.add(k)
        found[k] = (r, d)
    if not dry:
        link.kept = kept or None
    return skip, found


def short(v: str, n: int = 80) -> str:
    v = " ".join(str(v).split())
    return v if len(v) <= n else v[: n - 1] + "…"


def set_conflicts(c, o: m.Opportunity, link: m.CrmLink, found: dict) -> int:
    """Keep the open disagreements; write each new one (or a changed one) to the activity once. Returns how many are new."""
    old = {x["field"]: x for x in link.conflicts or []}
    out, new = [], 0
    for k, (r, d) in found.items():
        prev = old.get(k)
        if not prev or prev["crm"] != r or prev["workspace"] != d:
            new += 1
            log(c, o.id, f"Insightly: the {FIELD_LABELS[k]} was changed there to \"{short(r) or '(empty)'}\"; the workspace has "
                         f"\"{short(d)}\". Insightly keeps its value until a person decides on this opportunity's page.")
        out.append({"field": k, "label": FIELD_LABELS[k], "crm": r, "workspace": d, "on": (prev or {}).get("on") or c.today.isoformat()})
    link.conflicts = out or None
    return new


def task_plan(o: m.Opportunity, link: m.CrmLink) -> tuple[str, str | None, str, str, str]:
    """The next step as the CRM task should show it: title, due date, record, record id, and a key for "unchanged"."""
    want = (o.next_action or "").strip() if o.stage in TASK_STAGES else ""
    record, rid = ("opportunity", link.deal_id) if link.deal_id else ("lead", link.lead_id)
    due = o.next_due.isoformat() if o.next_due else None
    return want, due, record, str(rid), f"{want}|{due or ''}|{record}:{rid}"[:400]


def sync_task(o: m.Opportunity, link: m.CrmLink, crm) -> list[str]:
    want, due, record, rid, key = task_plan(o, link)
    if key == link.task_key:
        return []
    did = []
    old_title = (link.task_key or "").split("|")[0]
    same_record = bool(link.task_key) and link.task_key.endswith(f"|{record}:{rid}")
    cur = crm.get_task(link.task_id) if link.task_id else None
    open_task = cur is not None and not cur["completed"]
    if want and open_task and same_record:
        crm.save_task(record, rid, want, due, o.owner, task_id=link.task_id)
        did.append(f"task updated: {want}")
    else:
        if open_task:  # no next step any more, or it moves from the lead to the CRM opportunity
            crm.save_task(record, rid, old_title or cur["title"], None, task_id=link.task_id, done=True)
            did.append(f"task marked done: {old_title or cur['title']}")
        link.task_id = None
        if want:
            t = crm.save_task(record, rid, want, due, o.owner)
            link.task_id = t["id"]
            did.append(f"task added: {want}")
    link.task_key = key
    link.remote = {**(link.remote or {}), "taskDone": False if link.task_id else (link.remote or {}).get("taskDone")}
    return did


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
                want, have = desired_fields(crm, rec), found["fields"]
                if found.get("workspace_id") != o.id:
                    # Someone else's lead (found by email): it keeps its own values; the workspace fills the empty fields
                    # and writes a field again only once it changes it.
                    link.kept = {k: want[k] for k in FIELDS if have.get(k) and have[k] != want[k]} or None
                link.written = dict(have)
                skip, _ = compare(link, want, have)
                out = crm.save_lead(rec, link.lead_id, skip=skip)
                link.status, link.written = out["status"], dict(out["fields"])
                if link.kept:
                    did.append(f"{len(link.kept)} field(s) kept as the CRM has them")
        else:
            new = crm.save_lead(rec)
            link.lead_id, link.lead_url, link.mailbox, link.status = new["id"], new["url"], new["mailbox"], new["status"]
            link.written = dict(new["fields"])
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
            want, have = desired_fields(crm, rec), cur["fields"]
            skip, found = compare(link, want, have)
            if any(want[k] and want[k] != have.get(k) for k in FIELDS if k not in skip):
                out = crm.save_lead(rec, link.lead_id, skip=skip)
            else:
                out = cur  # nothing to write: the CRM already says what the workspace says
            written = link.written or {}
            link.written = {k: (written.get(k, have.get(k, "")) if k in found else out["fields"].get(k, "")) for k in FIELDS}
            if set_conflicts(c, o, link, found):
                did.append(f"{len(found)} field(s) changed in the CRM kept for a person to decide")
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
    if link.lead_id:
        did += sync_task(o, link, crm)
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
    from api.undo import held
    for o in list(c.s.scalars(q)):
        if held(o):
            continue  # a Step 4 decision that can still be undone: it goes to the CRM once the window ends (api/undo.py)
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


# ---------- the read-back ----------
def pull_one(c, o: m.Opportunity, crm) -> int:
    """Read what the CRM holds for this opportunity now and note what changed there. Returns how many changes were new.
    Never writes to the CRM and never moves the workspace stage."""
    link = o.crm_link
    rem, new = dict(link.remote or {}), 0
    if not link.converted:
        lead = crm.get_lead(link.lead_id)
        if lead is None:
            if not rem.get("deleted"):
                new += 1
                log(c, o.id, "Insightly: the lead was deleted there. Clear the CRM link to create it again, or leave it out of the CRM.")
            rem["deleted"] = True
            link.last_error = f"The CRM lead {link.lead_id} was deleted in the CRM."
        elif lead["converted"]:
            link.converted = True
            new += 1
            log(c, o.id, "Insightly: the lead was converted there by a person. The workspace no longer updates it.")
        else:
            rem.pop("deleted", None)
            rec = lead_record(o)
            want = desired_fields(crm, rec)
            skip, found = compare(link, want, lead["fields"], dry=True)
            new += set_conflicts(c, o, link, found)
            rem.update(status=lead["status"], updated=lead["updated"])
            out_of_step = any(want[k] and want[k] != lead["fields"].get(k) for k in FIELDS if k not in skip)
            if writes_on(c) and o.stage not in NOT_YET and (out_of_step or task_plan(o, link)[4] != link.task_key):
                o.crm_sync = "sync_pending"  # the workspace changed something the CRM does not show yet
    if link.deal_id:
        d = crm.get_deal(link.deal_id)
        if d is None:
            if not rem.get("dealDeleted"):
                new += 1
                log(c, o.id, "Insightly: the opportunity was deleted there.")
            rem["dealDeleted"] = True
        else:
            moved = (d["stage"], d["state"]) != (rem.get("dealStage"), rem.get("dealState"))
            first = "dealStage" not in rem
            if moved and not (first and d["state"] == "OPEN" and d["stage"] == crm.cfg.get("qualificationStage")):
                new += 1
                log(c, o.id, f"Insightly: the opportunity is now {d['state'].lower() or 'open'}, at the stage {d['stage'] or '(none)'}.")
            rem.update(dealStage=d["stage"], dealState=d["state"])
    if link.task_id:
        t = crm.get_task(link.task_id)
        done = t is None or t["completed"]
        if t is not None and done and not rem.get("taskDone"):
            new += 1
            log(c, o.id, f"Insightly: the task \"{short(t['title'])}\" was marked done there.")
        if t is None:
            link.task_id = None  # deleted there: the next sync adds the next step again
        rem["taskDone"] = done
    link.remote = rem
    link.pulled_at = datetime.now(timezone.utc)
    return new


def pull(c, ids: list[str] | None = None) -> dict:
    """The read-back for every opportunity with a CRM lead (or the given ones). Commits after each."""
    out = {"read": 0, "changes": 0, "failed": 0, "errors": [], "off": False}
    raw = client(c)
    if not reads_on(c):
        return {**out, "off": True}
    c.s.commit()
    crm = TK.metered(raw, WHO, "crm", c)
    q = select(m.Opportunity).join(m.CrmLink).where(m.CrmLink.lead_id.is_not(None))
    if ids:
        q = q.where(m.Opportunity.id.in_(ids))
    for oid in [o.id for o in c.s.scalars(q)]:
        o = c.s.get(m.Opportunity, oid)
        try:
            n = pull_one(c, o, crm)
        except (CrmError, TK.LimitReached) as e:
            c.s.rollback()
            out["failed"] += 1
            out["errors"].append(f"{c.s.get(m.Opportunity, oid).company.name}: {e}")
            continue
        out["read"] += 1
        out["changes"] += n
        c.s.commit()
    return out


def resolve(c, o: m.Opportunity, field: str, use: str) -> None:
    """A person settles one disagreement: use the workspace value (it goes to the CRM now), or keep the CRM's."""
    from api.services import ActionError
    link = o.crm_link
    x = next((x for x in (link.conflicts or []) if x["field"] == field), None) if link else None
    if x is None:
        raise ActionError("There is nothing to decide for this field: the CRM and the workspace agree, or it was already decided.", 409, "no_conflict")
    if use == "workspace" and not writes_on(c):
        raise ActionError(f"Writing to the CRM is off (CRM_MODE={c.settings.crm_mode}).", 409, "crm_off")
    link.conflicts = [y for y in link.conflicts if y["field"] != field] or None
    link.written = {**(link.written or {}), field: x["crm"]}  # the CRM's edit is seen, so it no longer counts as new
    label = FIELD_LABELS.get(field, field)
    if use == "workspace":
        log(c, o.id, f"{c.actor} chose the workspace's {label} (\"{short(x['workspace'])}\") over Insightly's (\"{short(x['crm'])}\"). It goes to Insightly now.")
        mark(c, o)
    else:
        link.kept = {**(link.kept or {}), field: x["workspace"]}
        log(c, o.id, f"{c.actor} kept Insightly's {label} (\"{short(x['crm'])}\"). The workspace writes it again only when it changes here.")


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
    rem = k.remote or {}
    return {"provider": k.provider, "leadId": k.lead_id, "leadUrl": k.lead_url, "status": k.status, "converted": k.converted,
            "opportunityId": k.deal_id, "opportunityUrl": k.deal_url, "lastError": k.last_error,
            "syncedAt": k.synced_at.isoformat() if k.synced_at else None,
            "conflicts": k.conflicts or [], "kept": sorted((k.kept or {}).keys()),
            "dealStage": rem.get("dealStage"), "dealState": rem.get("dealState"), "taskId": k.task_id, "taskDone": rem.get("taskDone"),
            "pulledAt": k.pulled_at.isoformat() if k.pulled_at else None}


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
    return {"off": False, "linked": view(o), "searchedBy": ["company", "email"] if email else ["company"], **found}


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
