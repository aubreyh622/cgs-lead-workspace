"""Hosting on Google Cloud Run: the listening address, the public host names, and the PostgreSQL address."""
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings, load_settings

HOST = "cgs-workspace-xyz.a.run.app"


def hosted(tmp_path):
    return TestClient(create_app(Settings(db_path=tmp_path / "h.sqlite3", host="0.0.0.0", public_hosts=(HOST,))))


def test_open_address_needs_a_public_host_name():
    with pytest.raises(ValueError, match="CGS_PUBLIC_HOSTS"):
        Settings(host="0.0.0.0")
    assert Settings(host="0.0.0.0", public_hosts=(HOST,)).host == "0.0.0.0"


def test_local_workspace_refuses_a_public_host_name(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "l.sqlite3")))
    assert api.get("/api/health", headers={"host": HOST}).status_code == 403
    assert api.get("/api/health").status_code == 200


def test_hosted_workspace_answers_its_own_name_only(tmp_path):
    api = hosted(tmp_path)
    assert api.get("/api/health", headers={"host": HOST}).status_code == 200
    r = api.get("/api/health", headers={"host": "other.example.com"})
    assert r.status_code == 403 and r.json()["code"] == "bad_host" and HOST in r.json()["error"]


def test_hosted_workspace_takes_writes_from_its_own_page_only(tmp_path):
    api = hosted(tmp_path)
    body = {"scores": {}, "conditions": {}}
    ok = api.post("/api/rules/calculate", json=body, headers={"host": HOST, "origin": f"https://{HOST}"})
    assert ok.status_code == 200
    for origin in ("https://other.example.com", f"http://{HOST}"):  # another site; the same name without https
        r = api.post("/api/rules/calculate", json=body, headers={"host": HOST, "origin": origin})
        assert r.status_code == 403 and r.json()["code"] == "bad_origin"


def test_cloud_run_port_and_settings_from_the_environment(monkeypatch):
    monkeypatch.setenv("PORT", "8080")
    monkeypatch.setenv("CGS_V2_PORT", "8800")
    monkeypatch.setenv("CGS_HOST", "0.0.0.0")
    monkeypatch.setenv("CGS_PUBLIC_HOSTS", f" {HOST.upper()} , second.example.com")
    s = load_settings(env_file=None)
    assert (s.port, s.host, s.public_hosts) == (8080, "0.0.0.0", (HOST, "second.example.com"))


def test_plain_postgres_address_uses_the_installed_driver():
    for plain in ("postgresql://u:p@/db?host=/cloudsql/p:r:i", "postgres://u:p@/db?host=/cloudsql/p:r:i"):
        assert Settings(database_url=plain).database_url == "postgresql+psycopg://u:p@/db?host=/cloudsql/p:r:i"
    assert Settings(database_url="postgresql+psycopg://u@h/db").database_url == "postgresql+psycopg://u@h/db"
