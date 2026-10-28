"""Governance of the agents' connectors (api/toolkit): the code sets what each agent may ever use, config/toolkit.json
switches connectors on or off inside that limit, and every connector client reaches an agent through one gate."""
from datetime import date
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from api import agent_runtime as R
from api import autonomy as AU
from api import evidence_agent as EA
from api import jobs as J
from api import models as m
from api import outreach_agent as OA
from api import toolkit as TK
from api.llm import make_provider
from api.main import create_app
from api.settings import Settings
from api.toolkit.mail import MockMail
from agent_fakes import play

ME = {"X-User": "Alex Morgan"}
ROOT = Path(__file__).resolve().parents[2]
AGENT_MODULES = ["discovery_agent", "direction_agent", "research_agent", "outreach_agent", "evidence_agent", "next_step_agent",
                 "manager_agent", "live_agents"]


def with_config(monkeypatch, connectors=None, agents=None):
    cfg = TK.config()
    new = {"connectors": {**cfg["connectors"], **(connectors or {})}, "agents": agents or {}}
    monkeypatch.setattr(TK, "config", lambda: new)
    return new


class FakeCtx:
    def __init__(self, box=None):
        self.mail_factory = (lambda: box) if box else None
        self.search_factory = None
        self.today = date(2026, 10, 1)


def names(tools):
    return [t.name for t in tools]


# ---------- the permission table ----------
def test_defaults_follow_the_ceiling_in_code():
    assert TK.state("Research agent", "web") == "on"
    assert TK.state("Research agent", "mail") == "forbidden"   # the research agent never reads email
    assert TK.state("Outreach agent", "mail") == "off"         # allowed, but off until someone switches it on
    assert TK.state("Discovery agent", "crm") == "not built"
    assert TK.state("Manager agent", "web") == "forbidden"
    # every agent the job board knows has a row in the table
    assert {a for a, _, _ in J.KINDS.values() if a.endswith("agent")} <= set(TK.CEILING)


def test_configuration_cannot_go_beyond_the_ceiling(monkeypatch, tmp_path):
    for bad, message in [({"agents": {"Research agent": ["web", "mail"]}}, "forbids"),
                         ({"agents": {"Research agent": "web"}}, "must list"),
                         ({"agents": {"Sales agent": ["web"]}}, "unknown agent"),
                         ({"agents": {"Research agent": ["ftp"]}}, "unknown connector"),
                         ({"connectors": {"ftp": True}, "agents": {}}, "unknown connector")]:
        with pytest.raises(TK.ConfigError, match=message):
            TK.check_config(bad)
    monkeypatch.setattr(TK, "config", lambda: {"connectors": {"web": True}, "agents": {"Research agent": ["mail"]}})
    with pytest.raises(TK.ConfigError):  # the workspace refuses to start rather than ignore it
        create_app(Settings(db_path=tmp_path / "x.sqlite3"))


def test_a_person_can_switch_a_connector_off_for_one_agent_or_for_all(monkeypatch):
    c = FakeCtx()
    c.search_factory = lambda: "search client"
    assert TK.web_for("Research agent", c)._client == "search client"  # handed out metered
    with_config(monkeypatch, agents={"Research agent": []})
    with pytest.raises(TK.Denied, match="Research agent may not use the web connector: it is not switched on"):
        TK.web_for("Research agent", c)
    assert TK.web_for("Discovery agent", c)._client == "search client"  # the others keep it
    with_config(monkeypatch, connectors={"web": False})
    with pytest.raises(TK.Denied, match="connector is turned off"):
        TK.web_for("Discovery agent", c)
    with pytest.raises(TK.Denied, match="the code does not allow it"):
        TK.mail_for("Research agent", FakeCtx(MockMail()))


def test_shared_tools_follow_the_table_and_the_client_data_gate(monkeypatch):
    monkeypatch.delenv("CLIENT_DATA_TO_AI", raising=False)
    want = ["company_pages", "search_web", "read_page", "mail_thread"]
    run = TK.Run(FakeCtx(MockMail()), "Halvorsen", "halvorsen-castings.example")
    assert names(TK.grant("Outreach agent", run, want)) == want[:3]  # mail is off by default
    with_config(monkeypatch, agents={"Outreach agent": ["web", "library", "mail"]})
    assert names(TK.grant("Outreach agent", run, want)) == want[:3]  # switched on, but client data may not reach the AI yet
    monkeypatch.setenv("CLIENT_DATA_TO_AI", "allowed")
    assert names(TK.grant("Outreach agent", run, want)) == want
    assert "mail_thread" not in names(TK.grant("Outreach agent", TK.Run(FakeCtx(None)), want))  # no mailbox
    assert TK.grant("Research agent", run, ["mail_thread"]) == []  # forbidden, whatever it asks for
    with pytest.raises(ValueError, match="unknown toolkit tool: send_email"):
        TK.grant("Outreach agent", run, ["send_email"])


