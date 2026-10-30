"""Mail connector: one interface, two mailboxes.

  GmailMail  the Gmail API (REST over httpx). Sign in once with `python -m api.toolkit.gmail_auth`. The Gmail credentials
             live in CREDENTIALS (~/.cgs-workspace/gmail.env, outside OneDrive and the project folder), not in the project
             .env, because the token can read the whole mailbox. Scopes: gmail.compose (drafts, send), gmail.readonly.
  MockMail   an in-memory mailbox for demo mode and tests. Nothing leaves the computer.

Outlook (Microsoft Graph) can be added later as a third class with the same methods.

The workspace calls create_draft, send_draft and send only after a person clicks; agents may only read a thread.
"""
from __future__ import annotations

import base64
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
import itertools
import os
from pathlib import Path
import re
import time

import httpx

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ("https://www.googleapis.com/auth/gmail.compose", "https://www.googleapis.com/auth/gmail.readonly")
PROVIDERS = ("off", "mock", "gmail")
CREDENTIALS = Path(os.environ.get("GMAIL_CREDENTIALS_FILE") or Path.home() / ".cgs-workspace" / "gmail.env")


def load_credentials() -> None:
    """Read the Gmail credentials file into the environment (values already set in the environment win)."""
    from api.settings import _load_dotenv
    _load_dotenv(CREDENTIALS)


class MailError(Exception):
    pass


# ---------- helpers shared by every mailbox ----------
def build_raw(to: str, subject: str, body: str, sender: str = "", reply_to_rfc_id: str = "", bcc: str = "") -> str:
    """A plain-text email. Gmail adds From (with the account's name), Date and Message-Id when it sends.
    bcc: a hidden copy, for example the CRM lead's own address so the CRM logs the email (the client does not see it)."""
    msg = EmailMessage()
    msg["To"] = to
    if bcc:
        msg["Bcc"] = bcc
    if sender:
        msg["From"] = sender
    msg["Subject"] = subject
    if reply_to_rfc_id:  # keeps a follow-up in the same conversation in the client's mail program
        msg["In-Reply-To"] = reply_to_rfc_id
        msg["References"] = reply_to_rfc_id
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


_QUOTE_START = re.compile(r"^(On .{5,200} wrote:|-----Original Message-----|From: .+|Sent from my \w+)\s*$", re.I)


def new_text(text: str) -> str:
    """The part of an email the writer wrote now: drops the quoted earlier messages below it."""
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if _QUOTE_START.match(line.strip()):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _body(payload: dict) -> str:
    """Plain text of a Gmail message payload (text/plain preferred, else text/html with the tags removed)."""
    plain, html = [], []

    def walk(p):
        mime, data = p.get("mimeType", ""), (p.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_b64(data))
        elif data and mime == "text/html":
            html.append(re.sub(r"<[^>]+>", " ", _b64(data)))
        for part in p.get("parts") or []:
            walk(part)

    walk(payload)
    return "\n".join(plain) if plain else re.sub(r"[ \t]+", " ", "\n".join(html))


