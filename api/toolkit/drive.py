"""Drive connector: CGS's library documents kept in Google Drive folders.

The workspace never lets an agent browse Drive. A sync (api/drive_sync.py, run by a person or once a day by the patrol)
copies the text of the documents in the folders CGS names (DRIVE_FOLDER_IDS) into a local cache, and the CGS library
(api/knowledge.py) reads that cache like the knowledge/ folder. Agents then search and quote those documents through the
library connector, with its permissions and word-for-word quote checks.

Two clients with the same methods:
  GoogleDrive  the Drive API v3 (REST over httpx), read only (scope drive.readonly). Signed in one of two ways:
               - a person's Google account: `python -m api.toolkit.gmail_auth --drive` saves DRIVE_REFRESH_TOKEN next to
                 the Gmail sign-in in ~/.cgs-workspace/gmail.env (same OAuth client, its own token);
               - DRIVE_AUTH=metadata: the service account a Google Cloud Run service runs as (the token comes from the
                 metadata server; share the folders with that service account's email). No key file is needed.
  MockDrive    in-memory folders for tests and demos. Nothing leaves the computer.

Documents read: Google Docs (exported as Markdown, so headings stay headings), and .md / .txt files. Other files (PDF,
Word, Sheets, Slides) are listed as skipped, with the reason, so a person sees what the library is missing.
"""
from __future__ import annotations

from dataclasses import dataclass
import itertools
import json
import os
import time

import httpx

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
METADATA_TOKEN = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
# A person's sign-in: read every file they can open (the library folders), and create and change only the files the
# workspace itself made (drive.file: the records folder and its Docs and Sheets). A sign-in made before records existed
# holds drive.readonly only; reading still works, saving records asks to sign in again.
SCOPES = ("https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/drive.file")
# The Cloud Run service account sees only the folders shared with it; to save records into a folder CGS made (a shared
# drive), it needs the full drive scope, still limited by what is shared with it.
METADATA_SCOPES = {"read": ("https://www.googleapis.com/auth/drive.readonly",), "write": ("https://www.googleapis.com/auth/drive",)}
PROVIDERS = ("off", "mock", "google")
FOLDER = "application/vnd.google-apps.folder"
GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
# What a record is uploaded as, and what Drive turns it into (Drive converts on upload: no Docs or Sheets API scope needed)
RECORD_KINDS = {"doc": ("text/html", GOOGLE_DOC), "sheet": ("text/csv", GOOGLE_SHEET)}


def records_on() -> bool:
    """DRIVE_RECORDS=on: the workspace saves its records (drafts, notes, the activity log) to Drive (api/drive_records.py)."""
    return os.environ.get("DRIVE_RECORDS", "off").strip().lower() == "on"
TEXT_TYPES = ("text/plain", "text/markdown", "text/x-markdown")
TEXT_ENDINGS = (".md", ".markdown", ".txt")
MAX_DEPTH = 4          # subfolders below a named folder
MAX_FILES = 300        # files listed per sync, all folders together
MAX_CHARS = 400_000    # text kept per document


class DriveError(Exception):
    pass


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime: str
    modified: str          # RFC 3339 time from Drive; the sync re-reads a document only when it changes
    link: str = ""         # where a person opens it
    path: str = ""         # folder names from the named folder down, for example "Library/Cases"

    @property
    def readable(self) -> bool:
        return self.mime == GOOGLE_DOC or self.mime in TEXT_TYPES or self.name.lower().endswith(TEXT_ENDINGS)


def skip_reason(f: DriveFile) -> str:
    if f.mime.startswith("application/vnd.google-apps."):
        kind = f.mime.rsplit(".", 1)[-1]
        return f"a Google {kind.title()} file; only Google Docs are read"
    if f.mime == "application/pdf" or f.name.lower().endswith(".pdf"):
        return "a PDF; save it as a Google Doc (Open with > Google Docs) to include it"
    if f.name.lower().endswith((".docx", ".doc")):
        return "a Word file; save it as a Google Doc (File > Save as Google Docs) to include it"
    return f"a {f.mime or 'file'} file; only Google Docs, .md and .txt are read"


def folder_ids(text: str | None = None) -> list[str]:
    """DRIVE_FOLDER_IDS: comma-separated folder ids, or folder links (the id is taken from the link)."""
    raw = os.environ.get("DRIVE_FOLDER_IDS", "") if text is None else text
    out = []
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        if "/folders/" in part:
            part = part.split("/folders/", 1)[1].split("?", 1)[0].split("/", 1)[0]
        out.append(part)
    return out


