"""Copy CGS's library documents from Google Drive into the workspace (api/toolkit/drive.py is the Drive client).

What happens in one sync:
1. List every file in the folders DRIVE_FOLDER_IDS names (and their subfolders, a few levels down).
2. Read the text of each Google Doc, .md and .txt file that is new or changed since the last sync (Drive's modifiedTime);
   other files are listed as skipped with the reason.
3. Save the text in drive_documents; delete the rows of documents no longer in the folders (only when every folder could
   be opened, so a sharing mistake never empties the library).
4. Hand the passages to the CGS library (api/knowledge.py set_extra), so agents search and quote them at once.

Who reads Drive: only this sync, under the name "Drive rules", metered in connector_uses like every connector. It runs
when the drive connector is on for every agent (config/toolkit.json "connectors", or the switch in the workspace) and
DRIVE_PROVIDER is set. Agents never browse Drive; they read the copied documents through the library connector.

Document kinds: a document whose name contains "solution" is read as CGS's solutions document (one solution per "## "
heading). A "Type: case" (or service) line in the document, or a folder named Cases / Services above it, sets its kind;
anything else is a "document".

SoW section 9: these are CGS's own documents. They are never sent for embeddings (api/knowledge.py embeddable) unless
KNOWLEDGE_EMBED_PRIVATE=allowed, and they reach an AI provider only as passages an agent searched, like any document
in the knowledge/ folder. Point DRIVE_FOLDER_IDS only at folders CGS has approved for that.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from sqlalchemy import select

from api import knowledge
from api import models as m
from api import toolkit as TK
from api.toolkit.drive import DriveError, folder_ids, skip_reason

WHO = "Drive rules"
LAST = "drive_last_sync"


def drive_client(c):
    f = getattr(c, "drive_factory", None)
    return f() if f else None


def status_off(c) -> str:
    """Why the sync cannot run now, or "" when it can."""
    if drive_client(c) is None:
        return "Google Drive is off (DRIVE_PROVIDER in .env)."
    if not TK.connector_detail("drive", getattr(c, "s", None))["on"]:
        return "The drive connector is off for every agent (config/toolkit.json, or switch it on in the workspace)."
    if not folder_ids():
        return "No Drive folders are named (DRIVE_FOLDER_IDS in .env)."
    return ""


def kind_of(name: str, path: str) -> str:
    if "solution" in name.lower():
        return "solutions"
    for part in reversed(path.lower().split("/")):
        for k in ("case", "service"):
            if k in part:
                return k
    return ""


def doc_id(row) -> str:
    """A passage id that stays the same when the document is renamed: the Drive file id."""
    return "drive-" + re.sub(r"[^A-Za-z0-9_-]", "", row.file_id)[:40]


def passages(rows) -> list:
    out = []
    for r in rows:
        kind = kind_of(r.name, r.path)
        if kind == "solutions":
            out += knowledge.solutions_text(r.text, synthetic=False, source=r.link)
        else:
            out += knowledge.document_text(r.text, doc_id(r), r.name, source=r.link, kind=kind, title=r.name)
    return out


def load_library(s) -> int:
    """Give the library the documents saved by the last sync (at start-up, and after each sync). Returns the passages."""
    found = passages(s.scalars(select(m.DriveDocument)).all())
    knowledge.set_extra("drive", found)
    return len(found)


def sync(c) -> dict:
    """One sync (see the module note). Returns what it did; never raises for a Drive problem (they are in errors)."""
    why = status_off(c)
    if why:
        return {"off": True, "why": why}
    drive = TK.metered(drive_client(c), WHO, "drive", c)
    out = {"read": 0, "unchanged": 0, "removed": 0, "skipped": [], "errors": [], "notes": [], "documents": 0}
    try:
        files, notes = drive.walk(folder_ids())
    except (DriveError, TK.LimitReached) as e:
        out["errors"].append(str(e))
        return finish(c, out)
    out["notes"] = notes
    have = {r.file_id: r for r in c.s.scalars(select(m.DriveDocument))}
    seen = set()
    for f in files:
        if not f.readable:
            out["skipped"].append({"name": f.name, "path": f.path, "why": skip_reason(f)})
            continue
        seen.add(f.id)
        row = have.get(f.id)
        if row is not None and row.modified == f.modified and f.modified:
            row.name, row.path, row.link = f.name[:300], f.path[:500], f.link[:500]
            out["unchanged"] += 1
            continue
        try:
            text = drive.text(f)
        except (DriveError, TK.LimitReached) as e:
            out["errors"].append(f"{f.path}/{f.name}: {e}")
            continue
        if row is None:
            row = m.DriveDocument(file_id=f.id)
            c.s.add(row)
        row.name, row.path, row.link, row.mime, row.modified = f.name[:300], f.path[:500], f.link[:500], f.mime[:120], f.modified
        row.text, row.synced_at = text, datetime.now(timezone.utc)
        out["read"] += 1
    folder_missing = any("was not found" in n for n in notes)
    cut_short = any(n.startswith("More than") for n in notes)
    if not folder_missing and not cut_short:
        for fid, row in have.items():
            if fid not in seen:
                c.s.delete(row)
                out["removed"] += 1
    elif any(fid not in seen for fid in have):
        out["notes"].append("Documents no longer listed were kept, because not every folder could be read in full.")
    return finish(c, out)


def finish(c, out: dict) -> dict:
    c.s.flush()
    out["documents"] = c.s.query(m.DriveDocument).count()
    out["passages"] = load_library(c.s)
    out["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["on"] = c.today.isoformat()  # the workspace's day (the demo has its own calendar)
    out["by"] = getattr(c, "actor", "") or WHO
    row = c.s.get(m.Setting, LAST)
    if row is None:
        c.s.add(m.Setting(key=LAST, value=out))
    else:
        row.value = out
    text = (f"Google Drive sync: {out['read']} document(s) read, {out['unchanged']} unchanged, {out['removed']} removed, "
            f"{len(out['skipped'])} skipped; the library holds {out['documents']} Drive document(s).")
    if out["errors"]:
        text += f" {len(out['errors'])} problem(s): {out['errors'][0]}"
    c.s.add(m.Activity(opportunity_id=None, on=c.today, actor_type="s", text=text[:2000]))
    return out


def status(c) -> dict:
    """GET /api/drive: whether Drive is set up, the folders, the last sync and the documents in the library."""
    drive = drive_client(c)
    last = c.s.get(m.Setting, LAST)
    rows = c.s.scalars(select(m.DriveDocument).order_by(m.DriveDocument.path, m.DriveDocument.name)).all()
    out = {"provider": getattr(drive, "name", "off"), "connectorOn": TK.connector_detail("drive", c.s)["on"],
           "folders": folder_ids(), "ready": bool(drive and drive.ready()), "off": status_off(c) or None,
           "lastSync": last.value if last else None,
           "documents": [{"name": r.name, "path": r.path, "link": r.link, "modified": r.modified, "kind": kind_of(r.name, r.path) or "document",
                          "chars": len(r.text or "")} for r in rows]}
    return out


def due(c, today) -> bool:
    """The patrol syncs once a day when Drive is set up."""
    if status_off(c):
        return False
    last = c.s.get(m.Setting, LAST)
    return not last or (last.value or {}).get("on") != today.isoformat()
