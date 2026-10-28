"""Autonomous agents: they find work (events, patrol, handoffs), prepare it for people, and respect the brakes.
A fake AI stands in for Gemini, so these run offline."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api import autonomy as AU
from api.llm import make_provider
from api.main import create_app
from api.search import MockSearch
from api.settings import Settings
from agent_fakes import play, tool_names

ME = {"X-User": "Alex Morgan"}


class SchemaAI:
    """Answers each agent by the shape of answer it asks for. evidence(notes) returns the evidence agent's answer."""

    def __init__(self, label="meet", evidence=None):
        self.label, self.evidence, self.calls = label, evidence or (lambda n: {"proposals": [], "people_to_find": []}), []

    def __call__(self, url, headers, payload, **kw):
        if "tools" in payload:  # a tool-using agent's turn (api/agent_runtime.py), played by tests/v2/agent_fakes.py
            names = tool_names(payload)
            self.calls.append("actions" if "suggest" in names else "discovery" if "nominate" in names else "evidence" if "propose" in names
                              else "draft" if "submit_draft" in names else "person")
            return play(payload, evidence=self.evidence)
        props = set(payload["generationConfig"]["responseJsonSchema"]["properties"])
        user = json.loads(payload["contents"][0]["parts"][0]["text"])
        if "found" in props:
            self.calls.append("person")
            page = next((i, p) for i, p in enumerate(user["pages"]) if "leadership" in p["url"])
            name = next((n for n in ("Kate Morrison", "Gregory Hale") if n in page[1]["text"]), "")
            sent = next((s for s in page[1]["text"].split(". ") if name and name in s), "")
            ans = {"found": bool(name), "name": name, "title": "CEO", "page_index": page[0], "quote": sent}
        elif "identity_confirmed" in props:
            self.calls.append("research")
            p = user["pages"][-1]
            quote = p["text"].split(". ")[0]
            ans = {"identity_confirmed": True, "facts": [{"page_index": p["index"], "quote": quote, "fact": quote, "category": "signal"}],
                   "person_name": "", "person_role": "", "person_page_index": -1, "hypothesis": "h", "relevance": "r", "alternative": "a",
                   "unknowns": [], "confidence": "medium"}
        elif "subject" in props:
            self.calls.append("draft")
            f = user["facts"][-1]
            ans = {"subject": "Following up", "body": f"Hi, [[{f['id']}|{' '.join(f['text'].split()[:5])}]]. A short call? Alex Morgan, CGS Advisors"}
        elif "label" in props:
            self.calls.append("label")
            ans = {"label": self.label, "why": "test"}
        elif "proposals" in props:
            self.calls.append("evidence")
            ans = self.evidence(user["notes"])
        elif "actions" in props:
            self.calls.append("actions")
            g = user["open_gaps"][0]
            ans = {"actions": [{"gap_key": g["key"], "title": "Ask who signs off", "reason": "gap", "question": "Who approves?"}]}
        elif "queries" in props:
            self.calls.append("queries")
            ans = {"queries": ["manufacturer merger integration"]}
        elif "candidates" in props:
            self.calls.append("discovery")
            ans = {"candidates": []}
        else:
            self.calls.append("feedback")
            ans = {"summary": "No change.", "propose": False, "field": "problem", "after_list": [], "after_text": "", "title": "",
                   "rationale": "", "feedback_ids": []}
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(ans)}]}}], "usageMetadata": {"promptTokenCount": 3}}


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.delenv("WEB_MODE", raising=False)  # synthetic internet

    def make(ai):
        db = tmp_path / "a.sqlite3"
        if not db.exists():
            create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
        app = create_app(Settings(app_mode="live", db_path=db))
        app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=ai)
        return app, TestClient(app)
    return make


def lead(api, company, label_ai):
    """Take a synthetic company through Steps 1-5: added, researched, accepted, emailed."""
    oid = api.post("/api/opportunities", json={"company": company}, headers=ME).json()["id"]
    api.post("/api/research/sync", headers=ME)
    assert api.post(f"/api/opportunities/{oid}/step4", headers=ME, json={"decision": "accept", "rating": 4, "owner": "Alex Morgan",
                    "next_action": "Send outreach", "duplicates_checked": True}).status_code == 200
    assert api.post(f"/api/opportunities/{oid}/outreach/generate", headers=ME, json={}).status_code == 200
    assert api.post(f"/api/opportunities/{oid}/outreach/sent", headers=ME, json={"via": "Email"}).status_code == 200
    return oid


