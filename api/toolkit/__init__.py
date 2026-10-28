"""Toolkit: the one way agents reach outside systems, and who may reach what.

Connectors are the outside systems: the web (Tavily or the synthetic web), the CGS library, email (Gmail), and later
Google Drive and the CRM (Insightly). Every connector client an agent works with comes through the gate in this module
(web_for, library_for, mail_for, or grant for ready-made tools), so one permission table governs all agents.

Permissions, strongest first:
1. CEILING (in code, reviewed like code): what each agent may ever use. "on" = allowed and on by default; "allowed" = may
   be switched on; anything else is forbidden and nothing below can switch it on.
2. Switches people set in the workspace (PUT /api/toolkit/permissions/..., stored in connector_switches, every change kept
   in permission_changes). A switch for agent "*" turns a connector on or off for every agent.
3. config/toolkit.json: the defaults the team agreed (a connector off for everyone, or the exact list one agent has).
   Asking for a connector the ceiling forbids stops the workspace with an error instead of being ignored.
4. The ceiling's own default ("on").
Functions that decide take the database session (s); without one only layers 1, 3 and 4 apply.

Other rules:
- Agents only read. Sending an email, creating a Gmail draft or writing to the CRM is a connector method the workspace
  calls after a person clicks; it is never an agent tool.
- A tool that returns client data (an email thread) also needs CLIENT_DATA_TO_AI=allowed (SoW section 9).
- An agent's own checks (a quote must be word for word, a cited phrase must match its source) stay in the agent.
- tests/v2/test_toolkit_governance.py fails if an agent module reaches a connector without this gate.

Usage: every client the gate hands out is metered. Each call (a search, a page read, a library search, an email read or
send) is recorded in connector_uses with the agent, the connector, what was called and the outcome (ok, failed, denied,
limit). A refused permission is recorded too. LIMITS: a connector can have a daily limit (config/toolkit.json "limits",
or set in the workspace); once today's calls reach it, further calls are refused until tomorrow.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import date
import json
import os
from pathlib import Path
from typing import Any, Callable

from api.agent_runtime import Tool, ToolError, params
from api.agent_tools import Pages, web_tools

CONFIG = Path(__file__).resolve().parents[2] / "config" / "toolkit.json"
DATA_CLASSES = ("public", "cgs", "client")

# name: (data it returns, what it is, built yet)
CONNECTORS = {
    "web": ("public", "Web search and pages (Tavily, or the synthetic web)", True),
    "library": ("cgs", "The CGS library: solutions, services, cases and documents", True),
    "mail": ("client", "Email threads with clients (Gmail)", True),
    "drive": ("cgs", "Google Drive documents", False),
    "crm": ("client", "The CRM (Insightly): accounts, contacts, opportunities", False),
}

# What each agent may ever use. Changing this is a code change.
CEILING: dict[str, dict[str, str]] = {
    "Discovery agent": {"web": "on", "crm": "allowed"},
    "Direction agent": {"library": "on", "drive": "allowed"},
    "Research agent": {"web": "on", "crm": "allowed"},
    "Outreach agent": {"web": "on", "library": "on", "mail": "allowed"},
    "Evidence agent": {"library": "on", "mail": "allowed"},
    "Next-step agent": {"library": "on"},
    "Manager agent": {"library": "on"},  # its triage plans next actions, including the service-fit gap
    "Feedback agent": {},
    "Reply classifier": {},
}


class Denied(Exception):
    """An agent asked for a connector it may not use, or one that is off."""


class ConfigError(ValueError):
    """config/toolkit.json asks for something the code does not allow."""


class LimitReached(Denied, ToolError):
    """A connector's daily limit is used up. Inside an agent's tool the agent is told and may finish with what it has."""


NOT_METERED = {"ready", "account"}  # cheap questions about the client itself, not uses of the outside system


# ---------- the permission table ----------
def config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def client_data_allowed() -> bool:
    return os.environ.get("CLIENT_DATA_TO_AI", "off").strip().lower() == "allowed"


def check_config(cfg: dict | None = None) -> dict:
    """The config, after checking it stays inside the ceiling. Raises ConfigError otherwise."""
    cfg = cfg if cfg is not None else config()
    for name in cfg.get("connectors", {}):
        if name not in CONNECTORS:
            raise ConfigError(f"config/toolkit.json names an unknown connector: {name}")
    for name, n in cfg.get("limits", {}).items():
        if name.startswith("_"):
            continue
        if name not in CONNECTORS or not (n is None or (isinstance(n, int) and n > 0)):
            raise ConfigError(f"config/toolkit.json limits: {name} must be a known connector with a whole number above 0, or null")
    for agent, conns in cfg.get("agents", {}).items():
        if agent not in CEILING:
            raise ConfigError(f"config/toolkit.json names an unknown agent: {agent}")
        if not isinstance(conns, list):
            raise ConfigError(f"config/toolkit.json: {agent} must list its connectors, for example [\"web\"]")
        for name in conns:
            if name not in CONNECTORS:
                raise ConfigError(f"config/toolkit.json gives {agent} an unknown connector: {name}")
            if name not in CEILING[agent]:
                raise ConfigError(f"config/toolkit.json gives {agent} the {name} connector, which the code forbids for it "
                                  "(api/toolkit CEILING).")
    return cfg


def switched_on(agent: str, cfg: dict) -> set[str]:
    listed = cfg.get("agents", {}).get(agent)
    if listed is not None:
        return set(listed)
    return {name for name, state in CEILING.get(agent, {}).items() if state == "on"}


def switches(s) -> dict[tuple[str, str], Any]:
    """The switches people set, by (agent, connector)."""
    if s is None:
        return {}
    from api import models as m
    from sqlalchemy import select
    return {(x.agent, x.connector): x for x in s.scalars(select(m.ConnectorSwitch))}


def _source(row) -> dict:
    return {"source": "switch", "changedBy": row.changed_by, "changedOn": row.changed_on.isoformat(), "reason": row.reason}


def connector_detail(connector: str, s=None, cfg: dict | None = None, sw: dict | None = None) -> dict:
    """Whether a connector is on for every agent, and why."""
    cfg = check_config(cfg)
    sw = switches(s) if sw is None else sw
    if not CONNECTORS[connector][2]:
        return {"on": False, "source": "not built"}
    row = sw.get(("*", connector))
    if row is not None:
        return {"on": bool(row.enabled), **_source(row)}
    return {"on": bool(cfg.get("connectors", {}).get(connector, False)), "source": "config file"}


def detail(agent: str, connector: str, s=None, cfg: dict | None = None, sw: dict | None = None) -> dict:
    """state: on | off (may be switched on) | connector off | not built | forbidden; with where it comes from."""
    cfg = check_config(cfg)
    sw = switches(s) if sw is None else sw
    ceiling = CEILING.get(agent, {}).get(connector)
    if ceiling is None:
        return {"state": "forbidden", "editable": False, "source": "code"}
    if not CONNECTORS[connector][2]:
        return {"state": "not built", "editable": False, "source": "code"}
    if not connector_detail(connector, cfg=cfg, sw=sw)["on"]:
        return {"state": "connector off", "editable": True, "source": "connector"}
    row = sw.get((agent, connector))
    if row is not None:
        return {"state": "on" if row.enabled else "off", "editable": True, **_source(row)}
    listed = agent in cfg.get("agents", {})
    return {"state": "on" if connector in switched_on(agent, cfg) else "off", "editable": True,
            "source": "config file" if listed else "default"}


def state(agent: str, connector: str, s=None, cfg: dict | None = None) -> str:
    return detail(agent, connector, s, cfg)["state"]


def allowed(agent: str, connector: str, s=None) -> bool:
    return state(agent, connector, s) == "on"


def require(agent: str, connector: str, s=None) -> None:
    st = state(agent, connector, s)
    if st != "on":
        why = {"forbidden": "the code does not allow it", "connector off": "that connector is turned off",
               "not built": "that connector is not built yet", "off": "it is not switched on for this agent"}[st]
        raise Denied(f"{agent} may not use the {connector} connector: {why} (GET /api/toolkit/permissions).")


# ---------- daily limits and the usage log ----------
def limits(s=None, cfg: dict | None = None) -> dict[str, dict]:
    """Each connector's daily limit: {"perDay": n or None, "source": "config file" | "switch" | "none"}."""
    cfg = check_config(cfg)
    out = {k: {"perDay": cfg.get("limits", {}).get(k), "source": "config file" if cfg.get("limits", {}).get(k) else "none"} for k in CONNECTORS}
    if s is not None:
        from api import models as m
        row = s.get(m.Setting, "connector_limits")
        for k, n in ((row.value if row else None) or {}).items():
            if k in out:
                out[k] = {"perDay": n or None, "source": "switch"}  # 0 = no limit, set by a person
    return out


