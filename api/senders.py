"""Who wrote a client's email reply, and whether it is the contact on file.

A reply does not always come from the person the email went to: an assistant answers, or the contact forwards it to a
colleague, who replies. That is worth knowing (a new person may now be involved), and the workspace must not credit
the contact with someone else's words. Found in the real Gmail check of 8 Nov 2026: the reply card said "Reply from"
the contact on file and the manager's note named the contact as the sender, though someone else had written.

Rules, no AI:
- The sender is the contact when the address is the contact's address; when the contact has no address on file, when
  the names agree ("Reyes, Sam" and "Sam Reyes" agree).
- From the contact's address but signed with another full name: not taken for the contact either (sameAddress); the
  address on file, the contact or the mailbox needs checking, so no new contact is offered for it.
- Otherwise, if another contact at the same company has that address, the reply is from that contact.
- Otherwise the sender is someone new. A person may add them as a contact at the company with one click; the address
  comes from the email's own header, it is never guessed, and nothing is added without that click.
"""
from __future__ import annotations

from email.utils import parseaddr
import re

from sqlalchemy import func, select

from api import models as m


def _norm(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def parse(raw: str) -> dict:
    """'"Reyes, Sam" <sreyes@x.example>' -> {"name": "Sam Reyes", "email": "sreyes@x.example"}."""
    name, email = parseaddr(raw or "")
    name = name.strip().strip('"').strip()
    if name.count(",") == 1:  # "Last, First" as many company directories write it
        last, first = (x.strip() for x in name.split(","))
        if last and first:
            name = f"{first} {last}"
    return {"name": name, "email": email.strip().lower()}


def same_name(a: str | None, b: str | None) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a) and len(a.split()) >= 2 and a == b  # a first name alone is not enough to say it is the same person


def other_name(a: str | None, b: str | None) -> bool:
    """Two full names that are clearly different people ("Sam Reyes" and "Priya Raman"). A first name alone, or no
    name, never counts as different: a shared inbox or a short display name proves nothing."""
    a, b = _norm(a), _norm(b)
    return len(a.split()) >= 2 and len(b.split()) >= 2 and a != b


def identify(s, o: m.Opportunity, raw_from: str) -> dict:
    """{name, email, isContact, contact (the name on file), knownAs (another contact at the company with that address),
    sameAddress (written from the contact's address on file but signed with another full name)}."""
    who = parse(raw_from)
    ct = o.contact
    on_file = (ct.email or "").strip().lower() if ct else ""
    out = {**who, "contact": ct.name if ct else None, "isContact": False, "knownAs": None, "sameAddress": False}
    if ct and on_file and who["email"] == on_file:
        # The address on file was typed by a person when the email went out; a reply signed by someone else from it means
        # the address, the contact, or the mailbox is not what the workspace thinks. Found in the real Gmail check.
        if other_name(who["name"], ct.name):
            out["sameAddress"] = True
        else:
            out["isContact"] = True
        return out
    if ct and not on_file and same_name(who["name"], ct.name):
        out["isContact"] = True
        return out
    if who["email"]:
        other = s.scalars(select(m.Contact).where(m.Contact.company_id == o.company_id, func.lower(m.Contact.email) == who["email"],
                                                  m.Contact.id != (ct.id if ct else ""))).first()
        if other is not None:
            out["knownAs"] = other.name
    return out


def label(sender: dict) -> str:
    """How the sender is named on screen: 'Sam Reyes', or the address when the email gave no name."""
    return sender.get("name") or sender.get("email") or "someone"


def note_header(sender: dict) -> str:
    """The line put before the client's words in the notes when someone other than the contact wrote, so a reader (person
    or agent) never takes them for the contact's words. "" when the contact wrote."""
    if sender.get("isContact"):
        return ""
    who = f"{label(sender)} <{sender['email']}>" if sender.get("email") and sender.get("name") else label(sender)
    if sender.get("sameAddress"):
        return f"[From {who}: the address on file for {sender['contact']}, but signed {label(sender)}.]\n"
    on_file = f", not {sender['contact']} (the contact on file)" if sender.get("contact") else ""
    known = f"; on file as a contact at this company" if sender.get("knownAs") else ""
    return f"[From {who}{on_file}{known}.]\n"


def title_suffix(sender: dict) -> str:
    """What the reply's inbox title adds when someone other than the contact wrote."""
    if sender.get("isContact") or not sender.get("contact"):
        return ""
    return f", not {sender['contact']}"