def inbox(api, kind=None):
    return [i for i in api.get("/api/inbox").json() if not kind or i["kind"] == kind]


def test_future_news_appears_only_when_the_clock_passes_it():
    early, late = MockSearch(as_of=date(2026, 10, 26)), MockSearch(as_of=date(2027, 4, 1))
    titles = lambda w: {p["title"] for p in w.pages_for("Kessler Motion Controls", "kesslermotion.example")}
    assert "Kessler Motion Controls finishes first phase of Arcwell integration" in titles(late) - titles(early)


def test_no_reply_for_a_week_gets_a_follow_up_draft_and_nothing_is_sent(live):
    ai = SchemaAI()
    app, api = live(ai)
    oid = lead(api, "Ardent Castings", ai)
    api.post("/api/demo/advance-days?days=8", headers=ME)
    found = api.post("/api/agents/patrol", headers=ME).json()["queued"]
    assert any(j["kind"] == "followup_draft" and j["opportunityId"] == oid for j in found)
    assert {j["kind"] for j in found} >= {"discovery_round", "feedback_review"}  # weekly rounds
    ran = api.post("/api/agents/run", headers=ME).json()["ran"]
    assert all(r["status"] == "done" for r in ran), ran
    item = next(i for i in inbox(api, "draft") if i["opportunityId"] == oid)
    assert item["owner"] == "Alex Morgan" and item["data"]["purpose"] == "followup"
    o = api.get(f"/api/opportunities/{oid}").json()
    assert o["stage"] == "contacted" and o["outreach"]["status"] == "sent"  # the agent prepared; nobody sent anything
    # patrolling again the same day finds nothing new for this lead
    assert not [j for j in api.post("/api/agents/patrol", headers=ME).json()["queued"] if j["opportunityId"] == oid]


def test_defer_then_news_then_reconnect_draft_chain(live):
    ai = SchemaAI(label="defer")
    app, api = live(ai)
    oid = lead(api, "Kessler Motion Controls", ai)
    sim = MockSearch().simulations["kesslermotion.example"]
    api.post(f"/api/opportunities/{oid}/outreach/reply", headers=ME, json={"text": sim["reply"]})
    api.post(f"/api/opportunities/{oid}/notes", headers=ME, json={"text": sim["notes"]})
    AU.run_pending(app)
    nurture = next(i for i in inbox(api, "nurture") if i["opportunityId"] == oid)
    assert "1 Apr 2027" in nurture["title"]
    assert api.post(f"/api/inbox/{nurture['id']}", headers=ME, json={"action": "apply"}).json()["status"] == "applied"
    assert api.get(f"/api/opportunities/{oid}").json()["stage"] == "nurture"

    days = (date(2027, 4, 2) - date.today()).days
    api.post(f"/api/demo/advance-days?days={days}", headers=ME)
    found = api.post("/api/agents/patrol?run=true", headers=ME).json()
    assert any(j["kind"] == "research_refresh" and j["opportunityId"] == oid for j in found["queued"])
    by_kind = {r["kind"]: r for r in found["ran"] if r["opportunityId"] == oid}
    assert by_kind["research_refresh"]["status"] == "done" and by_kind["reconnect_draft"]["status"] == "done"
    facts = next(i for i in inbox(api, "facts") if i["opportunityId"] == oid)
    assert "Arcwell" in facts["body"]
    draft = next(i for i in inbox(api, "draft") if i["opportunityId"] == oid)
    assert draft["data"]["purpose"] == "reconnect"
    board = api.get("/api/agents/jobs", params={"opportunity": oid}).json()
    chain = next(j for j in board if j["kind"] == "reconnect_draft")
    assert chain["trigger"] == "handoff" and chain["requestedBy"] == "Research agent" and chain["depth"] == 1
    api.post(f"/api/inbox/{facts['id']}", headers=ME, json={"action": "apply"})
    assert any("Arcwell" in f["text"] for f in api.get(f"/api/opportunities/{oid}").json()["brief"]["facts"])