def used_today(s, connector_name: str, today: date) -> int:
    from api import models as m
    from sqlalchemy import func, select
    return s.scalar(select(func.count()).select_from(m.ConnectorUse).where(
        m.ConnectorUse.on == today, m.ConnectorUse.connector == connector_name, m.ConnectorUse.outcome.in_(("ok", "failed")))) or 0


def record(s, agent: str, connector_name: str, action: str, outcome: str, today: date) -> None:
    from api import models as m
    s.add(m.ConnectorUse(on=today, agent=agent, connector=connector_name, action=action[:40], outcome=outcome))


class Meter:
    """Counts one client's calls and applies the daily limit.
    With a session (s): records join that request's transaction (a limit hit is saved at once).
    With a buffer (work in its own thread, such as research): the limit is checked by reading the database through
    session_factory (reads never wait for a writer), and records collect in the buffer; whoever saves the work's result
    saves them too (Usage.save). This way the thread never waits on a request that holds the database's write lock."""

    def __init__(self, agent: str, connector_name: str, today: date | None = None, s=None, session_factory=None,
                 buffer: list | None = None):
        self.agent, self.connector, self.today = agent, connector_name, today or date.today()
        self.s, self.session_factory, self.buffer = s, session_factory, buffer

    def _count(self) -> tuple[int | None, int]:
        with (self.session_factory() if self.buffer is not None else nullcontext(self.s)) as s:
            limit = limits(s)[self.connector]["perDay"]
            used = used_today(s, self.connector, self.today) if limit is not None else 0
        if self.buffer is not None:
            used += sum(1 for u in self.buffer if u["connector"] == self.connector and u["outcome"] in ("ok", "failed"))
        return limit, used

    def _record(self, action: str, outcome: str) -> None:
        if self.buffer is not None:
            self.buffer.append({"agent": self.agent, "connector": self.connector, "action": action, "outcome": outcome, "today": self.today})
        else:
            record(self.s, self.agent, self.connector, action, outcome, self.today)

    def use(self, action: str, fn: Callable[[], Any]) -> Any:
        if self.s is None and self.buffer is None:
            return fn()  # no database (a unit test): nothing to count against
        limit, used = self._count()
        if limit is not None and used >= limit:
            self._record(action, "limit")
            if self.buffer is None:
                self.s.commit()  # keep the record even though the work stops
            raise LimitReached(f"The {self.connector} connector has used its daily limit of {limit} calls. "
                               "Finish with what you have; it opens again tomorrow.")
        outcome = "failed"
        try:
            out = fn()
            outcome = "ok"
            return out
        finally:
            self._record(action, outcome)


