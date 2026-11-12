"""Workspace v2 settings, read from environment variables or the local .env file.

APP_MODE   demo | live            demo never calls an AI provider; live never falls back to demo.
CRM_MODE   disabled | read_only | write_approved
CRM_PROVIDER  off | mock | insightly; empty = mock in demo mode, off in live mode (api/toolkit/crm.py)
DRIVE_PROVIDER  off | mock | google; empty = off. CGS library documents from Google Drive (api/toolkit/drive.py)
CGS_LOGIN         off | google: Google sign-in for a hosted workspace (api/login.py); needs CGS_IAP_AUDIENCE and
                  CGS_USERS entries with emails: Alex Morgan <alex@example.com>, ...
CGS_HOST / PORT / CGS_PUBLIC_HOSTS   hosting (Google Cloud Run): see docs/DEPLOY_GOOGLE_CLOUD.md
DATABASE_URL      PostgreSQL instead of the local SQLite file, for example postgresql://user:password@host/dbname
AI_MODEL_<AGENT>  a model for one agent instead of AI_MODEL, for example AI_MODEL_OUTREACH=claude-sonnet-5-5 (see AGENT_MODEL_ENV)
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_MODES = ("demo", "live")
CRM_MODES = ("disabled", "read_only", "write_approved")
# Each agent may use its own model (same AI company as AI_PROVIDER): a stronger one where judgement and writing matter,
# a cheaper one for searching and reading. Unset = AI_MODEL. Compare choices with Check Quality.cmd before changing them.
AGENT_MODEL_ENV = {"Discovery agent": "AI_MODEL_DISCOVERY", "Direction agent": "AI_MODEL_DIRECTION", "Research agent": "AI_MODEL_RESEARCH",
                   "Outreach agent": "AI_MODEL_OUTREACH", "Evidence agent": "AI_MODEL_EVIDENCE", "Manager agent": "AI_MODEL_MANAGER",
                   # not an agent of its own: the step that reads each outreach draft against the CGS email standard
                   "Outreach reviewer": "AI_MODEL_OUTREACH_REVIEW"}


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
    host: str = "127.0.0.1"  # the address the server listens on; 0.0.0.0 inside a container (CGS_HOST)
    login: str = "off"       # off = each person picks their name; google = Google sign-in through IAP (api/login.py)
    iap_audience: str = ""   # who Google's sign-in note must be addressed to (CGS_IAP_AUDIENCE), copied from the IAP page
    logins: tuple[tuple[str, str], ...] = ()  # (email, workspace name): who each Google account is
    public_hosts: tuple[str, ...] = ()  # host names the hosted workspace is reached by, e.g. cgs-workspace-xyz.a.run.app
    db_path: Path = ROOT / "runtime" / "workspace-v2.sqlite3"
    frontend: Path = ROOT / "frontend" / "index.html"
    database_url: str = ""  # empty = SQLite file at db_path; set DATABASE_URL for PostgreSQL later
    ai_provider: str = "gemini"  # gemini | openai | claude; switch any time, see api/llm.py
    ai_model: str = ""           # empty = the provider's default model
    agent_models: tuple[tuple[str, str], ...] = ()  # (agent, model) pairs that override ai_model for that agent
    users: tuple[str, ...] = ("Alex Morgan", "Sarah Patel", "Daniel Brooks", "Quinn Harper")  # no login yet; pick a name
    directors: tuple[str, ...] = ()  # who may act as Director of Operations at Step 8; empty = anyone (until CGS names them)
    admins: tuple[str, ...] = ()     # who may change the agents' connector permissions; empty = any workspace user
    agent_worker: bool = False   # True = agents patrol and work in the background (the server turns it on; tests leave it off)
    agent_lanes: int = 3         # background work in parallel: jobs for different opportunities at once (1 = one at a time)
    mail_provider: str = ""  # off | mock | gmail; empty = mock in demo mode, off in live mode (api/toolkit/mail.py)
    crm_provider: str = ""   # off | mock | insightly; empty = mock in demo mode, off in live mode (api/toolkit/crm.py)
    drive_provider: str = ""  # off | mock | google; empty = off (api/toolkit/drive.py, folders in DRIVE_FOLDER_IDS)
    research_engine: str = "agent"  # Steps 2-3: agent (api/research_agent.py) | pipeline (Aubrey's pipeline, or one request on the synthetic web)

    def __post_init__(self):
        if self.app_mode not in APP_MODES:
            raise ValueError(f"APP_MODE must be one of {APP_MODES}, not {self.app_mode!r}.")
        if self.crm_mode not in CRM_MODES:
            raise ValueError(f"CRM_MODE must be one of {CRM_MODES}, not {self.crm_mode!r}.")
        if not 1 <= self.agent_lanes <= 8:
            raise ValueError(f"AGENT_LANES must be 1 to 8, not {self.agent_lanes}.")
        if self.research_engine not in ("agent", "pipeline"):
            raise ValueError(f"RESEARCH_ENGINE must be agent or pipeline, not {self.research_engine!r}.")
        if self.mail_provider not in ("", "off", "mock", "gmail"):
            raise ValueError(f"MAIL_PROVIDER must be off, mock or gmail, not {self.mail_provider!r}.")
        if self.crm_provider not in ("", "off", "mock", "insightly"):
            raise ValueError(f"CRM_PROVIDER must be off, mock or insightly, not {self.crm_provider!r}.")
        if self.drive_provider not in ("", "off", "mock", "google"):
            raise ValueError(f"DRIVE_PROVIDER must be off, mock or google, not {self.drive_provider!r}.")
        if self.mail_provider == "gmail" and self.app_mode == "demo":
            raise ValueError("MAIL_PROVIDER=gmail needs APP_MODE=live: demo data must not reach a real mailbox.")
        if self.host not in ("127.0.0.1", "localhost", "::1") and not self.public_hosts:
            raise ValueError(f"CGS_HOST={self.host} opens the workspace to other computers, so CGS_PUBLIC_HOSTS must name the "
                             "address people will use (for example cgs-workspace-xyz.a.run.app). Put Google sign-in in "
                             "front of it and set CGS_LOGIN=google (docs/DEPLOY_GOOGLE_CLOUD.md).")
        if self.login not in ("off", "google"):
            raise ValueError(f"CGS_LOGIN must be off or google, not {self.login!r}.")
        if self.login == "google":
            if not self.iap_audience:
                raise ValueError("CGS_LOGIN=google needs CGS_IAP_AUDIENCE: without it the workspace cannot tell a sign-in meant "
                                 "for it from one meant for another Google service (docs/DEPLOY_GOOGLE_CLOUD.md).")
            if not self.logins:
                raise ValueError("CGS_LOGIN=google needs each person's Google email in CGS_USERS, for example "
                                 "CGS_USERS=Alex Morgan <alex@example.com>, Sarah Patel <sarah@example.com>.")
            if len({e for e, _ in self.logins}) != len(self.logins):
                raise ValueError("CGS_USERS gives the same email to two people.")
        if self.database_url.startswith(("postgres://", "postgresql://")):  # the plain form Google Cloud and most hosts give
            object.__setattr__(self, "database_url", "postgresql+psycopg://" + self.database_url.split("://", 1)[1])
        unknown = [a for a, _ in self.agent_models if a not in AGENT_MODEL_ENV]
        if unknown:
            raise ValueError(f"No agent called {unknown[0]!r} can have its own model. Use one of {sorted(AGENT_MODEL_ENV)}.")
        if not self.database_url:
            object.__setattr__(self, "database_url", f"sqlite:///{Path(self.db_path).as_posix()}")

    def model_for(self, agent: str | None) -> str:
        """The model this agent uses: its own (AI_MODEL_<AGENT>) or AI_MODEL; empty = the provider's default."""
        return dict(self.agent_models).get(agent or "") or self.ai_model


