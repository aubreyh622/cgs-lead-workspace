"""Check the Google Drive connection: python -m api.check_drive [folder id or link ...]

Reads only. Shows the signed-in account, lists the folders (DRIVE_FOLDER_IDS in .env, or the ones given here), reads
every Google Doc / .md / .txt in them and says how many library passages each would give, and which files would be
skipped and why. Nothing is saved: run a sync in the workspace (POST /api/drive/sync) to copy them into the library.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from api.settings import ROOT, _load_dotenv
from api.toolkit.drive import DriveError, GoogleDrive, folder_ids, skip_reason


def say(ok: bool, text: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {text}")
    return ok


def main(argv: list[str] | None = None) -> int:
    from api import drive_sync as DRV
    _load_dotenv(ROOT / ".env")
    argv = sys.argv[1:] if argv is None else argv
    folders = folder_ids(",".join(argv)) if argv else folder_ids()
    drive = GoogleDrive.from_env()
    if not drive.ready():
        print("Google Drive is not signed in: run `python -m api.toolkit.gmail_auth --drive` (Drive Sign In.cmd), "
              "or set DRIVE_AUTH=metadata on Google Cloud.")
        return 1
    try:
        who = drive.account()
    except DriveError as e:
        say(False, f"sign-in: {e}")
        return 1
    say(True, f"signed in to Google Drive as {who}")
    if not folders:
        print("No folders: set DRIVE_FOLDER_IDS in .env, or give a folder id or link: python -m api.check_drive <folder>")
        return 1
    try:
        files, notes = drive.walk(folders)
    except DriveError as e:
        say(False, f"listing the folders: {e}")
        return 1
    ok = True
    for n in notes:
        ok = say(False, n) and ok
    readable = [f for f in files if f.readable]
    say(bool(readable), f"{len(files)} file(s) in {len(folders)} folder(s); {len(readable)} can go into the library")
    total = 0
    for f in readable:
        try:
            text = drive.text(f)
        except DriveError as e:
            ok = say(False, f"{f.path}/{f.name}: {e}") and ok
            continue
        row = SimpleNamespace(file_id=f.id, name=f.name, path=f.path, link=f.link, text=text)
        n = len(DRV.passages([row]))
        total += n
        kind = DRV.kind_of(f.name, f.path) or "document"
        ok = say(n > 0, f"{f.path}/{f.name} ({kind}): {len(text)} characters, {n} passage(s)"
                 + ("" if n else " - no paragraph longer than 40 characters") ) and ok
    for f in files:
        if not f.readable:
            print(f"  [SKIP] {f.path}/{f.name}: {skip_reason(f)}")
    print(f"\n{total} passage(s) in all. " + ("Drive is ready for the library." if ok and total else "See the FAIL lines above."))
    return 0 if ok and total else 1


if __name__ == "__main__":
    sys.exit(main())