# ---------- Gmail ----------
class GmailMail:
    name = "gmail"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str, http: httpx.Client | None = None):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.http = http or httpx.Client(timeout=30)
        self._token, self._expires, self._me = "", 0.0, ""

    @classmethod
    def from_env(cls, http: httpx.Client | None = None) -> "GmailMail":
        load_credentials()
        return cls(os.environ.get("GOOGLE_CLIENT_ID", ""), os.environ.get("GOOGLE_CLIENT_SECRET", ""),
                   os.environ.get("GMAIL_REFRESH_TOKEN", ""), http)

    def ready(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def _access(self) -> str:
        if not self.ready():
            raise MailError(f"Gmail is not signed in. Put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in {CREDENTIALS}, "
                            "run `python -m api.toolkit.gmail_auth`, then restart the workspace.")
        if self._token and time.time() < self._expires - 60:
            return self._token
        try:
            r = self.http.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                                "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
        except httpx.HTTPError as e:
            raise MailError(f"Could not reach Google ({type(e).__name__}).") from None
        if r.status_code != 200:
            raise MailError("Gmail sign-in was refused or has expired. Run `python -m api.toolkit.gmail_auth` again.")
        data = r.json()
        self._token, self._expires = data["access_token"], time.time() + int(data.get("expires_in", 3600))
        return self._token

    def _call(self, method: str, path: str, **kw) -> dict | None:
        try:
            r = self.http.request(method, API + path, headers={"Authorization": f"Bearer {self._access()}"}, **kw)
        except httpx.HTTPError as e:
            raise MailError(f"Could not reach Gmail ({type(e).__name__}).") from None
        if r.status_code == 404:
            return None
        if r.status_code in (401, 403):
            try:
                reason = (r.json().get("error") or {}).get("message", "")[:240]
            except ValueError:
                reason = ""
            if "has not been used in project" in reason or "is disabled" in reason:
                raise MailError("The Gmail API is not enabled in the Google Cloud project of this OAuth client. "
                                f"Enable it there and retry in a few minutes. Google says: {reason}")
            raise MailError("Gmail refused the request. Sign in again with `python -m api.toolkit.gmail_auth` "
                            "and allow drafting, sending and reading." + (f" Google says: {reason}" if reason else ""))
        if r.status_code >= 400:
            raise MailError(f"Gmail answered {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}

    def account(self) -> str:
        if not self._me:
            self._me = (self._call("GET", "/profile") or {}).get("emailAddress", "")
        return self._me

    def create_draft(self, to: str, subject: str, body: str, thread_id: str = "", reply_to_rfc_id: str = "", bcc: str = "") -> dict:
        msg = {"raw": build_raw(to, subject, body, reply_to_rfc_id=reply_to_rfc_id, bcc=bcc), **({"threadId": thread_id} if thread_id else {})}
        d = self._call("POST", "/drafts", json={"message": msg}) or {}
        return {"draft_id": d.get("id", ""), "thread_id": (d.get("message") or {}).get("threadId", "")}

    def send_draft(self, draft_id: str) -> dict:
        d = self._call("POST", "/drafts/send", json={"id": draft_id})
        if d is None:
            raise MailError("That Gmail draft no longer exists.")
        return {"message_id": d.get("id", ""), "thread_id": d.get("threadId", "")}

    def send(self, to: str, subject: str, body: str, thread_id: str = "", reply_to_rfc_id: str = "", bcc: str = "") -> dict:
        msg = {"raw": build_raw(to, subject, body, reply_to_rfc_id=reply_to_rfc_id, bcc=bcc), **({"threadId": thread_id} if thread_id else {})}
        d = self._call("POST", "/messages/send", json=msg) or {}
        return {"message_id": d.get("id", ""), "thread_id": d.get("threadId", "")}

    def draft_exists(self, draft_id: str) -> bool:
        return self._call("GET", f"/drafts/{draft_id}", params={"format": "minimal"}) is not None

    def thread(self, thread_id: str) -> list[dict]:
        t = self._call("GET", f"/threads/{thread_id}", params={"format": "full"})
        if t is None:
            return []
        me = self.account().lower()
        out = []
        for msg in t.get("messages", []):
            if "DRAFT" in (msg.get("labelIds") or []):
                continue
            h = {x["name"].lower(): x["value"] for x in (msg.get("payload") or {}).get("headers", [])}
            sender = parseaddr(h.get("from", ""))[1].lower()
            when = datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000, tz=timezone.utc).date()
            out.append({"id": msg["id"], "from": h.get("from", ""), "subject": h.get("subject", ""), "date": when.isoformat(),
                        "rfc_id": h.get("message-id", ""), "ours": "SENT" in (msg.get("labelIds") or []) or (bool(me) and sender == me),
                        "text": new_text(_body(msg.get("payload") or {}))})
        return out


# ---------- mock ----------
class MockMail:
    """An in-memory mailbox. deliver_reply() plays the client answering, for demos and tests."""
    name = "mock"

    def __init__(self, me: str = "you@cgs-demo.example", today=date.today):
        self.me, self.today = me, today
        self.drafts: dict[str, dict] = {}
        self.threads: dict[str, list[dict]] = {}
        self.outbox: list[dict] = []  # every message "sent", for tests
        self._ids = itertools.count(1)

    def _id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids)}"

    def ready(self) -> bool:
        return True

    def account(self) -> str:
        return self.me

    def create_draft(self, to, subject, body, thread_id="", reply_to_rfc_id="", bcc="") -> dict:
        tid = thread_id or self._id("t")
        self.threads.setdefault(tid, [])
        did = self._id("d")
        self.drafts[did] = {"to": to, "subject": subject, "body": body, "thread_id": tid, "bcc": bcc}
        return {"draft_id": did, "thread_id": tid}

    def _deliver(self, to, subject, body, tid, bcc="") -> dict:
        mid = self._id("m")
        msg = {"id": mid, "from": self.me, "to": to, "subject": subject, "date": self.today().isoformat(), "rfc_id": f"<{mid}@mock>",
               "ours": True, "text": body, "bcc": bcc}
        self.threads.setdefault(tid, []).append(msg)
        self.outbox.append(msg)
        return {"message_id": mid, "thread_id": tid}

    def send_draft(self, draft_id) -> dict:
        d = self.drafts.pop(draft_id, None)
        if d is None:
            raise MailError("That Gmail draft no longer exists.")
        return self._deliver(d["to"], d["subject"], d["body"], d["thread_id"], d.get("bcc", ""))

    def send(self, to, subject, body, thread_id="", reply_to_rfc_id="", bcc="") -> dict:
        return self._deliver(to, subject, body, thread_id or self._id("t"), bcc)

    def draft_exists(self, draft_id) -> bool:
        return draft_id in self.drafts

    def thread(self, thread_id) -> list[dict]:
        return [dict(x) for x in self.threads.get(thread_id, [])]

    def deliver_reply(self, thread_id: str, sender: str, text: str, on: date | None = None) -> str:
        mid = self._id("m")
        self.threads.setdefault(thread_id, []).append({"id": mid, "from": sender, "to": self.me, "subject": "Re:", "rfc_id": f"<{mid}@mock>",
                                                       "date": (on or self.today()).isoformat(), "ours": False, "text": new_text(text)})
        return mid


def make_mail(provider: str, app_mode: str):
    """The mailbox for these settings, or None when mail is off. Empty provider: mock in demo mode, off in live mode."""
    p = provider or ("mock" if app_mode == "demo" else "off")
    if p == "off":
        return None
    if p == "mock":
        return MockMail()
    return GmailMail.from_env()

