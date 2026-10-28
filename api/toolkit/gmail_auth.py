"""Sign the workspace in to Gmail once: python -m api.toolkit.gmail_auth

Before you run it:
  1. In Google Cloud Console, enable the Gmail API for a project.
  2. Create an OAuth client of type "Desktop app".
  3. Put its client id and secret in ~/.cgs-workspace/gmail.env (C:\\Users\\<you>\\.cgs-workspace\\gmail.env) as
     GOOGLE_CLIENT_ID=... and GOOGLE_CLIENT_SECRET=...  Not in the project .env: the project folder is synced and shared.

The script opens Google's sign-in page in your browser. You sign in and allow the workspace to create drafts, send
email and read email. Google sends the answer back to this computer only (127.0.0.1). The script saves the refresh
token in that same file as GMAIL_REFRESH_TOKEN and never prints it. Then set MAIL_PROVIDER=gmail and APP_MODE=live in the
project .env, and restart.

To sign out: delete GMAIL_REFRESH_TOKEN from the file and remove the app at https://myaccount.google.com/permissions.
"""
from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

import httpx

from api.settings import ROOT, _load_dotenv
from api.toolkit.mail import CREDENTIALS, SCOPES, TOKEN_URL, GmailMail, load_credentials

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def save_env_value(path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file: replace the existing line, or add one at the end."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _wait_for_code(state: str) -> tuple[str, int, HTTPServer]:
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlsplit(self.path).query)
            ok = q.get("state", [""])[0] == state and "code" in q
            if ok:
                result["code"] = q["code"][0]
            else:
                result["error"] = q.get("error", ["the answer did not match this sign-in"])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Signed in. You can close this tab." if ok else f"Sign-in failed: {result['error']}. You can close this tab."
            self.wfile.write(f"<p style='font-family:sans-serif'>{msg}</p>".encode())

        def log_message(self, *args):  # keep the code out of the console
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    return result, server.server_address[1], server


def main() -> int:
    _load_dotenv(ROOT / ".env")
    load_credentials()
    client_id, client_secret = os.environ.get("GOOGLE_CLIENT_ID", ""), os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print(f"Put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in {CREDENTIALS} first (an OAuth client of type Desktop app).")
        return 1
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    result, port, server = _wait_for_code(state)
    redirect = f"http://127.0.0.1:{port}/"
    url = AUTH_URL + "?" + urlencode({"client_id": client_id, "redirect_uri": redirect, "response_type": "code",
                                      "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
                                      "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    print("Opening Google sign-in in your browser. If it does not open, copy this link into the browser:\n" + url)
    webbrowser.open(url)
    server.timeout = 300
    server.handle_request()  # one answer from Google, or five minutes
    server.server_close()
    if "code" not in result:
        print(f"Sign-in did not finish: {result.get('error', 'no answer within five minutes')}.")
        return 1
    r = httpx.post(TOKEN_URL, data={"client_id": client_id, "client_secret": client_secret, "code": result["code"],
                                    "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"}, timeout=30)
    token = r.json().get("refresh_token") if r.status_code == 200 else None
    if not token:
        print(f"Google did not return a refresh token (status {r.status_code}). Try again; allow every permission asked for.")
        return 1
    save_env_value(CREDENTIALS, "GMAIL_REFRESH_TOKEN", token)
    try:
        who = GmailMail(client_id, client_secret, token).account()
    except Exception:
        who = "(could not read the address yet)"
    print(f"Saved the Gmail sign-in for {who} in {CREDENTIALS}. To use it in the workspace, set MAIL_PROVIDER=gmail and "
          "APP_MODE=live in the project .env, then restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
