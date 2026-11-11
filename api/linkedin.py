"""LinkedIn outreach a person sends themselves (client meeting 4: "if it could recommend a way to reach out via LinkedIn,
that would be huge").

The workspace never opens, reads or posts on LinkedIn: LinkedIn's terms forbid automated access. What it does:
- writes a short connection note (at most NOTE_MAX characters, what a free LinkedIn account allows on an invitation)
  and a longer message for after the person accepts, from the research brief's facts and the email draft;
- gives a LinkedIn people search for the contact, which the person opens in their own browser;
- records that the person sent it. For a lead not yet contacted this is the first contact (Contacted, as "Record as
  sent" with LinkedIn); later it is noted in the activity.

The outreach agent writes the text in live mode (one AI call, no tools); demo mode uses a template. Code checks it: no
prices, percentages or guarantees, every number must come from the facts, and the note fits LinkedIn's limit."""
from __future__ import annotations

import json
import re

from api import models as m
from api import services as S

VERSION = "linkedin-1.0"
NOTE_MAX = 200      # characters in a connection note on a free LinkedIn account (premium allows 300)
MESSAGE_MAX = 1000  # a first message after the person accepts: short enough to read on a phone
STAGES = ("identified", "contacted", "nurture")
_CITE = re.compile(r"\[\[([^|\]]+)\|([^\]]+)\]\]")
FORBIDDEN = re.compile(r"[$%€£]|\bguarantee", re.I)

SYSTEM = """You write LinkedIn outreach for a consultant at CGS Advisors, a management consultancy. A person reads it,
edits it and sends it themselves; nothing is sent automatically.

Write two texts:
- note: a connection request note, at most 200 characters. One concrete reason to connect, taken from the facts. No pitch,
  no meeting request, no links.
- message: a first message for after they accept, at most 900 characters. Refer to one fact, say in one sentence why CGS
  might be useful for that kind of change, and end with a light question. Not a sales pitch.

Rules: use only the facts given; never invent numbers, names, results or clients. No prices, percentages, discounts or
guarantees. Plain sentences, no emojis, no hashtags. Sign the message with the sender's first name."""

SCHEMA = {"type": "object", "properties": {"note": {"type": "string"}, "message": {"type": "string"}},
          "required": ["note", "message"], "additionalProperties": False}


def search_url(o: m.Opportunity) -> str:
    from urllib.parse import quote
    c = o.contact
    who = (c.name if c and c.name else (c.title if c and c.title and c.title != "Contact to confirm" else "")) or ""
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote(" ".join(x for x in (who, o.company.name) if x))


def _facts(o: m.Opportunity) -> list[dict]:
    return list((o.brief.facts if o.brief else None) or [])


def _first(name: str | None) -> str:
    return (name or "").split(" ")[0] or "there"


