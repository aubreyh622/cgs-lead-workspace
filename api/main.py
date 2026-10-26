"""CGS Lead Workspace v2 API (FastAPI).

Step 2: framework, settings, the rules engine over HTTP, the Step 1-8 frontend at /.
Step 3: one database (SQLite via SQLAlchemy), seeded with the synthetic demo records in demo mode.
Step 4: every human action in Steps 1-8 and the learning loop (api/actions.py, api/services.py).

Run:  python -m api.main         then open http://127.0.0.1:8800/  (API docs at /docs)
"""
from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, model_validator

from backend.rules import (
    ANCHORS, CONDITION_QUESTIONS, CONDITIONS, FLOORS, LABELS, QUESTIONS, REASONS,
    RULE_VERSION, WEIGHTS, calculate, delta, validate_score,
)
from api.db import Database
from api.models import DirectionVersion, Setting
from sqlalchemy import select
import json
from datetime import date
from api.actions import router as actions_router
from api.routes import router
from api.services import ActionError
from api.seed import load_demo
from api.settings import Settings, load_settings

VERSION = "0.4.0"
Dim = Literal["B", "A", "N", "T", "H", "E"]
Cond = Literal["problem", "fit", "external", "next", "commercial", "conflict"]
Blocker = Literal[tuple(REASONS)]  # type: ignore[valid-type]


class Revenue(BaseModel):
    """Estimated revenue to CGS from this opportunity in the first 12 months, in USD."""
    low: int = Field(gt=0, examples=[500000])
    high: int = Field(gt=0, examples=[700000])

    @model_validator(mode="after")
    def ordered(self):
        if self.high < self.low:
            raise ValueError("high must be at least low")
        return self


class Assessment(BaseModel):
    """What the rules engine needs. Unknown scores and conditions are null (pending), never zero."""
    scores: dict[Dim, float | None] = Field(default_factory=dict, examples=[{"B": 3, "A": 4, "N": 4, "T": 3, "H": 3, "E": 3}])
    conditions: dict[Cond, bool | None] = Field(default_factory=dict, examples=[{"problem": True, "fit": True, "external": True, "next": True, "commercial": True, "conflict": True}])
    blocker: Blocker | None = None
    revenue: Revenue | None = None

    @model_validator(mode="after")
    def valid_scores(self):
        for key, value in self.scores.items():
            validate_score(key, value)
        return self

    def as_case(self) -> dict:
        return {
            "scores": {k: self.scores.get(k) for k in WEIGHTS},
            "conditions": {k: self.conditions.get(k) for k in CONDITIONS},
            "blocker": self.blocker,
            "revenue": self.revenue.model_dump() if self.revenue else None,
        }


class Gap(BaseModel):
    key: str
    label: str
    question: str


class Result(BaseModel):
    ql: float = Field(description="Q lower bound: confirmed dimensions only")
    qu: float = Field(description="Q upper bound: pending dimensions at their maximum")
    coverage: int = Field(description="Weight of confirmed dimensions, 0-100")
    rl: int
    ru: int
    rt: float | None
    pl: float = Field(description="P = 0.6 Q + 0.4 R, lower bound")
    pu: float
    readiness: Literal["Ready", "More evidence needed", "Not ready"]
    priority: str
    gaps: list[Gap]
    ruleVersion: str


class ChangeRequest(BaseModel):
    """A proposed change and the state before it, to explain its effect (for example B 3 to 4: Q +5, P +3)."""
    before: Assessment
    after: Assessment


class Change(BaseModel):
    q: float
    p: float
    before: Result
    after: Result


def starting_direction() -> DirectionVersion:
    from api.settings import ROOT
    b = json.loads((ROOT / "config" / "problem_brief.json").read_text(encoding="utf-8"))
    return DirectionVersion(version="v1", scope="shared", problem=b["problem"], signals=b["signals"], buyer_roles=b["buyer_roles"],
                            exclusions=b["exclusions"], excluded_domains=b["excluded_domains"], approved_by=b["approved_by"],
                            approved_on=date.fromisoformat(b["approved_at"][:10]), basis=b["approval_basis"],
                            change=f"Imported from config/problem_brief.json ({b['version']})")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(
        title="CGS Lead Workspace API",
        version=VERSION,
        description="Backend for the CGS early-funnel workflow (Steps 1-8). "
                    "Synthetic data only. The rules engine, not an AI model, calculates every score.",
    )
    app.state.settings = settings
    app.state.db = Database(settings.database_url)
    if settings.app_mode == "demo":
        load_demo(app.state.db)  # only fills an empty database
    with app.state.db.session() as s:
        if not s.get(Setting, "owners"):
            s.add(Setting(key="owners", value=list(settings.users)))
        if not s.scalar(select(DirectionVersion.version).limit(1)):
            s.add(starting_direction())  # live mode: Aubrey's approved problem brief becomes v1
    app.include_router(router)
    app.include_router(actions_router)

    @app.exception_handler(ActionError)
    async def action_error(_: Request, exc: ActionError):
        return JSONResponse({"error": str(exc), "code": exc.code}, status_code=exc.status)

    @app.get("/api/health", tags=["system"], summary="Is the server running?")
    def health():
        return {"ok": True, "version": VERSION}

    @app.get("/api/config", tags=["system"], summary="Current modes")
    def config():
        return {"appMode": settings.app_mode, "crmMode": settings.crm_mode, "ruleVersion": RULE_VERSION,
                "aiConnected": False, "note": "Live AI providers are connected in a later step. "
                "In live mode, AI actions return an error rather than demo results."}

    @app.get("/api/rules", tags=["rules"], summary="The qualification rules (weights, anchors, minimums)")
    def rules():
        return {"version": RULE_VERSION, "weights": WEIGHTS, "labels": LABELS, "anchors": ANCHORS,
                "floors": FLOORS, "conditions": CONDITIONS, "reasons": REASONS,
                "questions": QUESTIONS, "conditionQuestions": CONDITION_QUESTIONS,
                "formula": {"Q": "sum(weight x score / 5)", "R": "revenue rating x 20; unknown keeps 0-100",
                            "P": "0.6 Q + 0.4 R", "bands": {"High": 75, "Medium": 55}}}

    @app.post("/api/rules/calculate", response_model=Result, tags=["rules"],
              summary="Calculate Q, R, P and readiness for one opportunity")
    def calc(body: Assessment):
        return calculate(body.as_case())

    @app.post("/api/rules/delta", response_model=Change, tags=["rules"],
              summary="Explain how a change moves Q and P")
    def change(body: ChangeRequest):
        return delta(calculate(body.before.as_case()), calculate(body.after.as_case()))

    @app.get("/", include_in_schema=False)
    def index():
        if not settings.frontend.exists():
            return HTMLResponse("<h1>Frontend not found</h1><p>Put the prototype at frontend/index.html.</p>", 404)
        return FileResponse(settings.frontend)

    return app


app = create_app()


def main():
    import uvicorn
    s = app.state.settings
    print(f"CGS Lead Workspace v2 ({s.app_mode} mode, CRM {s.crm_mode})")
    print(f"Open http://127.0.0.1:{s.port}/  API docs: http://127.0.0.1:{s.port}/docs  Ctrl+C stops.")
    uvicorn.run(app, host="127.0.0.1", port=s.port, log_level="warning")


if __name__ == "__main__":
    main()
