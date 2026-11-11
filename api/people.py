"""People and roles: who is an admin, which email address each person sends from, and who may act as someone else.

Roles (client meeting 4): an admin can change the scoring criteria, the team's research direction, the agents'
permissions and instructions, make someone else an admin, and act as another person (for example send a follow-up as
the consultant who has been leading the conversation). Everyone else acts only as themselves.

Where the roles live: CGS_ADMINS in .env names the first admins. Once an admin changes the roles in the workspace
(People page), the database list (Setting "admins") is used and CGS_ADMINS is only the starting point. While nobody is
an admin, everyone may do admin work, as before, so a fresh copy is never locked; the page says so.

Signed in vs acting as: X-User is the signed-in person (Google sign-in replaces it with the real account, api/main.py);
X-Act-As names the person an admin acts as. Every change records the acting person, and the signed-in admin where
they differ ("Alex Morgan as Sarah Patel")."""
from __future__ import annotations

import re

from api import models as m
from api.services import ActionError

EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


class RoleError(ActionError):
    def __init__(self, message: str, status: int = 403, code: str = "forbidden"):
        super().__init__(message, status, code)


def users(s) -> list[str]:
    row = s.get(m.Setting, "owners")
    return list(row.value) if row else []


def managed(s) -> bool:
    """True once the roles were changed in the workspace (then CGS_ADMINS no longer applies)."""
    return s.get(m.Setting, "admins") is not None


def admins(s, settings) -> list[str]:
    row = s.get(m.Setting, "admins")
    names = list(row.value) if row is not None else list(settings.admins)
    known = users(s)
    return [n for n in names if n in known] if known else names


def is_admin(s, settings, name: str) -> bool:
    """While nobody is an admin, everyone may do admin work (a fresh copy is never locked)."""
    names = admins(s, settings)
    return not names or name in names


def emails(s, settings) -> dict[str, str]:
    """name -> the address this person sends from: CGS_USERS emails, then what an admin set on the People page."""
    out = {n: e for e, n in settings.logins}
    row = s.get(m.Setting, "emails")
    out.update({k: v for k, v in dict(row.value if row else {}).items() if v})
    return out


def acting(s, settings, signed_in: str, act_as: str | None) -> str:
    """The person a request acts as. Raises RoleError when the signed-in person may not act as act_as."""
    if not act_as or act_as == signed_in:
        return signed_in
    if act_as not in users(s):
        raise RoleError(f"{act_as} is not a workspace user.", 400, "unknown_user")
    if not is_admin(s, settings, signed_in):
        raise RoleError(f"Only an admin can act as someone else. You are signed in as {signed_in}; "
                        f"ask an admin ({', '.join(admins(s, settings))}) to do this as {act_as}.", 403, "not_admin")
    return act_as


def label(c) -> str:
    """'Alex Morgan', or 'Alex Morgan as Sarah Patel' when an admin acts as someone else."""
    real = getattr(c, "signed_in", "") or ""
    return f"{real} as {c.actor}" if real and real != c.actor else c.actor


def require_admin(c, what: str) -> None:
    who = getattr(c, "signed_in", "") or c.actor
    if not is_admin(c.s, c.settings, who):
        raise RoleError(f"Only an admin can {what}. Admins: {', '.join(admins(c.s, c.settings))}.", 403, "not_admin")


def view(s, settings, mail_info=None) -> dict:
    """For the People page. mail_info(name) -> where that person's email goes out from (api/toolkit/mail.py)."""
    names, adm, mails = users(s), admins(s, settings), emails(s, settings)
    return {"people": [{"name": n, "email": mails.get(n, ""), "admin": n in adm,
                        **({"mailbox": mail_info(n)} if mail_info else {})} for n in names],
            "admins": adm, "everyoneIsAdmin": not adm,
            "source": "workspace" if managed(s) else ("CGS_ADMINS" if settings.admins else "none")}


def change(c, name: str, admin: bool | None = None, email: str | None = None) -> dict:
    """An admin makes someone an admin (or not), or sets the address they send from. Logged."""
    from api.services import log
    require_admin(c, "change people's roles or email addresses")
    if name not in users(c.s):
        raise RoleError(f"{name} is not a workspace user.", 404, "not_found")
    done = []
    if admin is not None:
        current = admins(c.s, c.settings)
        new = [n for n in current if n != name] + ([name] if admin else [])
        if current and not new:
            raise RoleError("At least one admin must remain: make someone else an admin first.", 409, "last_admin")
        row = c.s.get(m.Setting, "admins")
        if row is None:
            c.s.add(m.Setting(key="admins", value=new))
        else:
            row.value = new
        if (name in current) != admin:
            done.append(f"made {name} an admin" if admin else f"removed {name} from the admins")
    if email is not None:
        email = email.strip().lower()
        if email and not EMAIL_RE.match(email):
            raise RoleError("That is not an email address.", 400, "bad_email")
        row = c.s.get(m.Setting, "emails")
        value = dict(row.value if row else {})
        if value.get(name, "") != email:
            value[name] = email
            if row is None:
                c.s.add(m.Setting(key="emails", value=value))
            else:
                row.value = value
            done.append(f"set {name}'s sending address to {email}" if email else f"cleared {name}'s sending address")
    c.s.flush()
    for d in done:
        log(c, None, "h", f"{label(c)} {d}.")
    return {"changed": done}
