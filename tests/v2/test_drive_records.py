"""The workspace's records in CGS's Google Drive (api/drive_records.py): one Google Doc per opportunity and the activity
log Sheet, saved when they change, after a person's change and once a day; GoogleDrive.save checked against a fake API."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api import autonomy as AU
from api import models as m
from api.main import create_app
from api.settings import Settings
from api.toolkit.drive import GOOGLE_DOC, GOOGLE_SHEET, DriveError, GoogleDrive, MockDrive

ME = {"X-User": "Alex Morgan"}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIVE_RECORDS", "on")
    monkeypatch.delenv("DRIVE_RECORDS_FOLDER_ID", raising=False)
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "ws.sqlite3", drive_provider="mock"))
    app.state.drive = MockDrive()
    api = TestClient(app)
    r = api.put("/api/toolkit/permissions/*/drive", headers=ME, json={"enabled": True, "reason": "CGS records live in Drive"})
    assert r.status_code == 200, r.text
    return app, api


def records(app) -> dict:
    with app.state.db.session() as s:
        return {r.key: (r.file_id, r.error) for r in s.scalars(select(m.DriveRecord))}


def queued(app, kind="drive_records") -> list:
    with app.state.db.session() as s:
        return [j.id for j in s.scalars(select(m.AgentJob).where(m.AgentJob.kind == kind, m.AgentJob.status == "queued"))]


def active_opp(app) -> str:
    with app.state.db.session() as s:
        return s.scalars(select(m.Opportunity.id).where(m.Opportunity.stage == "active").order_by(m.Opportunity.id)).first()


def doc_text(app, key) -> str:
    fid = records(app)[key][0]
    return app.state.drive.items[fid]["text"]


def test_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("DRIVE_RECORDS", raising=False)
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "ws.sqlite3", drive_provider="mock"))
    app.state.drive = MockDrive()
    api = TestClient(app)
    st = api.get("/api/drive/records", headers=ME).json()
    assert st["on"] is False and "DRIVE_RECORDS" in st["off"]
    r = api.post("/api/drive/records/save", headers=ME)
    assert r.status_code == 409 and r.json()["code"] == "drive_records_off"
    api.put("/api/toolkit/permissions/*/drive", headers=ME, json={"enabled": True, "reason": "x"})
    assert queued(app) == [] and app.state.drive.items == {}


def test_save_makes_a_folder_a_doc_per_opportunity_and_the_log(ws):
    app, api = ws
    out = api.post("/api/drive/records/save", headers=ME).json()
    d = app.state.drive
    folder = out["folder"]
    assert d.folder(folder) == "CGS Workspace Records"
    with app.state.db.session() as s:
        opps = s.scalars(select(m.Opportunity).where(m.Opportunity.stage != "researching")).all()
        names = {f"{o.company.name} - {o.title} [{o.id}]"[:290] for o in opps}
    files = {f.name: f for f in d.children(folder)}
    assert names | {"Workspace activity log"} == set(files)
    assert out["saved"] == len(names) + 1 and out["errors"] == []
    assert files["Workspace activity log"].mime == GOOGLE_SHEET and all(f.mime == GOOGLE_DOC for n, f in files.items() if n in names)
    assert d.items[files["Workspace activity log"].id]["text"].startswith("Day,Time (UTC),Company,Opportunity,By,What happened")
    again = api.post("/api/drive/records/save", headers=ME).json()
    assert again["saved"] == 0 and again["unchanged"] == len(names) + 1  # nothing changed, nothing written
    assert len(d.children(folder)) == len(names) + 1  # files are replaced, never duplicated
    st = api.get("/api/drive/records", headers=ME).json()
    assert st["on"] and st["folder"]["id"] == folder and all(r["link"].startswith("https://docs.google.com/") for r in st["records"])
    with app.state.db.session() as s:
        uses = s.scalars(select(m.ConnectorUse).where(m.ConnectorUse.agent == "Drive records")).all()
        assert uses and {u.connector for u in uses} == {"drive"} and {u.action for u in uses} >= {"save"}


def test_a_persons_change_queues_a_save_and_only_that_record_changes(ws):
    app, api = ws
    api.post("/api/drive/records/save", headers=ME)
    api.post("/api/drive/records/save", headers=ME)
    oid = active_opp(app)
    before = {k: v for k, v in records(app).items()}
    note = "Client said budget is <b>approved</b> & they want a call before 2026-12-01."
    r = api.post(f"/api/opportunities/{oid}/notes", headers=ME, json={"text": note, "on": "2026-11-04"})
    assert r.status_code == 200, r.text
    jobs = queued(app)
    assert len(jobs) == 1  # one queued save, however many changes come before it runs
    api.post(f"/api/opportunities/{oid}/notes", headers=ME, json={"text": "Second note, same day.", "on": "2026-11-04"})
    assert queued(app) == jobs
    assert AU.run_job(app, jobs[0]) == "done"
    text = doc_text(app, f"opp:{oid}")
    assert "Client said budget is &lt;b&gt;approved&lt;/b&gt; &amp; they want a call" in text and "<b>approved</b>" not in text
    assert "Second note, same day." in text
    after = records(app)
    assert after[f"opp:{oid}"][0] == before[f"opp:{oid}"][0]  # same Doc, new content


def test_a_failed_save_is_kept_and_tried_again_and_a_deleted_doc_is_made_again(ws):
    app, api = ws
    api.post("/api/drive/records/save", headers=ME)
    oid = active_opp(app)
    d = app.state.drive
    api.post(f"/api/opportunities/{oid}/notes", headers=ME, json={"text": "A note that changes the record.", "on": "2026-11-04"})
    d.fail_next = "Google Drive answered 500"
    out = api.post("/api/drive/records/save", headers=ME).json()
    assert any("500" in e for e in out["errors"]) and sum(1 for _, err in records(app).values() if err) == 1
    out = api.post("/api/drive/records/save", headers=ME).json()
    assert out["errors"] == [] and not any(err for _, err in records(app).values())
    old = records(app)[f"opp:{oid}"][0]
    d.remove(old)  # someone deleted the Doc in Drive
    api.post(f"/api/opportunities/{oid}/notes", headers=ME, json={"text": "Another note after the Doc was deleted.", "on": "2026-11-05"})
    api.post("/api/drive/records/save", headers=ME)
    new = records(app)[f"opp:{oid}"][0]
    assert new != old and "Another note after the Doc was deleted." in d.items[new]["text"]


def test_named_folder_and_daily_patrol(ws, monkeypatch):
    app, api = ws
    d = app.state.drive
    named = d.add_folder("CGS Shared Records")
    monkeypatch.setenv("DRIVE_RECORDS_FOLDER_ID", f"https://drive.google.com/drive/folders/{named}?usp=sharing")
    assert len(queued(app)) == 1  # switching the connector on was a person's change: a save is already waiting
    AU.run_patrol(app)
    assert len(queued(app)) == 1  # the patrol's daily save joins it, never a second one
    job = queued(app)[0]
    assert AU.run_job(app, job) == "done"
    assert {f.name for f in d.children(named)} >= {"Workspace activity log"} and d.find_folder("CGS Workspace Records") is None
    assert not any(j["kind"] == "drive_records" for j in AU.run_patrol(app))  # once a day
    monkeypatch.setenv("DRIVE_RECORDS_FOLDER_ID", "missing-folder")
    api.post("/api/drive/records/save", headers=ME)
    assert {f.name for f in d.children(named)} >= {"Workspace activity log"}  # a saved file stays where it is (same Drive id)
    with app.state.db.session() as s:
        s.query(m.DriveRecord).delete()
        s.commit()
    out = api.post("/api/drive/records/save", headers=ME).json()
    assert out["saved"] == 0 and out["errors"] and all("missing-folder was not found" in e for e in out["errors"])


def test_records_wait_for_the_drive_connector(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIVE_RECORDS", "on")
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "ws.sqlite3", drive_provider="mock"))
    app.state.drive = MockDrive()
    api = TestClient(app)
    r = api.post("/api/drive/records/save", headers=ME)
    assert r.status_code == 409 and "connector" in r.json()["error"]


# ---------- GoogleDrive.save over a fake Drive API ----------
class FakeUpload:
    def __init__(self, scope_ok=True):
        self.calls, self.scope_ok, self.files = [], scope_ok, {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        self.calls.append((req.method, req.url.path, dict(req.url.params), req.content))
        if not self.scope_ok:
            return httpx.Response(403, json={"error": {"message": "Request had insufficient authentication scopes."}})
        if req.url.path == "/drive/v3/files" and req.method == "POST":
            meta = json.loads(req.content)
            fid = f"f{len(self.files) + 1}"
            self.files[fid] = meta
            return httpx.Response(200, json={"id": fid})
        if req.url.path == "/upload/drive/v3/files" and req.method == "POST":
            fid = f"f{len(self.files) + 1}"
            self.files[fid] = {}
            return httpx.Response(200, json={"id": fid, "webViewLink": f"https://docs.google.com/document/d/{fid}/edit"})
        if req.url.path.startswith("/upload/drive/v3/files/") and req.method == "PATCH":
            fid = req.url.path.rsplit("/", 1)[1]
            if fid not in self.files:
                return httpx.Response(404, json={"error": {"message": "File not found"}})
            return httpx.Response(200, json={"id": fid, "webViewLink": f"https://docs.google.com/document/d/{fid}/edit"})
        return httpx.Response(404)


def gd(fake) -> GoogleDrive:
    return GoogleDrive("cid", "secret", "refresh", http=httpx.Client(transport=httpx.MockTransport(fake)))


def parts(content: bytes) -> tuple[dict, str, str]:
    text = content.decode("utf-8")
    chunks = [c for c in text.split("--cgs-record-boundary") if c.strip() not in ("", "--")]
    meta = json.loads(chunks[0].split("\r\n\r\n", 1)[1].strip())
    media_head, media = chunks[1].split("\r\n\r\n", 1)
    return meta, media_head, media.rstrip("\r\n")


def test_google_drive_save_uploads_and_converts():
    fake = FakeUpload()
    d = gd(fake)
    folder = d.make_folder("CGS Workspace Records")
    saved = d.save(folder, "Acme - record", "<h1>Acme</h1>", "doc")
    method, path, params, content = fake.calls[-1]
    meta, head, media = parts(content)
    assert (method, path) == ("POST", "/upload/drive/v3/files") and params["uploadType"] == "multipart" and params["supportsAllDrives"] == "true"
    assert meta == {"name": "Acme - record", "mimeType": GOOGLE_DOC, "parents": [folder]} and "text/html" in head and media == "<h1>Acme</h1>"
    d.save(folder, "Workspace activity log", "a,b\n1,2", "sheet")
    meta, head, _ = parts(fake.calls[-1][3])
    assert meta["mimeType"] == GOOGLE_SHEET and "text/csv" in head
    again = d.save(folder, "Acme - record (renamed)", "<h1>Acme 2</h1>", "doc", saved["id"])
    method, path, _, content = fake.calls[-1]
    assert (method, path) == ("PATCH", f"/upload/drive/v3/files/{saved['id']}") and again["id"] == saved["id"]
    assert parts(content)[0] == {"name": "Acme - record (renamed)"}  # the content and name change; the file stays put
    made = d.save(folder, "Gone", "<p>x</p>", "doc", "deleted-id")  # deleted in Drive: made again
    assert made["id"] != "deleted-id" and fake.calls[-1][0] == "POST"


def test_google_drive_save_without_write_scope_says_sign_in_again():
    with pytest.raises(DriveError, match="gmail_auth --drive"):
        gd(FakeUpload(scope_ok=False)).save("folder", "x", "<p>x</p>")
