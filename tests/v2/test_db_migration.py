"""An older database file opens with the current code: missing columns are added, required ones with their default."""
import sqlite3

from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings

ME = {"X-User": "Alex Morgan"}


def test_an_older_database_gets_the_required_scope_column(tmp_path):
    db = tmp_path / "old.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    con = sqlite3.connect(db)  # make it look like a file from before manager_decisions.scope existed
    con.execute("INSERT INTO manager_decisions (id, \"on\", mode, kind, reason, outcome, detail, scope) "
                "VALUES (1, '2026-10-26', 'on', 'followup_draft', 'r', 'sent', '', 'review')")
    con.commit()
    con.execute("ALTER TABLE manager_decisions DROP COLUMN scope")
    con.commit()
    con.close()

    app = create_app(Settings(app_mode="demo", db_path=db))
    api = TestClient(app)
    assert api.get("/api/manager/scorecard").status_code == 200  # was a 500: no such column
    old = api.get("/api/manager/decisions").json()
    assert [d["scope"] for d in old] == ["review"]  # the older row took the default