def people(text: str) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """CGS_USERS: names, each optionally with a Google email: "Alex Morgan <alex@example.com>, Sarah Patel".
    Returns the names and the (email, name) pairs."""
    names, logins = [], []
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        name, _, rest = part.partition("<")
        name, email = name.strip(), rest.rstrip("> ").strip().lower()
        if not name:
            raise ValueError(f"CGS_USERS: {part!r} has an email but no name. Write it as Name <email>.")
        names.append(name)
        if email:
            logins.append((email, name))
    return tuple(names), tuple(logins)


def load_settings(env_file: Path | None = ROOT / ".env") -> Settings:
    if env_file is not None:
        _load_dotenv(env_file)
    return Settings(
        app_mode=os.environ.get("APP_MODE", "demo").strip().lower(),
        crm_mode=os.environ.get("CRM_MODE", "disabled").strip().lower(),
        port=int(os.environ.get("PORT") or os.environ.get("CGS_V2_PORT", "8800")),  # PORT: set by Google Cloud Run
        host=os.environ.get("CGS_HOST", "").strip() or "127.0.0.1",
        public_hosts=tuple(h.strip().lower() for h in os.environ.get("CGS_PUBLIC_HOSTS", "").split(",") if h.strip()),
        db_path=ROOT / Path(os.environ.get("CGS_V2_DB_PATH", "runtime/workspace-v2.sqlite3")),
        database_url=os.environ.get("DATABASE_URL", ""),
        ai_provider=os.environ.get("AI_PROVIDER", "gemini").strip().lower(),
        ai_model=os.environ.get("AI_MODEL", "").strip(),
        agent_models=tuple((agent, os.environ[key].strip()) for agent, key in AGENT_MODEL_ENV.items() if os.environ.get(key, "").strip()),
        agent_worker=os.environ.get("AGENT_WORKER", "on").strip().lower() in ("on", "1", "true", "yes"),
        agent_lanes=int(os.environ.get("AGENT_LANES", "3") or 3),
        research_engine=os.environ.get("RESEARCH_ENGINE", "agent").strip().lower() or "agent",
        mail_provider=os.environ.get("MAIL_PROVIDER", "").strip().lower(),
        crm_provider=os.environ.get("CRM_PROVIDER", "").strip().lower(),
        drive_provider=os.environ.get("DRIVE_PROVIDER", "").strip().lower(),
        **({"users": people(os.environ["CGS_USERS"])[0], "logins": people(os.environ["CGS_USERS"])[1]} if os.environ.get("CGS_USERS") else {}),
        login=os.environ.get("CGS_LOGIN", "").strip().lower() or "off",
        iap_audience=os.environ.get("CGS_IAP_AUDIENCE", "").strip(),
        directors=tuple(u.strip() for u in os.environ.get("CGS_DIRECTORS", "").split(",") if u.strip()),
        admins=tuple(u.strip() for u in os.environ.get("CGS_ADMINS", "").split(",") if u.strip()),
    )
