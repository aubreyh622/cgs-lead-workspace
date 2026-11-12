"""The workspace's records in CGS's Google Drive.

CGS asked (handover, 2026-11-12) that its records live in Google Drive and the database hold only the working copy.
Records here are what people and agents wrote about an opportunity: drafts and sent emails, notes, the evidence a person
confirmed, the agents' notes (the manager's summary and the promises each side made, suggestions, AI proposals), the
contact plan, the research brief, and the decisions. Opportunities themselves stay in Insightly (the source of truth;
the workspace caches them). docs/DATA_OWNERSHIP.md says which table is a cache, which holds records, and which is
working state.

What is saved, in one folder (DRIVE_RECORDS_FOLDER_ID, else a folder "CGS Workspace Records" the workspace makes):
  - one Google Doc per opportunity, "<company> - <title> [<id>]", rebuilt from the database each time it changes;
  - one Google Sheet "Workspace activity log": every line of the activity history (newest first, up to LOG_ROWS).
A record is saved again only when its content changed (a hash in drive_records); a failure is kept on the row and
tried again next time, never blocking the work itself. A Doc someone deleted in Drive is made again.

When: right after a person's change (the request queues the job drive_records), once a day by the patrol, and on
POST /api/drive/records/save. Who: "Drive records" (rules, no AI), metered in connector_uses on the drive connector.
Needs DRIVE_RECORDS=on, a Drive (DRIVE_PROVIDER) and the drive connector on for everyone. Records never reach an AI.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
from html import escape
import io
import os

from sqlalchemy import select

from api import models as m
from api import toolkit as TK
from api.toolkit.drive import DriveError, records_on

WHO = "Drive records"
FOLDER_NAME = "CGS Workspace Records"
FOLDER_SETTING = "drive_records_folder"
LAST = "drive_records_last"
LOG_ROWS = 5000
SAVED_LINE = "Records saved to Google Drive"
SKIP_STAGES = ("researching",)  # still a candidate being researched: nothing a person wrote yet


def drive_client(c):
    f = getattr(c, "drive_factory", None)
    return f() if f else None


def status_off(c) -> str:
    """Why records cannot be saved now, or "" when they can."""
    if not records_on():
        return "Saving records to Drive is off (DRIVE_RECORDS=on in .env turns it on)."
    if drive_client(c) is None:
        return "Google Drive is off (DRIVE_PROVIDER in .env)."
    if not TK.connector_detail("drive", getattr(c, "s", None))["on"]:
        return "The drive connector is off for every agent (config/toolkit.json, or switch it on in the workspace)."
    return ""


def queue(c, reason: str) -> None:
    """Ask for a save soon (a person changed something). Nothing happens when records are off or a save is already queued."""
    if status_off(c):
        return
    from api import jobs as J
    J.enqueue(c.s, c.today, "drive_records", None, "event", reason, requested_by=getattr(c, "actor", "") or WHO)


# ---------- what a record says ----------
def _p(text) -> str:
    return "".join(f"<p>{escape(line)}</p>" for line in str(text or "").splitlines() if line.strip()) or "<p>-</p>"


def _v(v) -> str:
    """A value as a person reads it: {"B": 4, "A": null} -> "B 4, A pending"; True -> yes."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, dict):
        return ", ".join(f"{k} {_v(x) if x is not None else 'pending'}" for k, x in v.items())
    if isinstance(v, list):
        return "; ".join(_v(x) for x in v)
    return str(v)


def _what(kind: str, key: str) -> str:
    return key if kind == key else f"{kind} {key}"


def _rows(head: list[str], rows: list[list]) -> str:
    if not rows:
        return "<p>None.</p>"
    th = "".join(f"<th>{escape(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{escape(_v(v))}</td>" for v in r) + "</tr>" for r in rows)
    return f"<table border='1' cellpadding='4'><tr>{th}</tr>{body}</table>"


def _d(x) -> str:
    return x.isoformat()[:16].replace("T", " ") if x else ""


def doc_name(o: m.Opportunity) -> str:
    return f"{o.company.name if o.company else 'Company'} - {o.title} [{o.id}]"[:290]


