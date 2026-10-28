"""Check the Gmail connection: python -m api.check_mail [--send]

Uses the sign-in saved by `python -m api.toolkit.gmail_auth`. Shows which account is signed in, puts a test draft addressed
to that same account in its Gmail drafts, and checks the draft is there. With --send it sends the test email to the same
account instead and reads the thread back. Only the account's own address is used; no client data.
"""
from __future__ import annotations

from datetime import datetime
import sys

from api.settings import ROOT, _load_dotenv
from api.toolkit.mail import CREDENTIALS, GmailMail, MailError


def main(argv: list[str]) -> int:
    _load_dotenv(ROOT / ".env")
    box = GmailMail.from_env()
    if not box.ready():
        print(f"Gmail is not signed in yet. Put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in {CREDENTIALS}, "
              "then run: python -m api.toolkit.gmail_auth")
        return 1
    subject = f"CGS workspace test {datetime.now():%Y-%m-%d %H:%M}"
    body = "This is a test from the CGS lead workspace (api/check_mail.py). You can delete it."
    try:
        me = box.account()
        print(f"Signed in as {me}.")
        if "--send" in argv:
            res = box.send(me, subject, body)
            msgs = box.thread(res["thread_id"])
            print(f"Sent the test email to yourself ({subject}). The thread reads back with {len(msgs)} message(s); "
                  f"marked as sent by you: {all(x['ours'] for x in msgs)}.")
        else:
            res = box.create_draft(me, subject, body)
            print(f"Put a test draft in your Gmail drafts: \"{subject}\". It is there: {box.draft_exists(res['draft_id'])}. "
                  "Nothing was sent; delete the draft in Gmail when you have seen it.")
    except MailError as e:
        print(f"Problem: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
