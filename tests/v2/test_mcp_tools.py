"""The MCP tool servers (cgs_tools/) give the same answers as the connectors called in-process, raise the same errors,
never open the workspace database, and work from Google ADK's own McpToolset."""
from __future__ import annotations

import asyncio
from datetime import date
import os
import subprocess
import sys

import pytest

from api.llm import LLMError
from api.search import MockSearch, make_search
from api.toolkit.crm import CrmError, MockCRM, make_crm
from api.toolkit.drive import DriveError, MockDrive, make_drive
from cgs_tools import SERVERS, client as MCP

AS_OF = date(2026, 11, 1)


@pytest.fixture
def over_mcp(monkeypatch):
    monkeypatch.setenv("TOOLS_TRANSPORT", "mcp")
    monkeypatch.delenv("CGS_TOOLS_SERVER", raising=False)
    yield
    MCP.close_all()


def without_ids(x):
    """Records with the ids each CRM handed out taken away (the server's mock may have handed out others before)."""
    if isinstance(x, dict):
        return {k: without_ids(v) for k, v in x.items() if k not in ("id", "url", "mailbox", "organisation", "contact", "opportunity")}
    if isinstance(x, list):
        return [without_ids(v) for v in x]
    return x


def test_factories_hand_out_mcp_clients_only_when_asked(over_mcp, monkeypatch):
    assert isinstance(make_search("mock", as_of=AS_OF), MCP._MockSearch)
    assert isinstance(make_crm("mock", "demo", "write_approved"), MCP.CrmClient)
    assert isinstance(make_drive("mock"), MCP.DriveClient)
    assert make_crm("mock", "demo", "disabled") is None and make_drive("off") is None
    assert isinstance(make_search("mock", local=True), MockSearch)  # what a server builds for itself
    monkeypatch.setenv("CGS_TOOLS_SERVER", "1")
    assert isinstance(make_crm("mock", "demo", "write_approved"), MockCRM)  # a server never calls another server
    monkeypatch.setenv("TOOLS_TRANSPORT", "inprocess")
    monkeypatch.delenv("CGS_TOOLS_SERVER")
    assert isinstance(make_drive("mock"), MockDrive)


def test_search_same_answers(over_mcp):
    local, remote = MockSearch(as_of=AS_OF), make_search("mock", as_of=AS_OF)
    for q, since, news in (("manufacturer completes acquisition", None, False), ("Chief Digital Officer", date(2026, 6, 1), True),
                           ("nothing matches zzqx", None, False)):
        assert remote.search(q, since, news) == local.search(q, since, news)
    hit = local.search("acquisition")[0]["url"]
    assert remote.read(hit) == local.read(hit)
    assert remote.read("https://nowhere.example/x") is None
    company = local.pages[0]["title"].split(" | ")[0].split(" ")[0]
    assert remote.pages_for(company, "") == local.pages_for(company, "")
    assert remote.name == "mock" and hasattr(remote, "pages_for")


def test_search_error_arrives_as_llm_error(over_mcp, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "")
    with pytest.raises(LLMError) as e:
        MCP.SearchClient("tavily").search("anything")
    assert e.value.kind == "auth" and "TAVILY_API_KEY" in str(e.value)
    assert not hasattr(MCP.SearchClient("tavily"), "pages_for")  # the real web has no page list per company