def save_uses(s, buffer: list) -> None:
    """Save the usage records a thread collected (see Meter)."""
    for u in buffer:
        record(s, u["agent"], u["connector"], u["action"], u["outcome"], u["today"])
    buffer.clear()


class Metered:
    """A connector client whose calls go through a Meter. Everything else (attributes, hasattr) behaves as the client."""

    def __init__(self, client: Any, meter: Meter):
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_meter", meter)

    def __getattr__(self, name: str):
        attr = getattr(object.__getattribute__(self, "_client"), name)
        if name.startswith("_") or name in NOT_METERED or not callable(attr):
            return attr
        meter = object.__getattribute__(self, "_meter")
        return lambda *a, **kw: meter.use(name, lambda: attr(*a, **kw))


def metered(client: Any, who: str, connector_name: str, c=None, session_factory=None) -> Any:
    """Meter a client without a permission check: for work people do themselves (an email a person sends)."""
    if client is None:
        return None
    return Metered(client, Meter(who, connector_name, getattr(c, "today", None), getattr(c, "s", None), session_factory))


# ---------- the gate: every connector client an agent gets comes from here ----------
def connector(agent: str, name: str, make: Callable[[], Any], s=None, today: date | None = None, session_factory=None,
              buffer: list | None = None) -> Any:
    """Check the permission, then build the client and meter it. make: how this server builds it (tests pass fakes).
    A refusal is recorded and saved even though the work stops. buffer + session_factory: for work in its own thread."""
    try:
        require(agent, name, s)
    except Denied:
        if buffer is not None:
            buffer.append({"agent": agent, "connector": name, "action": "permission", "outcome": "denied", "today": today or date.today()})
        elif s is not None:
            record(s, agent, name, "permission", "denied", today or date.today())
            s.commit()
        raise
    client = make()
    if client is None:
        return None
    if buffer is not None:
        return Metered(client, Meter(agent, name, today, session_factory=session_factory, buffer=buffer))
    return Metered(client, Meter(agent, name, today, s))


def web_for(agent: str, c=None, as_of=None, make: Callable[[], Any] | None = None):
    def build():
        if make:
            return make()
        if c is not None and getattr(c, "search_factory", None):
            return c.search_factory()
        from api.search import make_search
        return make_search(as_of=as_of or (c.today if c is not None else None))
    return connector(agent, "web", build, getattr(c, "s", None), getattr(c, "today", None))


def library_for(agent: str, c=None):
    from api import knowledge
    return connector(agent, "library", knowledge.library, getattr(c, "s", None), getattr(c, "today", None))


def mailbox(c):
    f = getattr(c, "mail_factory", None)
    return f() if f else None