def _fit(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip() if limit == NOTE_MAX else text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


def demo_text(o: m.Opportunity, sender: str) -> dict:
    """Demo mode: a template from the brief's first fact."""
    facts = _facts(o)
    first = _first(o.contact.name if o.contact else None)
    fact = facts[0]["text"].rstrip(".") if facts else f"the recent changes at {o.company.name}"
    note = f"Hi {first}, I read that {fact}. I work with leaders going through similar changes and would be glad to connect."
    if len(note) > NOTE_MAX:
        note = f"Hi {first}, I read about the recent news at {o.company.name} and would be glad to connect."
    message = (f"Hi {first},\n\nThanks for connecting. I read that {fact}. Changes like that often raise questions about "
               f"operating model, teams and timing, which is the work we do at CGS Advisors.\n\n"
               f"Would it be useful to compare notes on how other leaders have approached it?\n\n{_first(sender)}")
    return {"note": note, "message": message}


def check(d: dict, facts: list[dict]) -> str | None:
    """Why the text cannot be shown, or None. Numbers must come from the facts; no prices, percentages or guarantees."""
    source = " ".join(f.get("text", "") for f in facts)
    for key in ("note", "message"):
        text = d.get(key) or ""
        if not text.strip():
            return f"The {key} is empty."
        if FORBIDDEN.search(text):
            return f"The {key} mentions a price, a percentage or a guarantee."
        for n in re.findall(r"\d[\d,.]*", text):
            if n.strip(".,") not in source:
                return f"The {key} uses the number {n.strip('.,')}, which is not in the brief's facts."
    return None


def draft(c: S.Ctx, opp_id: str) -> dict:
    """The outreach agent writes a LinkedIn connection note and first message. Nothing is sent."""
    o = S.opp(c, opp_id, *STAGES)
    sender = o.owner or c.actor
    facts = _facts(o)
    if not facts and not c.demo:
        raise S.ActionError("The research brief has no facts to refer to yet. Research the company first.", 409, "no_facts")
    r = S.outreach_row(c, o)
    email = _CITE.sub(lambda g: g.group(2), r.body or "")

    def live(p):
        user = json.dumps({"to": {"name": o.contact.name if o.contact else None, "title": o.contact.title if o.contact else None,
                                  "company": o.company.name},
                           "sender": sender, "facts": [f.get("text", "") for f in facts][:8],
                           "email_draft_already_written": email[:1500] or None,
                           "stage": "not contacted yet" if o.stage == "identified" else "emailed, no reply yet" if o.stage == "contacted" else "in nurture"},
                          ensure_ascii=False)
        return p.json(SYSTEM, user, SCHEMA, "linkedin_outreach")

    d, _ = S.run_agent(c, "Outreach agent", o, "linkedin-demo-1.0", lambda: demo_text(o, sender), live=live, live_prompt=VERSION)
    d = {"note": _fit(_CITE.sub(lambda g: g.group(2), d.get("note", "")), NOTE_MAX),
         "message": _fit(_CITE.sub(lambda g: g.group(2), d.get("message", "")), MESSAGE_MAX)}
    why = check(d, facts) if not c.demo else None
    if why:
        S.log(c, o.id, "s", f"The LinkedIn text the outreach agent wrote was not shown: {why}")
        raise S.ActionError(f"The outreach agent's LinkedIn text did not pass the checks: {why} Ask again.", 502, "ai_check_failed")
    from api import people as P
    r.linkedin = {**(r.linkedin or {}), **d, "by": P.label(c), "on": c.today.isoformat(), "version": VERSION if not c.demo else "linkedin-demo-1.0"}
    S.touch(c, o)
    S.log(c, o.id, "a", "Outreach agent wrote a LinkedIn connection note and message. Nothing was sent: a person sends it on LinkedIn.")
    return view(o, r)


def save(c: S.Ctx, opp_id: str, note: str, message: str) -> dict:
    """A person edits the LinkedIn text."""
    o = S.opp(c, opp_id, *STAGES)
    note, message = (note or "").strip(), (message or "").strip()
    if len(note) > NOTE_MAX:
        raise S.ActionError(f"A connection note can have at most {NOTE_MAX} characters on a free LinkedIn account; this has {len(note)}.", 400, "too_long")
    if len(message) > MESSAGE_MAX:
        raise S.ActionError(f"Keep the message under {MESSAGE_MAX} characters; this has {len(message)}.", 400, "too_long")
    if not note and not message:
        raise S.ActionError("Write the note or the message.", 400, "empty")
    r = S.outreach_row(c, o)
    from api import people as P
    r.linkedin = {**(r.linkedin or {}), "note": note, "message": message, "editedBy": P.label(c), "editedOn": c.today.isoformat()}
    S.touch(c, o)
    return view(o, r)


def record(c: S.Ctx, opp_id: str, what: str, follow_days: int) -> dict:
    """The person sent the note or message on LinkedIn themselves. A lead not contacted yet becomes Contacted."""
    from api import people as P
    if what not in ("note", "message"):
        raise S.ActionError("Say what you sent: note (a connection request) or message.", 400, "invalid")
    o = S.opp(c, opp_id, *STAGES)
    label = "LinkedIn connection request" if what == "note" else "LinkedIn message"
    if o.stage == "identified":
        S.mark_sent(c, opp_id, label, None, follow_days)  # the first contact: the lead becomes Contacted
    else:
        S.no_waiting_email(c, o)
        S.touch(c, o)
        S.log(c, o.id, "h", f"{label} sent by {P.label(c)}. The workspace did not send it.")
    r = S.outreach_row(c, o)
    sent = dict((r.linkedin or {}).get("sent") or {})
    sent[what] = c.today.isoformat()
    r.linkedin = {**(r.linkedin or {}), "sent": sent}
    return view(o, r)


def view(o: m.Opportunity, r: m.Outreach | None) -> dict:
    li = dict((r.linkedin if r else None) or {})
    return {"note": li.get("note", ""), "message": li.get("message", ""), "by": li.get("editedBy") or li.get("by"),
            "on": li.get("editedOn") or li.get("on"), "sent": li.get("sent") or {}, "search": search_url(o),
            "noteMax": NOTE_MAX, "messageMax": MESSAGE_MAX}
