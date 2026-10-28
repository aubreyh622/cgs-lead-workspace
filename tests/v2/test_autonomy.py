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

ME = {"X-User": "Alex Morgan"}


class SchemaAI:
    """Answers each agent by the shape of answer it asks for. evidence(notes) returns the evidence agent's answer."""

    def __init__(self, label="meet", evidence=None):
        self.label, self.evidence, self.calls = label, evidence or (lambda n: {"proposals": [], "people_to_find": []}), []

    def __call__(self, url, headers, payload, **kw):
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
        if "label" in payload["generationConfig"]["responseJsonSchema"]["properties"]:
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
