"""The Google Drive connector (api/toolkit/drive.py) and the library sync (api/drive_sync.py).
GoogleDrive is checked against a fake Drive API (httpx.MockTransport); the sync runs on the in-memory MockDrive."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api import autonomy as AU
from api import knowledge
from api import models as m
from api import toolkit as TK
from api.main import create_app
from api.settings import Settings
from api.toolkit.drive import GOOGLE_DOC, DriveError, DriveFile, GoogleDrive, MockDrive, folder_ids, make_drive

ME = {"X-User": "Alex Morgan"}
SOLUTIONS = """# CGS solutions

## Post-merger integration

After an acquisition, the combined company still runs separate systems and must decide what to integrate first.

## Plant start-up readiness

A new plant needs its processes, systems and roles in place before the first shift starts producing.
"""
CASE = """Ardent Foods case

A mid-sized food maker merged two plants and cut changeover time by a third within six months of the work.

Short line.
"""


@pytest.fixture(autouse=True)
def no_drive_passages_left_behind():
    yield
    knowledge.set_extra("drive", [])


# ---------- GoogleDrive over a fake Drive API ----------
class FakeDrive:
    def __init__(self):
        self.calls = []
        self.files = {
            "root": {"id": "root", "name": "CGS Library", "mimeType": "application/vnd.google-apps.folder", "parent": None},
            "sub": {"id": "sub", "name": "Cases", "mimeType": "application/vnd.google-apps.folder", "parent": "root"},
            "d1": {"id": "d1", "name": "Solutions", "mimeType": GOOGLE_DOC, "parent": "root", "text": SOLUTIONS},
            "d2": {"id": "d2", "name": "notes.md", "mimeType": "text/markdown", "parent": "sub", "text": CASE},
            "p1": {"id": "p1", "name": "deck.pdf", "mimeType": "application/pdf", "parent": "sub"},
        }

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path, dict(req.url.params)))
        if req.url.host == "oauth2.googleapis.com":
            assert b"grant_type=refresh_token" in req.content
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert req.headers["Authorization"] == "Bearer tok"
        path, q = req.url.path.removeprefix("/drive/v3"), req.url.params
        if path == "/about":
            return httpx.Response(200, json={"user": {"emailAddress": "library@example.com"}})
        if path == "/files":
            parent = q["q"].split("'")[1]
            kids = [{k: v for k, v in f.items() if k in ("id", "name", "mimeType")} | {"modifiedTime": "2026-11-01T00:00:00Z",
                     "webViewLink": f"https://docs.google.com/x/{f['id']}"} for f in self.files.values() if f["parent"] == parent]
            if "pageToken" not in q and len(kids) > 1:  # two pages, to check paging
                return httpx.Response(200, json={"files": kids[:1], "nextPageToken": "next"})
            return httpx.Response(200, json={"files": kids[1:] if "pageToken" in q else kids})
        fid = path.split("/")[2]
        f = self.files.get(fid)
        if f is None:
            return httpx.Response(404, json={"error": {"message": "File not found"}})
        if path.endswith("/export"):
            assert q["mimeType"] == "text/markdown"
            return httpx.Response(200, content=f["text"].encode())
        if q.get("alt") == "media":
            return httpx.Response(200, content=f["text"].encode())
        return httpx.Response(200, json={"id": fid, "name": f["name"], "mimeType": f["mimeType"]})


def google(fake):
    return GoogleDrive("cid", "secret", "refresh", http=httpx.Client(transport=httpx.MockTransport(fake)))


def test_google_drive_lists_folders_and_reads_documents():
    fake = FakeDrive()
    d = google(fake)
    assert d.ready() and d.account() == "library@example.com"
    files, notes = d.walk(["root", "missing"])
    assert any("missing" in n for n in notes)
    by = {f.name: f for f in files}
    assert set(by) == {"Solutions", "notes.md", "deck.pdf"}
    assert by["notes.md"].path == "CGS Library/Cases" and by["Solutions"].path == "CGS Library"
    assert by["Solutions"].readable and by["notes.md"].readable and not by["deck.pdf"].readable
    assert d.text(by["Solutions"]).startswith("# CGS solutions")  # exported as Markdown
    assert "Ardent" in d.text(by["notes.md"])                       # downloaded as it is
    assert sum(1 for c in fake.calls if c[1].endswith("/token")) == 1  # the access token is reused


def test_google_drive_explains_refusals():
    def refuse(req):
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(403, json={"error": {"message": "Google Drive API has not been used in project 123 before or it is disabled."}})
    with pytest.raises(DriveError, match="not enabled"):
        google(refuse).account()
    with pytest.raises(DriveError, match="not signed in"):
        GoogleDrive().account()
    expired = GoogleDrive("cid", "secret", "old", http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400))))
    with pytest.raises(DriveError, match="gmail_auth --drive"):
        expired.account()


def test_folder_ids_take_ids_or_links(monkeypatch):
    assert folder_ids("abc, https://drive.google.com/drive/folders/XyZ_123?usp=sharing ,") == ["abc", "XyZ_123"]
    monkeypatch.setenv("DRIVE_FOLDER_IDS", "one")
    assert folder_ids() == ["one"]
    assert make_drive("") is None and make_drive("off") is None and make_drive("mock").name == "mock"
    with pytest.raises(ValueError, match="DRIVE_PROVIDER"):
        Settings(drive_provider="dropbox")


# ---------- the sync into the CGS library ----------
def library_drive():
    d = MockDrive()
    root = d.add_folder("CGS Library", fid="lib")
    cases = d.add_folder("Cases", root)
    ids = {"solutions": d.add(root, "CGS Solutions", SOLUTIONS), "case": d.add(cases, "Ardent Foods", CASE),
           "deck": d.add(cases, "Pitch deck.pdf", mime="application/pdf"),
           "about": d.add(root, "About CGS.txt", "CGS Advisors helps manufacturers turn strategy into results on the plant floor.", mime="text/plain")}
    return d, ids


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIVE_FOLDER_IDS", "lib")
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "ws.sqlite3", drive_provider="mock"))
    app.state.drive, ids = library_drive()
    api = TestClient(app)
    return app, api, ids


def drive_on(api):
    r = api.put("/api/toolkit/permissions/*/drive", headers=ME, json={"enabled": True, "reason": "CGS library lives in Drive"})
    assert r.status_code == 200, r.text


def test_sync_waits_for_the_connector_switch(ws):
    app, api, _ = ws
    r = api.post("/api/drive/sync", headers=ME)
    assert r.status_code == 409 and r.json()["code"] == "drive_off" and "connector is off" in r.json()["error"]
    assert api.get("/api/drive", headers=ME).json()["connectorOn"] is False


def test_sync_copies_documents_into_the_library(ws):
    app, api, ids = ws
    drive_on(api)
    res = api.post("/api/drive/sync", headers=ME).json()
    assert (res["read"], res["unchanged"], res["removed"], res["documents"]) == (3, 0, 0, 3)
    assert [x["name"] for x in res["skipped"]] == ["Pitch deck.pdf"] and "Google Doc" in res["skipped"][0]["why"]
    lib = knowledge.library()
    sol = [p for p in lib.passages if p.type == "solution" and not p.synthetic]
    assert {p.title for p in sol} == {"Solution: Post-merger integration", "Solution: Plant start-up readiness"}
    case = [p for p in lib.passages if p.doc_id == f"drive-{ids['case']}"]
    assert len(case) == 1 and case[0].type == "case" and case[0].title == "Ardent Foods"  # the folder "Cases" sets the kind
    assert case[0].source.startswith("https://docs.google.com/")
    assert not any(p.synthetic for p in lib.passages)  # real documents now: the synthetic demo library steps aside
    assert not knowledge.embeddable(case[0])           # CGS's own documents never go out for embeddings
    hit = lib.search("changeover time merged plants", k=1)[0]
    assert hit["id"] == case[0].id
    st = api.get("/api/drive", headers=ME).json()
    assert st["provider"] == "mock" and st["account"] == "library@example.com" and len(st["documents"]) == 3
    assert st["lastSync"]["read"] == 3
    with app.state.db.session() as s:  # every Drive call is metered under "Drive rules"
        uses = s.scalars(select(m.ConnectorUse).where(m.ConnectorUse.connector == "drive")).all()
        assert {u.agent for u in uses} == {"Drive rules"} and {u.action for u in uses} == {"walk", "text"}


def test_sync_reads_only_what_changed_and_follows_removals(ws):
    app, api, ids = ws
    drive_on(api)
    api.post("/api/drive/sync", headers=ME)
    d = app.state.drive
    d.edit(ids["case"], CASE.replace("a third", "half"))
    d.remove(ids["about"])
    res = api.post("/api/drive/sync", headers=ME).json()
    assert (res["read"], res["unchanged"], res["removed"], res["documents"]) == (1, 1, 1, 2)
    assert "half" in " ".join(p.text for p in knowledge.library().passages)
    # a folder that cannot be opened never empties the library
    d.remove("lib")
    res = api.post("/api/drive/sync", headers=ME).json()
    assert res["removed"] == 0 and res["documents"] == 2 and any("kept" in n for n in res["notes"])


def test_a_failed_read_keeps_the_old_copy(ws):
    app, api, ids = ws
    drive_on(api)
    api.post("/api/drive/sync", headers=ME)
    app.state.drive.edit(ids["solutions"], SOLUTIONS + "\n## Something new\n\nA paragraph long enough to be one passage of the library.\n")
    app.state.drive.fail_next = "Google Drive answered 500"
    res = api.post("/api/drive/sync", headers=ME).json()
    assert res["errors"] and res["documents"] == 3
    assert not any("Something new" in p.title for p in knowledge.library().passages)


def test_documents_return_after_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIVE_FOLDER_IDS", "lib")
    db = tmp_path / "ws.sqlite3"
    app = create_app(Settings(app_mode="demo", db_path=db, drive_provider="mock"))
    app.state.drive, _ = library_drive()
    api = TestClient(app)
    drive_on(api)
    api.post("/api/drive/sync", headers=ME)
    knowledge.set_extra("drive", [])
    create_app(Settings(app_mode="demo", db_path=db))  # Drive off now: the copies saved last time still load
    assert any(p.doc_id.startswith("drive-") for p in knowledge.library().passages)


def test_patrol_syncs_once_a_day(ws):
    app, api, _ = ws
    drive_on(api)
    with app.state.db.session() as s:
        c = AU.agent_ctx(app, s)
        jobs = [j for j in AU.patrol(c) if j.kind == "drive_sync"]
        s.commit()
        assert len(jobs) == 1 and jobs[0].agent == "Drive rules"
        jid = jobs[0].id
    assert AU.run_job(app, jid) == "done"
    with app.state.db.session() as s:
        assert s.get(m.AgentJob, jid).result.startswith("Copied 3 document(s)")
        assert not [j for j in AU.patrol(AU.agent_ctx(app, s)) if j.kind == "drive_sync"]  # already done today


def test_drive_is_not_an_agent_tool():
    assert all("drive" not in conns for conns in TK.CEILING.values())
    assert TK.CONNECTORS["drive"][2] is True  # built: switched on or off for everyone
    assert TK.state("Direction agent", "drive") == "forbidden"
    assert json.loads(TK.CONFIG.read_text(encoding="utf-8"))["connectors"]["drive"] is False  # off until CGS names a folder
