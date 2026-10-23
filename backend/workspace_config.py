"""Configuration for the local integration; provider secrets never enter reports."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


@dataclass(frozen=True)
class WorkspaceConfig:
    root: Path
    research_db: Path
    qualification_db: Path
    port: int
    providers: dict

    @classmethod
    def load(cls, root: Path, env: Mapping[str, str] | None = None):
        root = root.resolve()
        if env is None:
            load_dotenv(root / ".env", override=False)
            env = os.environ

        def path(key, default):
            value = (env.get(key) or default).strip()
            result = Path(value).expanduser()
            return (root / result).resolve() if not result.is_absolute() else result.resolve()

        try:
            port = int(env.get("CGS_PORT") or "8766")
        except ValueError:
            raise ValueError("CGS_PORT must be an integer.") from None
        providers = {
            "research": {
                "configured": all((env.get(k) or "").strip() for k in
                                  ("GEMINI_API_KEY", "TAVILY_API_KEY", "GEMINI_MODEL")),
                "model": (env.get("GEMINI_MODEL") or "").strip(),
            },
            "qualification": {
                "configured": all((env.get(k) or "").strip() for k in
                                  ("OPENAI_API_KEY", "OPENAI_MODEL")),
                "model": (env.get("OPENAI_MODEL") or "").strip(),
            },
            "orchestrator": {"implemented": True,
                "configured": all((env.get(k) or "").strip() for k in ("GEMINI_API_KEY", "GEMINI_MODEL")),
                "model": (env.get("GEMINI_MODEL") or "").strip()},
        }
        config = cls(root, path("CGS_RESEARCH_DB_PATH", "runtime/research.sqlite3"),
                     path("CGS_DB_PATH", "runtime/qualification.sqlite3"), port, providers)
        config.validate()
        return config

    def validate(self):
        if not 1 <= self.port <= 65535:
            raise ValueError("CGS_PORT must be between 1 and 65535.")
        same = self.research_db.resolve() == self.qualification_db.resolve()
        if not same and self.research_db.exists() and self.qualification_db.exists():
            same = self.research_db.samefile(self.qualification_db)
        if same:
            raise ValueError("Research and qualification must use different database files.")