def test_no_agent_tool_writes_to_the_outside_world():
    assert set(TK.REGISTRY) == {"company_pages", "search_web", "read_page", "mail_thread"}
    assert all(e.data in TK.DATA_CLASSES for e in TK.REGISTRY.values())


# ---------- agents see only what they may use ----------
@pytest.fixture
def demo(tmp_path):
    return create_app(Settings(db_path=tmp_path / "gov.sqlite3"))


def test_an_agent_is_built_without_the_tools_of_a_connector_it_may_not_use(demo, monkeypatch):
    with demo.state.db.session() as s:
        c = AU.agent_ctx(demo, s)
        o = s.get(m.Opportunity, "halvorsen")
        build = lambda: names(OA.outreach_agent(c, o, purpose="first email", angle=None, tone="standard", sender="Alex")[0].tools)
        assert {"company_pages", "search_web", "read_page", "add_fact", "search_library"} <= set(build())
        with_config(monkeypatch, agents={"Outreach agent": ["library"]})
        assert not {"company_pages", "search_web", "read_page", "add_fact"} & set(build()) and "search_library" in build()
        with_config(monkeypatch, connectors={"library": False})
        assert "search_library" not in build()
        note = m.Note(id="n-gov", opportunity_id=o.id, type="Meeting notes", on=c.today, text="We talked.", added_by="Alex")
        assert "search_library" not in names(EA.evidence_agent(c, o, note, [])[0].tools)


def test_a_denied_agent_stops_with_a_clear_answer(tmp_path, monkeypatch):
    db = tmp_path / "live.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=lambda u, h, payload, **k: play(payload))
    api = TestClient(app)
    with_config(monkeypatch, agents={"Discovery agent": []})
    r = api.post("/api/candidates/recommend", headers=ME, json={"intent": ""})
    assert r.status_code == 403 and r.json()["code"] == "not_permitted" and "Discovery agent" in r.json()["error"]
    with_config(monkeypatch, agents={"Direction agent": []})
    assert api.post("/api/direction/problems", headers=ME, json={}).status_code == 403
    t = api.get("/api/toolkit", headers=ME).json()
    assert t["permissions"]["Direction agent"]["library"] == "off" and t["permissions"]["Research agent"]["mail"] == "forbidden"


# ---------- one gate: no agent reaches a connector around it ----------
def test_agent_modules_reach_connectors_only_through_the_toolkit():
    direct = re.compile(r"make_search\(|knowledge\.library\(|(?<![\w.])library\(\)|mail_factory|search_factory\(|mailbox\(|GmailMail|TavilySearch|MockSearch\(")
    found = []
    for mod in AGENT_MODULES:
        for n, line in enumerate((ROOT / "api" / f"{mod}.py").read_text(encoding="utf-8").splitlines(), 1):
            if direct.search(line) and "toolkit." not in line and not line.lstrip().startswith("#"):
                found.append(f"api/{mod}.py:{n}: {line.strip()}")
    assert not found, "Reach connectors through api/toolkit (web_for, library_for, mail_for, connector, grant):\n" + "\n".join(found)


# ---------- people manage the table in the workspace (a management screen calls these) ----------
def put(api, path, enabled, reason="Testing the permission screen", who=ME):
    return api.put(f"/api/toolkit/{path}", headers=who, json={"enabled": enabled, "reason": reason})


def cell(api, agent, connector):
    rows = api.get("/api/toolkit/permissions", headers=ME).json()["agents"]
    return next(r for r in rows if r["agent"] == agent)["connectors"][connector]


def test_the_table_shows_every_agent_and_connector_with_where_it_comes_from(demo):
    api = TestClient(demo)
    t = api.get("/api/toolkit/permissions", headers=ME).json()
    assert t["canChange"] is True and {r["agent"] for r in t["agents"]} == set(TK.CEILING)
    assert cell(api, "Research agent", "mail") == {"state": "forbidden", "editable": False, "source": "code", "ceiling": "forbidden"}
    assert cell(api, "Outreach agent", "mail") == {"state": "off", "editable": True, "source": "default", "ceiling": "allowed"}
    assert cell(api, "Discovery agent", "crm")["state"] == "not built"
    assert next(x for x in t["connectors"] if x["name"] == "web")["on"] is True