def test_crm_same_answers(over_mcp):
    local, remote = MockCRM(), make_crm("mock", "demo", "write_approved")
    assert remote.cfg == local.cfg and remote.name == "mock" and remote.ready() and remote.account() == local.account()
    tag = os.urandom(3).hex()
    lead = {"first": "Ana", "last": "Test", "company": f"Acme {tag}", "email": f"ana.{tag}@example.com", "status": "identified",
            "workspace_id": f"opp-{tag}", "source": "ai", "priority_tier": 2, "target_problem": "Planning"}
    a, b = local.save_lead(lead), remote.save_lead(lead)
    assert without_ids(b) == without_ids(a)
    a2, b2 = local.save_lead({"status": "contacted"}, a["id"], skip=("title",)), remote.save_lead({"status": "contacted"}, b["id"], skip=("title",))
    assert without_ids(b2) == without_ids(a2) and b2["status"] == a2["status"]
    assert without_ids(remote.find_lead(f"opp-{tag}")) == without_ids(local.find_lead(f"opp-{tag}"))
    assert without_ids(remote.matches(f"Acme {tag}", "")) == without_ids(local.matches(f"Acme {tag}", ""))
    assert remote.get_lead("999999") is None and remote.get_deal("999999") is None and remote.get_task("999999") is None
    assert bool(remote.add_note("lead", b["id"], "Email", "Sent")) == bool(local.add_note("lead", a["id"], "Email", "Sent"))
    ta, tb = local.save_task("lead", a["id"], "Call", "2026-11-20", "Sam"), remote.save_task("lead", b["id"], "Call", "2026-11-20", "Sam")
    assert without_ids(tb) == without_ids(ta)
    assert without_ids(remote.save_task("lead", b["id"], "Call", None, task_id=tb["id"], done=True)) == \
        without_ids(local.save_task("lead", a["id"], "Call", None, task_id=ta["id"], done=True))
    deal = {"name": f"Acme {tag} - planning", "company": f"Acme {tag}", "workspace_id": f"opp-{tag}", "first": "Ana", "last": "Test",
            "email": f"ana.{tag}@example.com"}
    ca, cb = local.convert(a["id"], deal), remote.convert(b["id"], deal)
    assert set(cb) == set(ca) and without_ids(remote.get_deal(str(cb["opportunity"]))) == without_ids(local.get_deal(str(ca["opportunity"])))
    assert remote.get_lead(b["id"])["converted"] is True
    # the record exactly as api/crm_sync.py builds it: a number rating, no email, a float tier, empty fields as None
    rec = {"first": "Lee", "last": "", "title": "", "email": "", "company": f"Bolt {tag}", "website": "", "industry": "Manufacturing",
           "rating": 4, "status": "identified", "source": "ai", "owner": None, "description": "x", "workspace_id": f"opp2-{tag}",
           "target_problem": "", "evidence_url": "", "priority_tier": 2.0, "lead_type": None}
    assert without_ids(remote.save_lead(rec)) == without_ids(local.save_lead(rec))


def test_drive_same_answers_and_errors(over_mcp):
    local, remote = MockDrive(), make_drive("mock")
    for d in (local, remote):
        top = d.make_folder("Records")
        assert d.find_folder("Records") == top and d.folder(top) == "Records"
        doc = d.save(top, "Acme - record", "<h1>Acme</h1><p>Draft</p>", "doc")
        sheet = d.save(top, "Workspace log", "when,what\n2026-11-12,sent", "sheet")
        again = d.save(top, "Acme - record", "<h1>Acme</h1><p>Sent</p>", "doc", doc["id"])
        assert again["id"] == doc["id"] and "spreadsheets" in sheet["link"]
        d.result = (top, doc, sheet)
    lt, rt = local.result[0], remote.result[0]
    lf, ln = local.walk([lt])
    rf, rn = remote.walk([rt])
    assert [(f.name, f.mime, f.path) for f in rf] == [(f.name, f.mime, f.path) for f in lf] and rn == ln
    assert [remote.text(f) for f in rf] == [local.text(f) for f in lf]
    assert [f.name for f in remote.children(rt)] == [f.name for f in local.children(lt)]
    assert remote.walk(["missing"])[1] == local.walk(["missing"])[1]
    for d in (local, remote):
        with pytest.raises(DriveError, match="not found"):
            d.save("missing", "x", "y")


def test_servers_never_open_the_workspace_database():
    for module in SERVERS.values():
        out = subprocess.run([sys.executable, "-c", f"import sys, {module}; "
                              "print(sorted(m for m in sys.modules if m.split('.')[0] == 'sqlalchemy' or m in ('api.db', 'api.models', 'api.services')))"],
                             capture_output=True, text=True, timeout=120, cwd=str(MCP.ROOT))
        assert out.returncode == 0, out.stderr[-500:]
        assert out.stdout.strip() == "[]", f"{module} imports {out.stdout.strip()}"


def test_every_tool_is_typed_and_writes_are_marked():
    from api.check_mcp import list_tools
    env = {"WEB_MODE": "mock", "CRM_PROVIDER": "mock", "DRIVE_PROVIDER": "mock"}
    for server in SERVERS:
        tools = asyncio.run(list_tools(server, env))
        assert tools and all(t["input"] and t["output"] for t in tools), server
        writes = {t["name"] for t in tools if t["write"]}
        assert writes == {"cgs-search": set(), "cgs-insightly": {"save_lead", "add_note", "save_task", "convert"},
                          "cgs-drive": {"make_folder", "save_record"}}[server]


def test_adk_agent_uses_the_servers_unchanged():
    from api.check_mcp import adk_agent_run
    out = asyncio.run(adk_agent_run({"WEB_MODE": "mock"}))
    assert out["called"] == ["web_search"]
    assert out["answer"] == MockSearch(as_of=AS_OF).search("manufacturer completes acquisition")[0]["title"]
    assert not {"save_lead", "add_note", "save_task", "convert"} & set(out["tools"])  # tool_filter: agents get CRM reads only