def orchard_evidence(notes):
    q = next((s for s in notes.split("\n") if "CEO would decide" in s), "").strip()
    if not q:
        return {"proposals": [], "people_to_find": []}
    return {"proposals": [], "people_to_find": [{"role": "CEO", "quote": q}, {"role": "Board", "quote": "not in the notes at all"}]}


def test_evidence_agent_asks_research_agent_to_find_the_approver(live):
    ai = SchemaAI(label="need", evidence=orchard_evidence)
    app, api = live(ai)
    oid = lead(api, "Orchard Lane Foods", ai)
    sim = MockSearch().simulations["orchardlanefoods.example"]
    api.post(f"/api/opportunities/{oid}/outreach/reply", headers=ME, json={"text": sim["reply"]})
    r = api.post(f"/api/opportunities/{oid}/notes", headers=ME, json={"text": sim["notes"]}).json()
    assert r["handoffs"] == ["CEO"]  # the request whose quote is not in the notes was dropped by code
    ran = AU.run_pending(app)
    assert any(x["kind"] == "person_lookup" and x["status"] == "done" for x in ran)
    item = next(i for i in inbox(api, "contact") if i["opportunityId"] == oid)
    assert item["data"]["name"] == "Kate Morrison" and "leadership" in item["data"]["url"]
    # the research agent chose its own steps, and every step is on record
    run = next(r for r in api.get("/api/agent-runs").json() if r["agent"] == "Research agent" and r["steps"])
    trace = api.get(f"/api/agent-runs/{run['id']}").json()["trace"]
    assert [t.get("tool") for t in trace if t["type"] == "tool"] == ["company_pages", "read_page"] and trace[-1]["accepted"] is True
    assert run["calls"] == 3 and run["prompt"] == "research-person-agent-1.0"
    api.post(f"/api/inbox/{item['id']}", headers=ME, json={"action": "apply"})
    # the CEO is now known, so the same note does not send the research agent again
    r = api.post(f"/api/opportunities/{oid}/notes", headers=ME, json={"text": sim["notes"]}).json()
    assert r["handoffs"] == []


def test_brakes_budget_pause_and_handoff_cap(live):
    ai = SchemaAI()
    app, api = live(ai)
    oid = lead(api, "Ardent Castings", ai)
    api.post("/api/demo/advance-days?days=8", headers=ME)
    api.patch("/api/agents", headers=ME, json={"paused": True})
    api.post("/api/agents/patrol", headers=ME)
    assert AU.run_pending(app) == []  # paused: nothing runs
    api.patch("/api/agents", headers=ME, json={"paused": False, "ai_budget": 0})
    ran = AU.run_pending(app)
    assert ran and all(r["status"] == "queued" for r in ran)  # AI work waits for tomorrow's budget
    assert "budget" in api.get("/api/agents/jobs", params={"status": "queued"}).json()[0]["result"]
    status = api.get("/api/agents").json()
    assert status["aiBudget"] == 0 and status["queued"] >= 1 and any(a["agent"] == "Research agent" for a in status["agents"])
    from api import jobs as J
    made = 0
    with app.state.db.session() as s:
        today = AU.agent_ctx(app, s).today
        for i in range(5):
            j = J.enqueue(s, today, "person_lookup", oid, "handoff", f"r{i}", {"role": f"R{i}"}, requested_by="test")
            if j:
                made += 1
                J.finish(j, "done", "test")
        deep = J.enqueue(s, today, "reconnect_draft", oid, "handoff", "too deep", requested_by="test",
                         parent=type("P", (), {"depth": J.MAX_DEPTH, "id": "x"})())
    assert made == J.HANDOFF_CAP and deep is None

def test_returned_pack_and_ready_are_picked_up_without_a_click(tmp_path):
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "d.sqlite3"))
    api = TestClient(app)
    opp = next(o for o in api.get("/api/opportunities").json() if o["stage"] == "approval")
    pack = next(p for p in api.get(f"/api/opportunities/{opp['id']}").json()["packs"] if p["status"] == "open")
    api.post(f"/api/packs/{pack['id']}/decision", headers=ME, json={"decision": "return", "role": "Director of Operations",
                                                                    "questions": "Who signs off above $250K?"})
    ran = AU.run_pending(app)
    assert any(r["kind"] == "gap_actions" and r["trigger"] == "event" and r["status"] == "done" for r in ran), ran
    assert inbox(api, "actions")