class _Walker:
    """Lists every file below the named folders (breadth first), shared by both clients."""

    def walk(self, folders: list[str]) -> tuple[list[DriveFile], list[str]]:
        files, notes, seen = [], [], set()
        queue = []
        for fid in folders:
            meta = self.folder(fid)
            if meta is None:
                notes.append(f"Folder {fid} was not found, or this account cannot open it (share it with {self.account() or 'the account'}).")
                continue
            queue.append((fid, meta, 0))
        while queue:
            fid, path, depth = queue.pop(0)
            if fid in seen:
                continue
            seen.add(fid)
            for f in self.children(fid):
                if f.mime == FOLDER:
                    if depth < MAX_DEPTH:
                        queue.append((f.id, f"{path}/{f.name}", depth + 1))
                    else:
                        notes.append(f"{path}/{f.name}: deeper than {MAX_DEPTH} folders, not read.")
                    continue
                if len(files) >= MAX_FILES:
                    notes.append(f"More than {MAX_FILES} files: the rest were not read.")
                    return files, notes
                files.append(DriveFile(f.id, f.name, f.mime, f.modified, f.link, path))
        return files, notes


# ---------- Google Drive ----------
class GoogleDrive(_Walker):
    name = "google"

    def __init__(self, client_id: str = "", client_secret: str = "", refresh_token: str = "", auth: str = "oauth",
                 http: httpx.Client | None = None):
        self.client_id, self.client_secret, self.refresh_token, self.auth = client_id, client_secret, refresh_token, auth
        self.http = http or httpx.Client(timeout=60)
        self._token, self._expires, self._me = "", 0.0, ""

    @classmethod
    def from_env(cls, http: httpx.Client | None = None) -> "GoogleDrive":
        from api.toolkit.mail import load_credentials
        load_credentials()  # the same file as the Gmail sign-in: ~/.cgs-workspace/gmail.env
        auth = os.environ.get("DRIVE_AUTH", "oauth").strip().lower() or "oauth"
        return cls(os.environ.get("GOOGLE_CLIENT_ID", ""), os.environ.get("GOOGLE_CLIENT_SECRET", ""),
                   os.environ.get("DRIVE_REFRESH_TOKEN", ""), auth, http)

    def ready(self) -> bool:
        return self.auth == "metadata" or bool(self.client_id and self.client_secret and self.refresh_token)

    def _access(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        if self.auth == "metadata":
            try:
                scopes = METADATA_SCOPES["write" if records_on() else "read"]
                r = self.http.get(METADATA_TOKEN, params={"scopes": ",".join(scopes)}, headers={"Metadata-Flavor": "Google"})
            except httpx.HTTPError as e:
                raise DriveError(f"DRIVE_AUTH=metadata works only on Google Cloud (could not reach the metadata server: {type(e).__name__}).") from None
            if r.status_code != 200:
                raise DriveError(f"The Google Cloud metadata server gave no Drive token ({r.status_code}).")
        else:
            if not self.ready():
                from api.toolkit.mail import CREDENTIALS
                raise DriveError(f"Google Drive is not signed in. Put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in {CREDENTIALS}, "
                                 "run `python -m api.toolkit.gmail_auth --drive`, then restart the workspace.")
            try:
                r = self.http.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                                    "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
            except httpx.HTTPError as e:
                raise DriveError(f"Could not reach Google ({type(e).__name__}).") from None
            if r.status_code != 200:
                raise DriveError("The Drive sign-in was refused or has expired. Run `python -m api.toolkit.gmail_auth --drive` again.")
        data = r.json()
        self._token, self._expires = data["access_token"], time.time() + int(data.get("expires_in", 3600))
        return self._token

    def _get(self, path: str, params: dict | None = None, raw: bool = False):
        try:
            r = self.http.get(API + path, params=params, headers={"Authorization": f"Bearer {self._access()}"})
        except httpx.HTTPError as e:
            raise DriveError(f"Could not reach Google Drive ({type(e).__name__}).") from None
        if r.status_code == 404:
            return None
        if r.status_code in (401, 403):
            try:
                reason = (r.json().get("error") or {}).get("message", "")[:240]
            except ValueError:
                reason = ""
            if "has not been used in project" in reason or "is disabled" in reason:
                raise DriveError("The Google Drive API is not enabled in the Google Cloud project of this sign-in. "
                                 f"Enable it there and retry in a few minutes. Google says: {reason}")
            raise DriveError("Google Drive refused the request." + (f" Google says: {reason}" if reason else ""))
        if r.status_code >= 400:
            raise DriveError(f"Google Drive answered {r.status_code}: {r.text[:200]}")
        return r if raw else r.json()

    def account(self) -> str:
        if not self._me:
            about = self._get("/about", {"fields": "user(emailAddress)"}) or {}
            self._me = (about.get("user") or {}).get("emailAddress", "")
        return self._me

    def folder(self, folder_id: str) -> str | None:
        """The folder's name, or None when it does not exist or this account cannot open it."""
        meta = self._get(f"/files/{folder_id}", {"fields": "id,name,mimeType", "supportsAllDrives": "true"})
        if not meta or meta.get("mimeType") != FOLDER:
            return None
        return meta.get("name") or folder_id

    def children(self, folder_id: str) -> list[DriveFile]:
        out, token = [], None
        while True:
            params = {"q": f"'{folder_id}' in parents and trashed = false", "pageSize": "200",
                      "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink)",
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "orderBy": "folder,name"}
            if token:
                params["pageToken"] = token
            page = self._get("/files", params) or {}
            out += [DriveFile(f["id"], f.get("name", ""), f.get("mimeType", ""), f.get("modifiedTime", ""), f.get("webViewLink", ""))
                    for f in page.get("files", [])]
            token = page.get("nextPageToken")
            if not token:
                return out

    def text(self, f: DriveFile) -> str:
        if f.mime == GOOGLE_DOC:
            r = None
            for fmt in ("text/markdown", "text/plain"):  # Markdown keeps headings; plain text if the export is refused
                try:
                    r = self._get(f"/files/{f.id}/export", {"mimeType": fmt}, raw=True)
                    break
                except DriveError as e:
                    if fmt == "text/plain" or "400" not in str(e):
                        raise
        else:
            r = self._get(f"/files/{f.id}", {"alt": "media", "supportsAllDrives": "true"}, raw=True)
        if r is None:
            raise DriveError(f"{f.name} is no longer in Drive.")
        return r.content.decode("utf-8", "replace")[:MAX_CHARS]

    # ----- writing records (api/drive_records.py) -----
    def _send(self, method: str, url: str, params: dict, **kw):
        try:
            r = self.http.request(method, url, params={**params, "supportsAllDrives": "true"},
                                  headers={"Authorization": f"Bearer {self._access()}", **kw.pop("headers", {})}, **kw)
        except httpx.HTTPError as e:
            raise DriveError(f"Could not reach Google Drive ({type(e).__name__}).") from None
        if r.status_code == 404:
            return None
        if r.status_code in (401, 403):
            try:
                reason = (r.json().get("error") or {}).get("message", "")[:240]
            except ValueError:
                reason = ""
            if "insufficient" in reason.lower() and "scope" in reason.lower():
                raise DriveError("This Drive sign-in may only read. Run `python -m api.toolkit.gmail_auth --drive` again so the "
                                 "workspace may also save its records, then restart.")
            raise DriveError("Google Drive refused to save." + (f" Google says: {reason}" if reason else ""))
        if r.status_code >= 400:
            raise DriveError(f"Google Drive answered {r.status_code}: {r.text[:200]}")
        return r.json()

    def find_folder(self, name: str, parent: str = "") -> str | None:
        """A folder with this name (in parent, or anywhere this sign-in may see), or None."""
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        q = f"mimeType = '{FOLDER}' and name = '{safe}' and trashed = false"
        if parent:
            q += f" and '{parent}' in parents"
        page = self._get("/files", {"q": q, "fields": "files(id)", "pageSize": "1", "supportsAllDrives": "true",
                                    "includeItemsFromAllDrives": "true"}) or {}
        return next((f["id"] for f in page.get("files", [])), None)

    def make_folder(self, name: str, parent: str = "") -> str:
        meta = {"name": name, "mimeType": FOLDER, **({"parents": [parent]} if parent else {})}
        x = self._send("POST", API + "/files", {"fields": "id"}, json=meta)
        if not x:
            raise DriveError(f"The folder {parent} was not found, or this account may not add to it.")
        return x["id"]

    def save(self, folder_id: str, name: str, body: str, kind: str = "doc", file_id: str = "") -> dict:
        """Save a record as a Google Doc (body = HTML) or Sheet (body = CSV): replace file_id's content, or create the file
        in folder_id. Returns {"id", "link"}. A file_id that no longer exists (someone deleted it) is made again."""
        source, target = RECORD_KINDS[kind]
        boundary = "cgs-record-boundary"
        meta = {"name": name} if file_id else {"name": name, "mimeType": target, "parents": [folder_id]}
        payload = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
                   f"--{boundary}\r\nContent-Type: {source}; charset=UTF-8\r\n\r\n{body}\r\n--{boundary}--").encode("utf-8")
        headers = {"Content-Type": f"multipart/related; boundary={boundary}"}
        params = {"uploadType": "multipart", "fields": "id,webViewLink"}
        if file_id:
            x = self._send("PATCH", f"{UPLOAD}/files/{file_id}", params, content=payload, headers=headers)
            if x is None:
                return self.save(folder_id, name, body, kind)
        else:
            x = self._send("POST", UPLOAD + "/files", params, content=payload, headers=headers)
            if x is None:
                raise DriveError(f"The records folder {folder_id} was not found, or this account may not add to it.")
        return {"id": x["id"], "link": x.get("webViewLink", "")}