def opportunity_html(c, o: m.Opportunity) -> str:
    """The opportunity's record as HTML (Drive turns it into a Google Doc). No timestamps of the save itself, so the same
    content gives the same hash."""
    s = c.s
    out = [f"<h1>{escape(doc_name(o))}</h1>"]
    contact = o.contact
    link = o.crm_link
    out.append(_rows(["Field", "Value"], [
        ["Company", o.company.name if o.company else ""], ["Contact", f"{contact.name or 'to confirm'} ({contact.title})" if contact else ""],
        ["Stage", o.stage], ["Owner", o.owner or ""], ["Target problem", o.target_problem or ""],
        ["Next action", f"{o.next_action or ''} {('by ' + o.next_due.isoformat()) if o.next_due else ''}".strip()],
        ["Insightly", (link.lead_url if link and getattr(link, "lead_url", None) else "") or (f"lead {link.lead_id}" if link and link.lead_id else "not in the CRM yet")],
        ["Workspace id", o.id]]))
    out.append("<p><i>Saved by the CGS lead workspace. The workspace keeps a working copy and saves this document again "
               "whenever the record changes; edits made here are not read back.</i></p>")

    b = o.brief
    out.append("<h2>Research brief</h2>")
    if b:
        out.append(_p(b.hypothesis))
        out.append(_rows(["Fact", "Source", "Date"], [[f.get("text", ""), f.get("src", ""), f.get("date", "")] for f in (b.facts or [])]))
        rec = b.recommendation or {}
        if rec:
            out.append(_p(f"Recommendation: {rec.get('decision', '')}. {rec.get('reason', '')}"))
    else:
        out.append("<p>No brief.</p>")

    r = s.get(m.Outreach, o.id)
    plan = ((r.plan or {}).get("plan") if r else None) or {}
    if plan:
        out.append("<h2>Contact plan</h2>")
        out.append(_p(f"Approach: {plan.get('approach', '')}. {plan.get('approach_why', '')} Status: {(r.plan or {}).get('status', '')}"))
        out.append(_rows(["Day", "Channel", "To", "Purpose", "Status"], [[x.get("day"), x.get("channel"), x.get("to"), x.get("purpose"), x.get("status", "")]
                                                                         for x in plan.get("steps") or []]))

    out.append("<h2>Drafts and emails</h2>")
    if r and (r.subject or r.body):
        out.append(f"<h3>Step 5 draft ({escape(r.status)})</h3>" + _p(f"Subject: {r.subject or ''}") + _p(r.body))
        if r.sent_on:
            out.append(_p(f"Sent {r.sent_on.isoformat()} via {r.sent_via or ''}."))
        if r.reply_on:
            out.append(f"<h3>Client reply ({r.reply_on.isoformat()})</h3>" + _p(r.reply_text))
    if r and r.linkedin:
        li = r.linkedin
        out.append("<h3>LinkedIn</h3>" + _p(f"Note: {li.get('note', '')}") + _p(f"Message: {li.get('message', '')}"))
    emails = s.scalars(select(m.OutboundEmail).where(m.OutboundEmail.opportunity_id == o.id).order_by(m.OutboundEmail.created_at)).all()
    for e in emails:
        out.append(f"<h3>Email {escape(e.purpose)} to {escape(e.to)} ({escape(e.status)})</h3>"
                   + _p(f"Approved by {e.by}" + (f", second approval {e.approved_by}" if e.approved_by else "")
                        + (f"; sent {_d(e.sent_at)}" if e.sent_at else "")) + _p(f"Subject: {e.subject}") + _p(e.body))
    drafts = s.scalars(select(m.InboxItem).where(m.InboxItem.opportunity_id == o.id, m.InboxItem.kind == "draft")
                       .order_by(m.InboxItem.created_at)).all()
    for it in drafts:
        out.append(f"<h3>Agent draft: {escape(it.title)} ({escape(it.status)}, {it.created_on.isoformat()})</h3>"
                   + _p(f"Subject: {it.data.get('subject', '')}") + _p(it.data.get("body", "")))

    out.append("<h2>Notes</h2>")
    notes = s.scalars(select(m.Note).where(m.Note.opportunity_id == o.id).order_by(m.Note.on, m.Note.id)).all()
    for n in notes:
        out.append(f"<h3>{escape(n.type)}, {n.on.isoformat()}{(' - ' + escape(n.added_by)) if n.added_by else ''}</h3>" + _p(n.text))
        if (n.analysis or {}).get("summary"):
            out.append(_p(f"Evidence agent: {n.analysis['summary']}"))
    if not notes:
        out.append("<p>None.</p>")

    out.append("<h2>Agent notes</h2>")
    mem = s.get(m.OpportunityMemory, o.id)
    if mem and (mem.summary or mem.commitments):
        out.append("<h3>Manager's summary</h3>" + _p(mem.summary))
        out.append(_rows(["Who", "Promised", "Due", "Status", "Quote"], [[x.get("who"), x.get("what"), x.get("due") or "", x.get("status"), x.get("quote")]
                                                                       for x in mem.commitments or []]))
    props = s.scalars(select(m.Proposal).where(m.Proposal.opportunity_id == o.id).order_by(m.Proposal.id)).all()
    out.append("<h3>AI proposals</h3>" + _rows(["What", "Value", "Quote", "Why", "Status", "Decided by"],
                                                 [[_what(p.kind, p.key), p.final_value if p.status == "edited" else p.value, p.quote, p.why, p.status, p.decided_by or ""]
                                                  for p in props]))
    sugg = s.scalars(select(m.Suggestion).where(m.Suggestion.opportunity_id == o.id).order_by(m.Suggestion.id)).all()
    out.append("<h3>Suggested next actions</h3>" + _rows(["Action", "Why", "Status"], [[x.title, x.reason, x.status] for x in sugg]))

    out.append("<h2>Confirmed evidence</h2>")
    ev = s.scalars(select(m.Evidence).where(m.Evidence.opportunity_id == o.id).order_by(m.Evidence.id)).all()
    out.append(_rows(["What", "Value", "Quote", "How", "By", "On"], [[_what(x.kind, x.key), x.value, x.quote, x.how, x.by, x.on.isoformat()] for x in ev]))
    tasks = s.scalars(select(m.Task).where(m.Task.opportunity_id == o.id).order_by(m.Task.due)).all()
    out.append("<h3>Agreed actions</h3>" + _rows(["Action", "Side", "Who", "Due", "Status"], [[t.title, t.side, t.assignee, t.due.isoformat(), t.status] for t in tasks]))

    packs = s.scalars(select(m.Pack).where(m.Pack.opportunity_id == o.id).order_by(m.Pack.submitted_on, m.Pack.id)).all()
    if packs:
        out.append("<h2>Qualification packs</h2>" + _rows(
            ["Submitted", "By", "Status", "Scores", "Conditions", "Exception", "Out of date"],
            [[p.submitted_on.isoformat(), p.submitted_by, p.status, (p.snapshot or {}).get("scores"), (p.snapshot or {}).get("conditions"),
              p.exception or "", p.stale_reason or ""] for p in packs]))

    out.append("<h2>Decisions</h2>")
    dec = s.scalars(select(m.Decision).where(m.Decision.opportunity_id == o.id).order_by(m.Decision.id)).all()
    out.append(_rows(["Step", "Decision", "By", "On", "Note"], [[x.step, x.decision, x.by, x.on.isoformat(), x.note] for x in dec]))
    return "<html><body>" + "".join(out) + "</body></html>"