def mail_for(agent: str, c):
    box = connector(agent, "mail", lambda: mailbox(c), getattr(c, "s", None), getattr(c, "today", None))
    if box is None:
        raise Denied("Email is off (MAIL_PROVIDER).")
    return box


# ---------- ready-made tools ----------
@dataclass
class Run:
    """What one agent run works on. Tools built for the run share it (for example the pages the agent read)."""
    c: Any                      # api.services.Ctx
    company: str = ""
    domain: str = ""
    opp: Any = None             # api.models.Opportunity, when the run is about one
    company_id: str = ""
    pages: Pages = field(default_factory=Pages)
    cache: dict = field(default_factory=dict)
    agent: str = ""             # set by grant


@dataclass(frozen=True)
class Entry:
    name: str
    connector: str
    about: str
    make: Callable[[Run], Tool]

    @property
    def data(self) -> str:
        return CONNECTORS[self.connector][0]


REGISTRY: dict[str, Entry] = {}


def register(name: str, connector_name: str, about: str):
    assert connector_name in CONNECTORS, connector_name

    def wrap(make: Callable[[Run], Tool]):
        REGISTRY[name] = Entry(name, connector_name, about, make)
        return make
    return wrap


def grant(agent: str, run: Run, names: list[str]) -> list[Tool]:
    """The tools this agent asked for (names) that it may use now. Tools of a connector it may not use are left out."""
    out = []
    run.agent = agent
    for name in names:
        e = REGISTRY.get(name)
        if e is None:
            raise ValueError(f"{agent} asked for an unknown toolkit tool: {name}")
        if not allowed(agent, e.connector, getattr(run.c, "s", None)):
            continue
        if e.data == "client" and not client_data_allowed():
            continue
        if e.connector == "mail" and mailbox(run.c) is None:
            continue
        out.append(e.make(run))
    return out


def catalog(s=None) -> dict:
    """What the toolkit holds and who may use what (GET /api/toolkit)."""
    cfg, sw = check_config(), switches(s)
    return {"clientDataToAI": client_data_allowed(),
            "connectors": {k: {"data": d, "about": a, "built": b, **connector_detail(k, cfg=cfg, sw=sw)} for k, (d, a, b) in CONNECTORS.items()},
            "permissions": {agent: {k: detail(agent, k, cfg=cfg, sw=sw)["state"] for k in CONNECTORS} for agent in CEILING},
            "tools": [{"name": e.name, "connector": e.connector, "data": e.data, "about": e.about} for e in REGISTRY.values()]}


def table(s) -> dict:
    """The permission table for a management screen: every agent x connector, with state, source and who changed it."""
    cfg, sw = check_config(), switches(s)
    return {"connectors": [{"name": k, "data": d, "about": a, "built": b, **connector_detail(k, cfg=cfg, sw=sw)}
                           for k, (d, a, b) in CONNECTORS.items()],
            "agents": [{"agent": agent, "connectors": {k: {**detail(agent, k, cfg=cfg, sw=sw),
                                                           "ceiling": CEILING[agent].get(k, "forbidden")} for k in CONNECTORS}}
                       for agent in CEILING],
            "clientDataToAI": client_data_allowed()}


# web connector
def _web(run: Run, name: str) -> Tool:
    if "web" not in run.cache:
        run.cache["web"] = {t.name: t for t in web_tools(run.c, run.pages, run.company, run.domain, web_for(run.agent, run.c))}
    return run.cache["web"][name]


register("company_pages", "web", "Pages from and about the company")(lambda r: _web(r, "company_pages"))
register("search_web", "web", "Search the web")(lambda r: _web(r, "search_web"))
register("read_page", "web", "Read the full text of a page the agent found")(lambda r: _web(r, "read_page"))


# mail connector (Gmail, or a mock mailbox; see api/toolkit/mail.py)
@register("mail_thread", "mail", "The email thread with the client: what CGS sent and what the client wrote")
def _mail_thread(run: Run) -> Tool:
    from api import models as m
    from api.toolkit.mail import MailError

    def mail_thread(_args):
        r = run.c.s.get(m.Outreach, run.opp.id) if run.opp else None
        if not r or not r.mail_thread_id:
            return {"messages": [], "note": "No email thread yet: nothing was sent through the workspace."}
        try:
            msgs = mail_for(run.agent, run.c).thread(r.mail_thread_id)
        except MailError as e:
            raise ToolError(f"The mailbox could not be read: {e}") from None
        return {"messages": [{"from": "CGS" if x["ours"] else "client", "on": x["date"], "subject": x["subject"], "text": x["text"][:1500]}
                             for x in msgs[-6:]]}

    return Tool("mail_thread", "The email thread with this client, newest last: what CGS sent and what the client wrote.",
                params({}), mail_thread)
