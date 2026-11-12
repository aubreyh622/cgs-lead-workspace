"""MCP server cgs-insightly: the CRM (Insightly, or the in-memory mock), with provider-neutral records.

Reads: crm_info, check_setup, matches, find_lead, get_lead, get_deal, get_task.
Writes: save_lead, add_note, save_task, convert. In the workspace these run only after a person acts (api/crm_sync.py);
they are never given to an agent. An ADK port should keep that rule: give agents the read tools only (tool_filter).

Credentials: INSIGHTLY_API_KEY / INSIGHTLY_API_URL (Secret Manager on Cloud Run), or
%USERPROFILE%\\.cgs-workspace\\insightly.env. CRM_PROVIDER picks insightly or mock; field names come from config/crm.json.
"""
from __future__ import annotations

from functools import lru_cache
import os

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from api.toolkit.crm import CrmError
from cgs_tools.common import main, run

mcp = FastMCP("cgs-insightly", instructions="CGS's CRM (Insightly): leads, contacts, organizations, opportunities, tasks. "
                                            "Write tools change CGS's records; call them only after a person approved.")


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Lead(Open):
    id: str
    name: str | None = ""
    company: str | None = ""
    email: str | None = ""
    status: str | None = Field("", description="The CRM's status name")
    converted: bool | None = False
    mailbox: str | None = Field("", description="The lead's own address: BCC it and the email is logged on the lead")
    url: str | None = ""
    workspace_id: str | None = None
    updated: str | None = ""
    fields: dict = Field(default_factory=dict, description="Field values as the CRM holds them, for the read-back")


class LeadIn(Open):
    """A lead to save. Any field left out is not changed."""
    first: str | None = None
    last: str | None = None
    title: str | None = None
    email: str | None = None
    company: str | None = None
    website: str | None = None
    industry: str | None = None
    rating: int | None = Field(None, description="1-5 lead rating from Step 4")
    status: str | None = Field(None, description="A workspace stage name (identified, contacted, ...), mapped by config/crm.json")
    source: str | None = Field(None, description="ai | manual | import")
    owner: str | None = None
    description: str | None = None
    workspace_id: str | None = None
    target_problem: str | None = None
    evidence_url: str | None = None
    priority_tier: int | float | None = None
    lead_type: str | None = None


class Deal(Open):
    id: str
    name: str | None = ""
    state: str | None = Field("", description="OPEN, WON, LOST, ABANDONED, SUSPENDED")
    stage: str | None = ""
    updated: str | None = ""
    url: str | None = ""


class DealIn(Open):
    """The opportunity made at Qualification."""
    name: str
    company: str
    workspace_id: str
    first: str | None = None
    last: str | None = None
    title: str | None = None
    email: str | None = None
    owner: str | None = None
    details: str | None = None


class Task(Open):
    id: str
    title: str | None = ""
    due: str | None = ""
    status: str | None = ""
    completed: bool | None = False


class Matches(BaseModel):
    leads: list[Lead]
    contacts: list[dict]
    organizations: list[dict]
    opportunities: list[dict]


class LeadResult(BaseModel):
    lead: Lead | None = None


class DealResult(BaseModel):
    deal: Deal | None = None


class TaskResult(BaseModel):
    task: Task | None = None


class Info(BaseModel):
    provider: str
    ready: bool
    account: str = ""
    config: dict = Field(description="config/crm.json as the server reads it (statuses, stages, field names)")


class Converted(Open):
    organisation: str | int
    contact: str | int
    opportunity: str | int
    url: str = ""


class Text(BaseModel):
    text: str


@lru_cache(maxsize=1)
def crm():
    from api.toolkit.crm import make_crm
    c = make_crm(os.environ.get("CRM_PROVIDER", "").strip().lower(), os.environ.get("APP_MODE", "demo").strip().lower(), "write_approved",
                 local=True)
    if c is None:
        raise CrmError("No CRM is set up (CRM_PROVIDER).")
    return c


def _lead(x):
    return LeadResult(lead=Lead(**x) if x else None)


@mcp.tool()
async def crm_info() -> Info:
    """Which CRM answers, whether it is signed in, and the field mapping it uses."""
    def go():
        c = crm()
        return Info(provider=c.name, ready=c.ready(), account=c.account() if c.ready() else "", config=c.cfg)
    return await run(go, (CrmError,))


@mcp.tool()
async def check_setup() -> list[str]:
    """What is missing in the CRM account for the workspace (statuses, custom fields, pipeline); empty = ready."""
    return await run(lambda: list(crm().check_setup()) if hasattr(crm(), "check_setup") else [], (CrmError,))


@mcp.tool()
async def record_url(kind: str, record_id: str) -> Text:
    """Where a person opens a record (kind: Lead, Contact, Organisation, Opportunity)."""
    return await run(lambda: Text(text=crm().url(kind, record_id)), (CrmError,))


@mcp.tool()
async def matches(company: str = "", email: str = "") -> Matches:
    """What the CRM already holds for a company (exact name) or a person's email."""
    return await run(lambda: Matches(**crm().matches(company=company, email=email)), (CrmError,))


@mcp.tool()
async def find_lead(workspace_id: str = "", email: str = "") -> LeadResult:
    """The lead for a workspace record (Workspace ID field), else the lead with this email."""
    return await run(lambda: _lead(crm().find_lead(workspace_id=workspace_id, email=email)), (CrmError,))


@mcp.tool()
async def get_lead(lead_id: str) -> LeadResult:
    return await run(lambda: _lead(crm().get_lead(lead_id)), (CrmError,))


@mcp.tool()
async def get_deal(deal_id: str) -> DealResult:
    """The CRM opportunity: its state and pipeline stage."""
    return await run(lambda: DealResult(deal=(lambda d: Deal(**d) if d else None)(crm().get_deal(deal_id))), (CrmError,))


@mcp.tool()
async def get_task(task_id: str) -> TaskResult:
    return await run(lambda: TaskResult(task=(lambda t: Task(**t) if t else None)(crm().get_task(task_id))), (CrmError,))


@mcp.tool()
async def save_lead(lead: LeadIn, lead_id: str = "", skip: list[str] | None = None) -> LeadResult:
    """WRITE. Create a lead, or update lead_id leaving the fields in skip as the CRM holds them."""
    return await run(lambda: _lead(crm().save_lead(lead.model_dump(exclude_unset=True), lead_id or None, tuple(skip or ()))), (CrmError,))


@mcp.tool()
async def add_note(record: str, record_id: str, title: str, text: str) -> Text:
    """WRITE. A note on a lead, opportunity or contact (record). Returns the note's id."""
    return await run(lambda: Text(text=crm().add_note(record, record_id, title, text)), (CrmError,))


@mcp.tool()
async def save_task(record: str, record_id: str, title: str, due: str = "", owner: str = "", task_id: str = "",
                    done: bool = False) -> TaskResult:
    """WRITE. The next step as a task on a lead or opportunity; with task_id: rename, move or complete it."""
    return await run(lambda: TaskResult(task=Task(**crm().save_task(record, record_id, title, due or None, owner or None, task_id or None, done))),
                     (CrmError,))


@mcp.tool()
async def convert(lead_id: str, deal: DealIn) -> Converted:
    """WRITE. Qualification: organization, contact and opportunity, linked; the lead is marked converted."""
    return await run(lambda: Converted(**crm().convert(lead_id, deal.model_dump(exclude_unset=True))), (CrmError,))


if __name__ == "__main__":
    main(mcp)
