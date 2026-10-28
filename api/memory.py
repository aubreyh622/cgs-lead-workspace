"""The manager agent's memory of each opportunity (multi-agent step 7). Rules only: no AI here.

One notebook per opportunity (models.OpportunityMemory):
  summary      a running summary the manager rewrites as the opportunity moves (who is involved, what they want, where it stands)
  commitments  what each side promised to do, by when, with the sentence from a note that says so, word for word

The manager writes it during a triage (api/manager_agent.py); people can close a commitment by hand. An open commitment past
its date sends the opportunity back to the manager on the next patrol, so a promise nobody kept is noticed within days,
not months.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from api import models as m
from api.demo_agents import quote_in_text

SUMMARY_MIN, SUMMARY_MAX = 20, 1200
MAX_OPEN = 10            # open commitments per opportunity
NUDGE_AGAIN_DAYS = 7     # an overdue commitment goes back to the manager at most once a week
SIDES = ("client", "cgs")
CLOSED = ("done", "dropped")


class Invalid(ValueError):
    """A memory change that does not pass the checks. The message says why, in words the model can act on."""


def get(s, opp_id: str) -> m.OpportunityMemory | None:
    return s.get(m.OpportunityMemory, opp_id)


def _row(s, opp_id: str) -> m.OpportunityMemory:
    row = get(s, opp_id)
    if row is None:
        row = m.OpportunityMemory(opportunity_id=opp_id, summary="", commitments=[])
        s.add(row)
    return row


def _iso(value) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise Invalid("Give the due date as YYYY-MM-DD, or leave it empty when the notes give no date.")


def view(row: m.OpportunityMemory | None, today: date) -> dict:
    """What the manager (and the API) sees: the summary, and each commitment with whether it is overdue."""
    if row is None:
        return {"summary": "", "commitments": [], "updatedOn": None}
    out = []
    for c in row.commitments or []:
        due = _iso(c.get("due"))
        out.append({**c, "overdue": bool(c.get("status") == "open" and due and due < today),
                    "daysOverdue": (today - due).days if c.get("status") == "open" and due and due < today else 0})
    return {"summary": row.summary or "", "commitments": out, "updatedOn": row.updated_on.isoformat() if row.updated_on else None}


def set_summary(s, opp_id: str, text: str, today: date, run_id: str | None = None) -> None:
    text = " ".join((text or "").split())
    if not SUMMARY_MIN <= len(text) <= SUMMARY_MAX:
        raise Invalid(f"The summary must be {SUMMARY_MIN}-{SUMMARY_MAX} characters: who is involved, what they want, where it stands.")
    row = _row(s, opp_id)
    row.summary, row.updated_on, row.run_id = text, today, run_id or row.run_id


def add_commitment(s, opp_id: str, args: dict, notes_by_id: dict[str, dict], today: date, run_id: str | None = None) -> dict:
    """notes_by_id: {note id: {"text", "on"}} for this opportunity. Returns the saved commitment."""
    who, what = str(args.get("who") or "").strip(), str(args.get("what") or "").strip()
    side, quote = str(args.get("side") or "").strip().lower(), str(args.get("quote") or "").strip()
    note_id = str(args.get("note_id") or "").strip()
    if not 1 <= len(who) <= 80 or not 5 <= len(what) <= 300:
        raise Invalid("Say who promised (a name or role, up to 80 characters) and what they will do (5-300 characters).")
    if side not in SIDES:
        raise Invalid("side is client (the client promised) or cgs (someone at CGS promised).")
    if note_id not in notes_by_id:
        raise Invalid(f"Unknown note id {note_id!r}. Use one of {sorted(notes_by_id)}.")
    if not quote or not quote_in_text(quote, notes_by_id[note_id]["text"]):
        raise Invalid("The quote is not in that note word for word. Copy the sentence that states the promise exactly.")
    due = _iso(args.get("due"))
    written = notes_by_id[note_id].get("on")
    if due and written and due < written:
        raise Invalid(f"The due date {due.isoformat()} is before the note was written ({written.isoformat()}).")
    row = _row(s, opp_id)
    items = list(row.commitments or [])
    if any(c["status"] == "open" and c["quote"].strip().lower() == quote.lower() for c in items):
        raise Invalid("This commitment is already in memory.")
    if sum(c["status"] == "open" for c in items) >= MAX_OPEN:
        raise Invalid(f"{MAX_OPEN} open commitments is the limit. Close the ones that are done or no longer apply first.")
    item = {"id": f"c{len(items) + 1}", "who": who, "side": side, "what": what, "due": due.isoformat() if due else None,
            "quote": quote, "note_id": note_id, "status": "open", "added_on": today.isoformat(),
            "closed_on": None, "closed_by": None, "closed_why": "", "nudged_on": None}
    row.commitments = items + [item]  # a new list, so the JSON column is saved
    row.updated_on, row.run_id = today, run_id or row.run_id
    return item


def close_commitment(s, opp_id: str, cid: str, status: str, why: str, today: date, by: str) -> dict:
    if status not in CLOSED:
        raise Invalid("status is done (it happened) or dropped (it no longer applies).")
    why = (why or "").strip()
    if len(why) < 5:
        raise Invalid("Say in a few words why it is closed (what shows it happened, or why it no longer applies).")
    row = get(s, opp_id)
    items = [dict(c) for c in (row.commitments if row else [])]
    hit = next((c for c in items if c["id"] == cid), None)
    if not hit:
        raise Invalid(f"Unknown commitment {cid!r}.")
    if hit["status"] != "open":
        raise Invalid(f"Commitment {cid} is already {hit['status']}.")
    hit.update(status=status, closed_on=today.isoformat(), closed_by=by, closed_why=why[:300])
    row.commitments, row.updated_on = items, today
    return hit


def overdue(s, today: date) -> list[tuple[str, dict]]:
    """Open commitments past their date that the manager has not been sent back to this week: [(opportunity id, commitment)]."""
    out = []
    for row in s.scalars(select(m.OpportunityMemory)):
        for c in row.commitments or []:
            due, nudged = _iso(c.get("due")), _iso(c.get("nudged_on"))
            if c.get("status") == "open" and due and due < today and (not nudged or (today - nudged).days >= NUDGE_AGAIN_DAYS):
                out.append((row.opportunity_id, c))
    return out


def mark_nudged(s, opp_id: str, cid: str, today: date) -> None:
    row = get(s, opp_id)
    if row:
        row.commitments = [{**c, "nudged_on": today.isoformat()} if c["id"] == cid else c for c in row.commitments or []]


def brief(row: m.OpportunityMemory | None, today: date) -> dict:
    """A short version for the daily board."""
    v = view(row, today)
    open_ = [c for c in v["commitments"] if c["status"] == "open"]
    return {"summary": v["summary"][:300], "open_commitments": len(open_),
            "overdue": [f"{c['who']}: {c['what']} (due {c['due']})" for c in open_ if c["overdue"]]}