def test_a_person_switches_an_agent_off_it_takes_effect_and_the_change_is_kept(tmp_path):
    db = tmp_path / "live.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=lambda u, h, payload, **k: play(payload))
    api = TestClient(app)
    r = put(api, "permissions/Discovery agent/web", False, "Pause web searches while CGS reviews costs").json()
    assert (r["before"], r["after"]) == ("on", "off")
    c = cell(api, "Discovery agent", "web")
    assert (c["state"], c["source"], c["changedBy"], c["reason"]) == ("off", "switch", "Alex Morgan", "Pause web searches while CGS reviews costs")
    denied = api.post("/api/candidates/recommend", headers=ME, json={"intent": ""})
    assert denied.status_code == 403 and "not switched on" in denied.json()["error"]  # the switch in the database governs the agent
    assert put(api, "permissions/Discovery agent/web", None, "Cost review done").json()["after"] == "on"  # back to the default
    assert cell(api, "Discovery agent", "web")["source"] == "default"
    h = api.get("/api/toolkit/permissions/history", headers=ME).json()
    assert [(x["set"], x["before"], x["after"], x["by"]) for x in h] == [("default", "off", "on", "Alex Morgan"), ("off", "on", "off", "Alex Morgan")]
    with app.state.db.session() as s:
        assert s.query(m.Activity).filter(m.Activity.text.like("%switched the web connector off for Discovery agent%")).count() == 1


def test_what_the_code_forbids_cannot_be_switched_on(demo):
    api = TestClient(demo)
    assert put(api, "permissions/Research agent/mail", True).json()["code"] == "forbidden"
    assert put(api, "permissions/Discovery agent/crm", True).json()["code"] == "not_built"
    assert put(api, "permissions/Sales agent/web", True).status_code == 404
    assert put(api, "permissions/Research agent/ftp", True).status_code == 404
    assert put(api, "permissions/Research agent/web", False, reason="").status_code == 400  # a reason is required
    assert api.get("/api/toolkit/permissions/history", headers=ME).json() == []  # nothing was changed


def test_a_connector_can_be_switched_off_for_every_agent(demo):
    api = TestClient(demo)
    assert put(api, "connectors/web", False, "Tavily account suspended").json()["after"] == "off"
    assert cell(api, "Research agent", "web")["state"] == "connector off"
    with demo.state.db.session() as s:
        c = AU.agent_ctx(demo, s)
        tools = names(OA.outreach_agent(c, s.get(m.Opportunity, "halvorsen"), purpose="first email", angle=None, tone="standard", sender="A")[0].tools)
        assert not {"company_pages", "search_web", "read_page", "add_fact"} & set(tools)
    r = put(api, "permissions/Research agent/web", True, "Research needs the web").json()
    assert r["after"] == "connector off" and "off for every agent" in r["warnings"][0]
    assert put(api, "connectors/web", None, "Tavily is back").json()["after"] == "on"
    assert cell(api, "Research agent", "web")["state"] == "on"


def test_switching_email_on_says_what_else_is_needed(demo, monkeypatch):
    monkeypatch.delenv("CLIENT_DATA_TO_AI", raising=False)
    r = put(TestClient(demo), "permissions/Outreach agent/mail", True, "Follow-ups should read the thread").json()
    assert r["after"] == "on" and "CLIENT_DATA_TO_AI" in r["warnings"][0]


def test_only_the_named_admins_may_change_permissions(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "a.sqlite3", admins=("Sarah Patel",))))
    assert api.get("/api/toolkit/permissions", headers=ME).json()["canChange"] is False
    assert put(api, "permissions/Research agent/web", False).json()["code"] == "wrong_role"
    assert put(api, "permissions/Research agent/web", False, who={"X-User": "Sarah Patel"}).json()["after"] == "off"


def test_the_research_thread_reads_the_switches_too(demo):
    from api.research_agent import AgentResearchBridge
    from api.search import MockSearch
    put(TestClient(demo), "permissions/Research agent/web", False, "No web research this week")
    bridge = AgentResearchBridge(lambda: make_provider("gemini", "m", key="k", transport=lambda u, h, payload, **k: play(payload)),
                                 lambda as_of: MockSearch(), background=False, session_factory=demo.state.db.session)
    run = bridge.start("Halvorsen Precision Castings", "halvorsen-castings.example", "Alex Morgan")
    assert bridge.status(run)["status"] == "failed" and "Research agent may not use the web connector" in bridge.status(run)["error"]


# ---------- usage log, daily limits and undo ----------
def live(tmp_path):
    db = tmp_path / "use.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db))
    app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=lambda u, h, payload, **k: play(payload))
    return app, TestClient(app)


def usage_rows(api, agent, connector):
    return [r for r in api.get("/api/toolkit/usage", headers=ME).json()["rows"] if r["agent"] == agent and r["connector"] == connector]