def test_inbox_needs_a_person_and_cannot_be_applied_twice(live):
    ai = SchemaAI()
    app, api = live(ai)
    lead(api, "Ardent Castings", ai)
    api.post("/api/demo/advance-days?days=8", headers=ME)
    api.post("/api/agents/patrol?run=true", headers=ME)
    item = inbox(api, "draft")[0]
    assert api.post(f"/api/inbox/{item['id']}", json={"action": "dismiss"}).status_code == 401
    assert api.post(f"/api/inbox/{item['id']}", headers=ME, json={"action": "dismiss"}).json()["status"] == "dismissed"
    assert api.post(f"/api/inbox/{item['id']}", headers=ME, json={"action": "apply"}).status_code == 409


def test_review_date_and_demo_lookup_rules():
    from api.demo_agents import people_to_find
    today = date(2026, 10, 26)
    sims = MockSearch().simulations
    assert AU.review_date(sims["kesslermotion.example"]["notes"], today) == date(2027, 4, 1)
    assert [x["role"] for x in people_to_find(sims["pioneervalve.example"]["notes"])] == ["CFO"]
    assert people_to_find(sims["ardentcastings.example"]["notes"]) == []  # the CEO delegated: nobody to look up


def test_live_agents_check_runs_the_whole_story(tmp_path, monkeypatch):
    import api.check_agents as CA
    monkeypatch.setattr(CA, "ROOT", tmp_path)
    ai = SchemaAI(evidence=orchard_evidence)
    labels = iter(["defer", "need"])
    real = ai.__call__

    def answer(url, headers, payload, **kw):
        if "label" in payload.get("generationConfig", {}).get("responseJsonSchema", {}).get("properties", {}):
            ai.label = next(labels)
        return real(url, headers, payload, **kw)
    code = CA.main(prepare=lambda app: setattr(app.state, "provider_factory", lambda: make_provider("gemini", "m", key="k", transport=answer)))
    out = "\n".join(CA.LINES)
    assert code == 0, out
    assert "asked for help finding: CEO" in out and "Kate Morrison" in out and "Reconnect draft" in out and "Follow-up draft" in out


def test_background_worker_patrols_and_works_on_its_own(tmp_path):
    import time
    import api.main
    assert api.main.app.state.worker is None  # importing the server never starts agents; only running it does
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "w.sqlite3"))
    api = TestClient(app)
    api.post("/api/demo/advance-days?days=7", headers=ME)  # time moved, so today's patrol is due whatever the hour
    w = AU.Worker(app, tick=0.02, pause=0).start()
    try:
        for _ in range(300):
            board = api.get("/api/agents/jobs").json()
            if board and not [j for j in board if j["status"] in ("queued", "running")]:
                break
            time.sleep(0.05)
    finally:
        w.stop.set()
        w.thread.join(2)
    assert board and all(j["status"] in ("done", "skipped") for j in board), board
    assert api.get("/api/agents").json()["lastPatrol"] and api.get("/api/inbox").json()


def _job(app, kind, opp_id):
    from api import jobs as J
    with app.state.db.session() as s:
        return J.enqueue(s, AU.agent_ctx(app, s).today, kind, opp_id, "patrol", "test", requested_by="test").id


def test_ai_budget_wait_does_not_block_work_without_ai(live):
    app, api = live(SchemaAI())
    api.patch("/api/agents", headers=ME, json={"ai_budget": 0})
    waiting = _job(app, "followup_draft", "bluegate")       # needs AI, queued first
    rules_only = _job(app, "defer_nurture", "meridian")     # needs no AI
    assert AU.next_job(app) == rules_only
    assert AU.run_job(app, rules_only) == "done"
    assert AU.next_job(app) is None and AU.queued(app) == [waiting]


def test_temporary_ai_error_waits_before_retrying(live):
    import httpx

    def down(*a, **kw):
        raise httpx.ConnectError("offline")
    app, api = live(down)
    jid = _job(app, "gap_actions", "meridian")
    assert AU.run_job(app, jid) == "queued"
    job = api.get("/api/agents/jobs").json()[0]
    assert job["attempts"] == 1 and "will retry" in job["result"]
    assert AU.queued(app) == [] and AU.next_job(app) is None   # waiting out the retry delay
    assert AU.run_job(app, jid) == "queued" and api.get("/api/agents/jobs").json()[0]["attempts"] == 1
    assert api.get("/api/agents").json()["queued"] == 1


