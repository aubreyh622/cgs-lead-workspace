"""Sign-in for a hosted workspace: Google Identity-Aware Proxy (IAP).

On one computer there is no sign-in: each person picks their name and the page sends it as X-User (CGS_LOGIN=off).
Hosted, that is not enough, because anyone who reaches the address could pick any name. With CGS_LOGIN=google the
workspace sits behind IAP, which makes each person sign in with their Google account and adds a signed note to every
request saying who they are. This module checks that note on every request:

  1. the note is there and Google signed it (keys from KEYS_URL, kept for an hour);
  2. it was written for this workspace (CGS_IAP_AUDIENCE) and has not expired;
  3. the email belongs to a workspace user: CGS_USERS=Alex Morgan <alex@example.com>, ...

The person's name then replaces whatever X-User the page sent, so the rest of the code works as before and nobody
can act under another name. Needs requirements-cloud.txt (google-auth, cryptography).
"""
from __future__ import annotations

import threading
import time

KEYS_URL = "https://www.gstatic.com/iap/verify/public_key"  # Google's public keys for IAP notes, by key id
ISSUER = "https://cloud.google.com/iap"
HEADER = "x-goog-iap-jwt-assertion"
KEEP_KEYS = 3600  # seconds


class LoginError(Exception):
    def __init__(self, message: str, status: int, code: str):
        super().__init__(message)
        self.status, self.code = status, code


def fetch_keys() -> dict:
    import httpx
    r = httpx.get(KEYS_URL, timeout=10)
    r.raise_for_status()
    return r.json()


class GoogleLogin:
    def __init__(self, audience: str, logins: dict[str, str], fetch=fetch_keys):
        self.audience, self.logins, self.fetch = audience, {e.lower(): n for e, n in logins.items()}, fetch
        self._keys: dict = {}
        self._at = 0.0
        self._lock = threading.Lock()

    def keys(self, fresh: bool = False) -> dict:
        with self._lock:
            if fresh or not self._keys or time.monotonic() - self._at > KEEP_KEYS:
                self._keys, self._at = self.fetch(), time.monotonic()
            return self._keys

    def claims(self, token: str) -> dict:
        from google.auth import exceptions, jwt
        try:
            kid = jwt.decode_header(token).get("kid")
        except (exceptions.GoogleAuthError, ValueError) as e:
            raise LoginError("The Google sign-in on this request cannot be read. Reload the page to sign in again.",
                             401, "bad_sign_in") from e
        keys = self.keys()
        if kid not in keys:  # Google changes its keys now and then: look once more before refusing
            keys = self.keys(fresh=True)
        try:
            return jwt.decode(token, certs=keys, audience=self.audience, clock_skew_in_seconds=30)
        except (exceptions.GoogleAuthError, ValueError) as e:
            raise LoginError(f"The Google sign-in on this request is not valid ({e}). Reload the page to sign in again.",
                             401, "bad_sign_in") from e

    def who(self, headers) -> tuple[str, str]:
        """(workspace name, email) of the signed-in person, or LoginError."""
        token = headers.get(HEADER)
        if not token:
            raise LoginError("This workspace needs Google sign-in, and this request arrived without one. "
                             "Open the workspace through its normal address.", 401, "sign_in_required")
        try:
            claims = self.claims(token)
        except LoginError:
            raise
        except Exception as e:  # the keys could not be fetched: refuse, never let the request through unchecked
            raise LoginError(f"Google sign-in could not be checked just now ({type(e).__name__}). Try again in a minute.",
                             503, "sign_in_unavailable") from e
        if claims.get("iss") != ISSUER:
            raise LoginError("The Google sign-in on this request is not from Identity-Aware Proxy.", 401, "bad_sign_in")
        email = str(claims.get("email") or "").lower()
        name = self.logins.get(email)
        if not name:
            raise LoginError(f"{email or 'This Google account'} is not a workspace user. Ask the workspace admin to add "
                             "this email to CGS_USERS.", 403, "not_a_user")
        return name, email