def log_csv(c) -> str:
    names = {o.id: (o.company.name if o.company else "") for o in c.s.scalars(select(m.Opportunity))}
    rows = c.s.scalars(select(m.Activity).order_by(m.Activity.id.desc()).limit(LOG_ROWS)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Day", "Time (UTC)", "Company", "Opportunity", "By", "What happened"])
    who = {"h": "person", "a": "agent", "s": "rules"}
    for a in rows:
        if a.actor_type == "s" and a.text.startswith(SAVED_LINE):
            continue  # the save's own line would change the log every time it is saved
        w.writerow([a.on.isoformat(), _d(a.at), names.get(a.opportunity_id or "", ""), a.opportunity_id or "", who.get(a.actor_type, a.actor_type), a.text])
    return buf.getvalue()


# ---------- saving ----------
def folder_id(c, drive) -> str:
    """The records folder: DRIVE_RECORDS_FOLDER_ID, else the one the workspace made before, else a new one."""
    named = os.environ.get("DRIVE_RECORDS_FOLDER_ID", "").strip()
    if named:
        if "/folders/" in named:
            named = named.split("/folders/", 1)[1].split("?", 1)[0].split("/", 1)[0]
        return named
    row = c.s.get(m.Setting, FOLDER_SETTING)
    if row and (row.value or {}).get("id") and drive.folder(row.value["id"]):
        return row.value["id"]
    fid = drive.find_folder(FOLDER_NAME) or drive.make_folder(FOLDER_NAME)
    if row is None:
        c.s.add(m.Setting(key=FOLDER_SETTING, value={"id": fid, "name": FOLDER_NAME}))
    else:
        row.value = {"id": fid, "name": FOLDER_NAME}
    return fid


def _save_one(c, drive, folder: str, key: str, opp_id: str | None, name: str, body: str, kind: str, out: dict) -> None:
    digest = hashlib.sha256(f"{name}\n{body}".encode("utf-8")).hexdigest()
    row = c.s.get(m.DriveRecord, key)
    if row is None:
        row = m.DriveRecord(key=key, opportunity_id=opp_id)
        c.s.add(row)
    if row.hash == digest and row.file_id and not row.error:
        out["unchanged"] += 1
        return
    try:
        saved = drive.save(folder, name, body, kind, row.file_id or "")
    except (DriveError, TK.LimitReached) as e:
        row.error = str(e)[:1000]
        out["errors"].append(f"{name}: {e}")
        return
    row.file_id, row.link, row.name, row.hash, row.error = saved["id"], saved.get("link", "")[:500], name[:300], digest, None
    row.saved_at = datetime.now(timezone.utc)
    out["saved"] += 1


def save(c) -> dict:
    """Save every record that changed. Never raises for a Drive problem: they are in errors and on the rows."""
    why = status_off(c)
    if why:
        return {"off": True, "why": why}
    drive = TK.metered(drive_client(c), WHO, "drive", c)
    out = {"saved": 0, "unchanged": 0, "errors": []}
    try:
        folder = folder_id(c, drive)
    except (DriveError, TK.LimitReached) as e:
        out["errors"].append(f"Records folder: {e}")
        return finish(c, out)
    out["folder"] = folder
    for o in c.s.scalars(select(m.Opportunity).where(m.Opportunity.stage.not_in(SKIP_STAGES)).order_by(m.Opportunity.id)):
        _save_one(c, drive, folder, f"opp:{o.id}", o.id, doc_name(o), opportunity_html(c, o), "doc", out)
    _save_one(c, drive, folder, "log", None, "Workspace activity log", log_csv(c), "sheet", out)
    return finish(c, out)


def finish(c, out: dict) -> dict:
    c.s.flush()
    out["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["on"] = c.today.isoformat()
    row = c.s.get(m.Setting, LAST)
    if row is None:
        c.s.add(m.Setting(key=LAST, value=out))
    else:
        row.value = out
    if out["saved"] or out["errors"]:
        text = f"{SAVED_LINE}: {out['saved']} saved, {out['unchanged']} unchanged."
        if out["errors"]:
            text += f" {len(out['errors'])} problem(s): {out['errors'][0]}"
        c.s.add(m.Activity(opportunity_id=None, on=c.today, actor_type="s", text=text[:2000]))
    return out


def status(c) -> dict:
    """GET /api/drive/records: whether records are saved to Drive, where, the last save, and each record's file."""
    last = c.s.get(m.Setting, LAST)
    folder = c.s.get(m.Setting, FOLDER_SETTING)
    rows = c.s.scalars(select(m.DriveRecord).order_by(m.DriveRecord.key)).all()
    return {"on": records_on(), "off": status_off(c) or None, "folder": os.environ.get("DRIVE_RECORDS_FOLDER_ID", "").strip() or (folder.value if folder else None),
            "lastSave": last.value if last else None,
            "records": [{"key": r.key, "opportunityId": r.opportunity_id, "name": r.name, "link": r.link,
                         "savedAt": r.saved_at.isoformat() if r.saved_at else None, "error": r.error} for r in rows]}


def due(c, today) -> bool:
    """The patrol saves once a day (a person's change saves sooner)."""
    if status_off(c):
        return False
    last = c.s.get(m.Setting, LAST)
    return not last or (last.value or {}).get("on") != today.isoformat()