def test_every_connector_call_is_counted_and_a_refusal_too(tmp_path):
    app, api = live(tmp_path)
    assert api.post("/api/candidates/recommend", headers=ME, json={"intent": ""}).status_code == 200
    web = usage_rows(api, "Discovery agent", "web")
    assert web and web[0]["ok"] > 0 and web[0]["denied"] == 0
    with app.state.db.session() as s:
        assert {u.action for u in s.query(m.ConnectorUse).filter(m.ConnectorUse.agent == "Discovery agent")} <= {"search", "read", "pages_for"}
    put(api, "permissions/Discovery agent/web", False, "Testing the usage log")
    assert api.post("/api/candidates/recommend", headers=ME, json={"intent": ""}).status_code == 403
    assert usage_rows(api, "Discovery agent", "web")[0]["denied"] == 1  # kept although the request failed
    used = next(x for x in api.get("/api/toolkit/usage", headers=ME).json()["limits"] if x["connector"] == "web")
    assert used["perDay"] == 150 and used["source"] == "config file" and used["usedToday"] == web[0]["ok"] + web[0]["failed"]


def test_a_daily_limit_stops_further_calls_and_the_agent_is_told(tmp_path):
    app, api = live(tmp_path)
    r = api.put("/api/toolkit/limits/web", headers=ME, json={"perDay": 1, "reason": "Tavily credits are nearly used up"}).json()
    assert (r["before"], r["after"]) == ("150 per day", "1 per day")
    assert api.post("/api/candidates/recommend", headers=ME, json={"intent": ""}).status_code == 200  # uses the one call
    with app.state.db.session() as s:
        w = TK.web_for("Research agent", AU.agent_ctx(app, s))
        with pytest.raises(TK.LimitReached, match="daily limit of 1"):
            w.search("Halvorsen news")
    assert issubclass(TK.LimitReached, R.ToolError)  # inside an agent's tool the agent is told, and may finish
    lim = next(x for x in api.get("/api/toolkit/usage", headers=ME).json()["limits"] if x["connector"] == "web")
    assert lim["usedToday"] == 1 and lim["source"] == "switch"
    assert usage_rows(api, "Research agent", "web")[0]["limit"] == 1
    assert api.put("/api/toolkit/limits/web", headers=ME, json={"perDay": 0, "reason": "No limit for the demo"}).json()["after"] == "no limit"
    assert api.put("/api/toolkit/limits/web", headers=ME, json={"perDay": -1, "reason": "Wrong number"}).status_code == 400
    assert api.put("/api/toolkit/limits/crm", headers=ME, json={"perDay": 5, "reason": "Not built yet"}).status_code == 404


def test_email_a_person_sends_counts_against_the_mail_limit(demo):
    api = TestClient(demo)
    api.put("/api/opportunities/halvorsen/outreach/draft", headers=ME, json={"subject": "Hello", "body": "Hi, [[f1|a fact]]. A call?"})
    api.put("/api/toolkit/limits/mail", headers=ME, json={"perDay": 1, "reason": "One email a day while testing"})
    p = api.get("/api/opportunities/halvorsen/outreach/email", headers=ME, params={"to": "dana@halvorsen.example"}).json()
    assert api.post("/api/opportunities/halvorsen/outreach/email", headers=ME,
                    json={"to": p["to"], "mode": "send", "seenHash": p["seenHash"]}).status_code == 200
    sync = api.post("/api/mail/sync", headers=ME).json()
    assert "daily limit of 1" in sync["errors"][0]  # the next call is refused, and said so
    assert usage_rows(api, "Workspace (person)", "mail")[0]["ok"] == 1 and usage_rows(api, "Mail rules", "mail")[0]["limit"] == 1


def test_a_change_can_be_undone_and_the_undo_is_kept_too(demo):
    api = TestClient(demo)
    put(api, "permissions/Research agent/web", False, "Pause research")
    api.put("/api/toolkit/limits/web", headers=ME, json={"perDay": 50, "reason": "Lower for this week"})
    h = api.get("/api/toolkit/permissions/history", headers=ME).json()
    limit_change, switch_change = h[0], h[1]
    assert limit_change["kind"] == "limit" and switch_change["kind"] == "switch" and switch_change["undoable"]
    r = api.post(f"/api/toolkit/permissions/history/{switch_change['id']}/undo", headers=ME, json={"reason": "Pause no longer needed"}).json()
    assert r["after"] == "on" and cell(api, "Research agent", "web")["source"] == "default"
    again = api.post(f"/api/toolkit/permissions/history/{switch_change['id']}/undo", headers=ME, json={"reason": "Twice"})
    assert again.json()["code"] == "not_latest"  # the undo is now the latest change to that setting
    assert api.post(f"/api/toolkit/permissions/history/{limit_change['id']}/undo", headers=ME, json={"reason": "Back to normal"}).json()["after"] == "150 per day"
    newest = api.get("/api/toolkit/permissions/history", headers=ME).json()[0]
    assert newest["reason"].startswith(f"Undo of change #{limit_change['id']}") and newest["by"] == "Alex Morgan"
