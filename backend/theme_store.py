"""P4 approved direction versions. Config files remain the immutable import baseline."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from .research_service import ResearchServiceError
from lead_agent.models import Brief, now


class ThemeStore:
    def __init__(self, root: Path, store):
        self.store = store
        baseline = Brief.model_validate_json((root / "config/problem_brief.json").read_text(encoding="utf-8"))
        self.theme_id = baseline.brief_id
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workspace_theme_versions(
                    version TEXT PRIMARY KEY, theme_id TEXT NOT NULL, scope TEXT NOT NULL,
                    consultant_id TEXT NOT NULL, parent_version TEXT, brief TEXT NOT NULL,
                    proposal_id TEXT UNIQUE, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspace_theme_heads(
                    theme_id TEXT NOT NULL, consultant_id TEXT NOT NULL, version TEXT NOT NULL,
                    PRIMARY KEY(theme_id,consultant_id));
            """)
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM workspace_theme_heads WHERE theme_id=? AND consultant_id=''", (self.theme_id,)).fetchone():
                db.execute("INSERT INTO workspace_theme_versions VALUES(?,?,?,?,?,?,?,?)",
                    (baseline.version, self.theme_id, "baseline", "", None, baseline.model_dump_json(), None, now()))
                db.execute("INSERT INTO workspace_theme_heads VALUES(?,?,?)", (self.theme_id, "", baseline.version))

    def current(self, consultant_id=None, *, db=None):
        if db is None:
            with self.store.db() as connection:
                return self.current(consultant_id, db=connection)
        row = db.execute("""SELECT v.brief FROM workspace_theme_heads h
            JOIN workspace_theme_versions v ON h.version=v.version
            WHERE h.theme_id=? AND h.consultant_id IN ('',?)
            ORDER BY CASE WHEN h.consultant_id='' THEN 1 ELSE 0 END LIMIT 1""",
            (self.theme_id, consultant_id or "")).fetchone()
        return Brief.model_validate_json(row[0])

    def assert_current(self, db, consultant_id, expected_version):
        if self.current(consultant_id, db=db).version != expected_version:
            raise ResearchServiceError("The approved direction changed. Refresh before starting research.")

    def versions(self):
        with self.store.db() as db:
            rows = db.execute("SELECT * FROM workspace_theme_versions ORDER BY rowid DESC").fetchall()
        return [{**dict(row), "brief": json.loads(row["brief"])} for row in rows]

    def apply(self, db, proposal, reviewer):
        target = proposal.consultant_id if proposal.scope == "consultant" else ""
        base = self.current(target, db=db)
        if base.version != proposal.base_theme_version:
            raise ResearchServiceError("This proposal is based on an older direction. Review current feedback and create a new proposal.")
        version = "p4-" + uuid4().hex[:16]
        values = {**base.model_dump(mode="json"), **proposal.patch.model_dump(exclude_none=True),
                  "version": version, "approved_by": reviewer, "approved_at": now()}
        # A local prototype approval never promotes mock settings to CGS approval.
        brief = Brief.model_validate(values)
        db.execute("INSERT INTO workspace_theme_versions VALUES(?,?,?,?,?,?,?,?)",
            (version, self.theme_id, proposal.scope, target, base.version,
             brief.model_dump_json(), proposal.proposal_id, now()))
        db.execute("INSERT INTO workspace_theme_heads VALUES(?,?,?) ON CONFLICT(theme_id,consultant_id) DO UPDATE SET version=excluded.version",
                   (self.theme_id, target, version))
        return version