# ---------- an in-memory Drive ----------
class MockDrive(_Walker):
    """Folders and documents in memory: add(folder, name, text) or add_folder(parent, name). fail_next makes the next read fail."""
    name = "mock"

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.fail_next = ""
        self._ids = itertools.count(1)
        self._tick = itertools.count(1)

    def ready(self) -> bool:
        return True

    def account(self) -> str:
        return "library@example.com"

    def add_folder(self, name: str, parent: str = "", fid: str = "") -> str:
        fid = fid or f"fold{next(self._ids)}"
        self.items[fid] = {"name": name, "mime": FOLDER, "parent": parent, "text": "", "modified": ""}
        return fid

    def add(self, parent: str, name: str, text: str = "", mime: str = GOOGLE_DOC, fid: str = "") -> str:
        fid = fid or f"doc{next(self._ids)}"
        self.items[fid] = {"name": name, "mime": mime, "parent": parent, "text": text, "modified": self._stamp()}
        return fid

    def edit(self, fid: str, text: str) -> None:
        self.items[fid].update(text=text, modified=self._stamp())

    def remove(self, fid: str) -> None:
        self.items.pop(fid, None)

    def _stamp(self) -> str:
        return f"2026-11-01T00:00:{next(self._tick):02d}Z"

    def folder(self, folder_id: str) -> str | None:
        x = self.items.get(folder_id)
        return x["name"] if x and x["mime"] == FOLDER else None

    def children(self, folder_id: str) -> list[DriveFile]:
        return [DriveFile(i, x["name"], x["mime"], x["modified"], f"https://docs.google.com/document/d/{i}/edit")
                for i, x in sorted(self.items.items(), key=lambda kv: (kv[1]["mime"] != FOLDER, kv[1]["name"])) if x["parent"] == folder_id]

    def text(self, f: DriveFile) -> str:
        if self.fail_next:
            msg, self.fail_next = self.fail_next, ""
            raise DriveError(msg)
        if f.id not in self.items:
            raise DriveError(f"{f.name} is no longer in Drive.")
        return self.items[f.id]["text"]

    def find_folder(self, name: str, parent: str = "") -> str | None:
        return next((i for i, x in self.items.items() if x["mime"] == FOLDER and x["name"] == name and (not parent or x["parent"] == parent)), None)

    def make_folder(self, name: str, parent: str = "") -> str:
        if parent and self.folder(parent) is None:
            raise DriveError(f"The folder {parent} was not found, or this account may not add to it.")
        return self.add_folder(name, parent)

    def save(self, folder_id: str, name: str, body: str, kind: str = "doc", file_id: str = "") -> dict:
        if self.fail_next:
            msg, self.fail_next = self.fail_next, ""
            raise DriveError(msg)
        mime = RECORD_KINDS[kind][1]
        if file_id and file_id in self.items:
            self.items[file_id].update(name=name, text=body, modified=self._stamp())
        else:
            if self.folder(folder_id) is None:
                raise DriveError(f"The records folder {folder_id} was not found, or this account may not add to it.")
            file_id = self.add(folder_id, name, body, mime)
        return {"id": file_id, "link": f"https://docs.google.com/{'spreadsheets' if kind == 'sheet' else 'document'}/d/{file_id}/edit"}


def make_drive(provider: str, local: bool = False):
    """The Drive client for DRIVE_PROVIDER (off | mock | google), or None when it is off. Empty = off: the library then
    comes only from the knowledge/ folder. TOOLS_TRANSPORT=mcp: a client of the cgs-drive MCP server instead (local=True
    always builds the client here; the server itself does that)."""
    p = (provider or "off").strip().lower()
    if p == "off":
        return None
    if not local:
        from cgs_tools import client as MCP
        if MCP.wanted():
            return MCP.DriveClient(p)
    if p == "mock":
        return MockDrive()
    return GoogleDrive.from_env()