def test_two_workers_never_run_the_same_job(tmp_path, monkeypatch):
    import threading
    from api import jobs as J
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "c.sqlite3"))
    jid = _job(app, "ready_reminder", "meridian")
    both_read = threading.Barrier(2, timeout=5)
    real_due = J.due
    monkeypatch.setattr(J, "due", lambda r, now=None: (both_read.wait(), real_due(r, now))[1])  # both have seen "queued"
    ran = []
    monkeypatch.setitem(AU.HANDLERS, "ready_reminder", lambda c, job, o: (ran.append(job.id), ("done", "ok"))[1])
    results = []
    threads = [threading.Thread(target=lambda: results.append(AU.run_job(app, jid))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert ran == [jid]  # the handler ran once; the second worker saw the job already taken
    assert len(results) == 2 and "done" in results and set(results) <= {"done", "running"}


def test_ai_budget_is_read_from_the_env_file(tmp_path, monkeypatch):
    from api.settings import load_settings
    monkeypatch.setenv("AGENT_AI_DAILY_BUDGET", "0")
    monkeypatch.delenv("AGENT_AI_DAILY_BUDGET")  # restored to unset after the test
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)  # this test reads its own .env file (the offline guard skips the real one)
    env = tmp_path / ".env"
    env.write_text("AGENT_AI_DAILY_BUDGET=7\n", encoding="utf-8")
    load_settings(env)  # api.jobs was imported long before this, as it is when the server starts
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "b.sqlite3"))
    assert TestClient(app).get("/api/agents").json()["aiBudget"] == 7


class FlakyBridge:
    """A research service where one run cannot be checked, one returns a broken card and one finishes normally."""
    dated, prompt, label = False, "test", "test"

    def __init__(self):
        self.runs = {}

    def start(self, company, domain, consultant, **kw):
        run_id = f"run-{company.split()[0].lower()}"
        self.runs[run_id] = company
        return run_id

    def status(self, run_id):
        if "down" in run_id:
            raise ConnectionError("research service offline")
        return {"status": "completed", "usage": {}, "events": ["done"]}

    def result(self, run_id):
        if "broken" in run_id:
            return {"research": {}}  # missing every field
        from api.mock_research import research
        pages = [{"url": "https://ok.example/news", "title": "News", "published": "2026-10-01",
                  "content": "Okay Foundry opened a second plant in Ohio. It plans more automation."}]
        provider = make_provider("gemini", "m", key="k", transport=SchemaAI())
        return research(provider, pages, "Okay Foundry", "ok.example")[0]


def test_research_sync_survives_a_bad_run_and_runs_in_the_background(tmp_path):
    db = tmp_path / "s.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    app = create_app(Settings(app_mode="live", db_path=db))
    bridge = FlakyBridge()
    app.state.research_bridge = bridge
    api = TestClient(app)
    ids = {name: api.post("/api/opportunities", headers=ME, json={"company": f"{name} Foundry", "domain": f"{name.lower()}.example"}).json()["id"]
           for name in ("Down", "Broken", "Okay")}
    api.patch("/api/agents", headers=ME, json={"paused": True})  # agents paused: collecting research still happens
    w = AU.Worker(app, tick=0, pause=0)
    w.step()  # what the background worker does every few seconds: no button press needed
    stage = {n: api.get(f"/api/opportunities/{i}").json() for n, i in ids.items()}
    assert stage["Okay"]["stage"] == "review"
    assert stage["Broken"]["research"]["status"] == "failed" and "could not be read" in stage["Broken"]["research"]["error"]
    assert stage["Down"]["research"]["status"] == "running"  # checked again next time


def test_budget_counts_every_ai_request(live):
    ai = SchemaAI()
    app, api = live(ai)
    api.post("/api/candidates/recommend", headers=ME, json={})  # the discovery agent: direction, one search, finish
    run = api.get("/api/agent-runs").json()[0]
    assert ai.calls == ["discovery"] * 3
    assert run["agent"] == "Discovery agent" and run["calls"] == 3  # every model turn counts
