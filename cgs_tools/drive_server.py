"""MCP server cgs-drive: Google Drive (or the in-memory mock), as DRIVE_PROVIDER names it.

Reads (the CGS library folders): drive_info, walk, folder, children, read_doc.
Writes (the workspace's records, api/drive_records.py): find_folder, make_folder, save_record. A record is uploaded as
HTML (a Google Doc) or CSV (a Google Sheet); Drive converts it. Records are written by the workspace's rules after a
person's work, never by an agent.

Sign-in: DRIVE_REFRESH_TOKEN (a person, `python -m api.toolkit.gmail_auth --drive`) or DRIVE_AUTH=metadata (the Cloud Run
service account; share the folders with it).
"""
from __future__ import annotations

from functools import lru_cache
import os
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from api.toolkit.drive import DriveError, DriveFile
from cgs_tools.common import main, run

mcp = FastMCP("cgs-drive", instructions="CGS's Google Drive: read the library folders; save the workspace's records as Docs and Sheets.")


class File(BaseModel):
    id: str
    name: str
    mime: str
    modified: str = Field("", description="Drive's modifiedTime (RFC 3339)")
    link: str = ""
    path: str = Field("", description="Folder names from the named folder down, for example Library/Cases")


class Files(BaseModel):
    files: list[File]
    notes: list[str] = Field(default_factory=list, description="Folders that could not be opened, limits reached")


class Info(BaseModel):
    provider: str
    ready: bool
    account: str = ""


class Name(BaseModel):
    name: str | None = None


class FolderId(BaseModel):
    id: str | None = None


class Text(BaseModel):
    text: str


class Saved(BaseModel):
    id: str
    link: str = ""


@lru_cache(maxsize=1)
def drive():
    from api.toolkit.drive import make_drive
    d = make_drive(os.environ.get("DRIVE_PROVIDER", "off"), local=True)
    if d is None:
        raise DriveError("Google Drive is off (DRIVE_PROVIDER).")
    return d


def _file(f: DriveFile) -> File:
    return File(id=f.id, name=f.name, mime=f.mime, modified=f.modified, link=f.link, path=f.path)


@mcp.tool()
async def drive_info() -> Info:
    """Which Drive answers and the account it is signed in as."""
    def go():
        d = drive()
        return Info(provider=d.name, ready=d.ready(), account=d.account() if d.ready() else "")
    return await run(go, (DriveError,))


@mcp.tool()
async def walk(folders: list[str]) -> Files:
    """Every file below these folders (a few levels of subfolders), with its folder path."""
    def go():
        files, notes = drive().walk(folders)
        return Files(files=[_file(f) for f in files], notes=notes)
    return await run(go, (DriveError,))


@mcp.tool()
async def folder(folder_id: str) -> Name:
    """The folder's name, or null when it does not exist or this account cannot open it."""
    return await run(lambda: Name(name=drive().folder(folder_id)), (DriveError,))


@mcp.tool()
async def children(folder_id: str) -> Files:
    """The files and folders directly in one folder."""
    return await run(lambda: Files(files=[_file(f) for f in drive().children(folder_id)]), (DriveError,))


@mcp.tool()
async def read_doc(file: File) -> Text:
    """The text of a Google Doc (as Markdown), .md or .txt file."""
    return await run(lambda: Text(text=drive().text(DriveFile(**file.model_dump()))), (DriveError,))


@mcp.tool()
async def find_folder(name: str, parent: str = "") -> FolderId:
    """A folder with this name (in parent), or null."""
    return await run(lambda: FolderId(id=drive().find_folder(name, parent)), (DriveError,))


@mcp.tool()
async def make_folder(name: str, parent: str = "") -> FolderId:
    """WRITE. A new folder (in parent, else at the top of the signed-in Drive)."""
    return await run(lambda: FolderId(id=drive().make_folder(name, parent)), (DriveError,))


@mcp.tool()
async def save_record(folder_id: str, name: str, body: str, kind: Literal["doc", "sheet"] = "doc", file_id: str = "") -> Saved:
    """WRITE. Save a record: kind doc = body is HTML, saved as a Google Doc; kind sheet = body is CSV, saved as a Google
    Sheet. With file_id the file's content is replaced (made again if it was deleted)."""
    return await run(lambda: Saved(**drive().save(folder_id, name, body, kind, file_id)), (DriveError,))


if __name__ == "__main__":
    main(mcp)
