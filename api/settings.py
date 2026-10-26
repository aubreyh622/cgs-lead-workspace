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
    if not path.exists():
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

    def __post_init__(self):
        if self.app_mode not in APP_MODES:
            raise ValueError(f"APP_MODE must be one of {APP_MODES}, not {self.app_mode!r}.")
        if self.crm_mode not in CRM_MODES:
            raise ValueError(f"CRM_MODE must be one of {CRM_MODES}, not {self.crm_mode!r}.")


def load_settings(env_file: Path | None = ROOT / ".env") -> Settings:
    if env_file is not None:
        _load_dotenv(env_file)
    return Settings(
        app_mode=os.environ.get("APP_MODE", "demo").strip().lower(),
        crm_mode=os.environ.get("CRM_MODE", "disabled").strip().lower(),
        port=int(os.environ.get("CGS_V2_PORT", "8800")),
        db_path=Path(os.environ.get("CGS_V2_DB_PATH", str(ROOT / "runtime" / "workspace-v2.sqlite3"))),
    )
