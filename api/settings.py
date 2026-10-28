"""Workspace v2 settings, read from environment variables or the local .env file.

APP_MODE   demo | live            demo never calls an AI provider; live never falls back to demo.
CRM_MODE   disabled | read_only | write_approved
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_MODES = ("demo", "live")
CRM_MODES = ("disabled", "read_only", "write_approved")


def _load_dotenv(path: Path) -> None:
    if not path.exists() or os.environ.get("PYTHON_DOTENV_DISABLED"):  # the test suite's offline guard sets this
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass(frozen=True)
class Settings:
    app_mode: str = "demo"
    crm_mode: str = "disabled"
    port: int = 8800
    db_path: Path = ROOT / "runtime" / "workspace-v2.sqlite3"
    frontend: Path = ROOT / "frontend" / "index.html"
    database_url: str = ""  # empty = SQLite file at db_path; set DATABASE_URL for PostgreSQL later
    ai_provider: str = "gemini"  # gemini | openai | claude; switch any time, see api/llm.py
    ai_model: str = ""           # empty = the provider's default model
    users: tuple[str, ...] = ("Alex Morgan", "Sarah Patel", "Daniel Brooks", "Quinn Harper")  # no login yet; pick a name
    directors: tuple[str, ...] = ()  # who may act as Director of Operations at Step 8; empty = anyone (until CGS names them)
    agent_worker: bool = False   # True = agents patrol and work in the background (the server turns it on; tests leave it off)

    def __post_init__(self):
        if self.app_mode not in APP_MODES:
            raise ValueError(f"APP_MODE must be one of {APP_MODES}, not {self.app_mode!r}.")
        if self.crm_mode not in CRM_MODES:
            raise ValueError(f"CRM_MODE must be one of {CRM_MODES}, not {self.crm_mode!r}.")
        if not self.database_url:
            object.__setattr__(self, "database_url", f"sqlite:///{Path(self.db_path).as_posix()}")


def load_settings(env_file: Path | None = ROOT / ".env") -> Settings:
    if env_file is not None:
        _load_dotenv(env_file)
    return Settings(
        app_mode=os.environ.get("APP_MODE", "demo").strip().lower(),
        crm_mode=os.environ.get("CRM_MODE", "disabled").strip().lower(),
        port=int(os.environ.get("CGS_V2_PORT", "8800")),
        db_path=ROOT / Path(os.environ.get("CGS_V2_DB_PATH", "runtime/workspace-v2.sqlite3")),
        database_url=os.environ.get("DATABASE_URL", ""),
        ai_provider=os.environ.get("AI_PROVIDER", "gemini").strip().lower(),
        ai_model=os.environ.get("AI_MODEL", "").strip(),
        agent_worker=os.environ.get("AGENT_WORKER", "on").strip().lower() in ("on", "1", "true", "yes"),
        **({"users": tuple(u.strip() for u in os.environ["CGS_USERS"].split(",") if u.strip())} if os.environ.get("CGS_USERS") else {}),
        directors=tuple(u.strip() for u in os.environ.get("CGS_DIRECTORS", "").split(",") if u.strip()),
    )
