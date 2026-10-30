"""Multi-agent step 7: the manager as orchestrator. Its triage looks at one opportunity when something happens, keeps a
memory of it (summary and promises), decides on the other agents' requests, plans next actions and writes the owner one
message. A fake AI plays every agent, so these run offline."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api import autonomy as AU
from api import memory as MEM
from api import models as m
from api.llm import LLMError, make_provider
from api.main import create_app
from api.settings import Settings
from agent_fakes import default_triage, play, tool_names

ME = {"X-User": "Alex Morgan"}
CFO = "Emma said the CFO would decide on anything above $300K."
PROMISE = "Emma will send the 2027 budget plan within ten days."
NOTES = f"Call with Emma Chen (VP Finance) about the planning review. {CFO} {PROMISE} Agreed next step: a workshop with the plant leads."


def asks_for_cfo(text):
    return {"proposals": [], "people_to_find": [{"role": "CFO", "quote": CFO}]} if CFO in text else {"proposals": [], "people_to_find": []}


def remembering(case):
    """A manager that writes the summary, keeps Emma's promise (and tries an invented one), answers requests, plans and writes.
    Other opportunities (stalled ones the patrol finds) get the default careful triage."""
    if case["opportunity"]["id"] != "meridian":
        return default_triage(case)
    today = date.fromisoformat(case["today"])
    note = case["notes"][0]
    calls = [("remember", {"summary": "Meridian: Emma Chen (VP Finance) leads the review; the CFO decides above $300K. A workshop is agreed."}),
             ("add_commitment", {"who": "Emma Chen", "side": "client", "what": "send the 2027 budget plan",
                                 "due": (today + timedelta(days=10)).isoformat(), "quote": PROMISE, "note_id": note["id"]}),
             ("add_commitment", {"who": "Emma Chen", "side": "client", "what": "sign the contract",
                                 "quote": "Emma promised to sign the contract.", "note_id": note["id"]})]  # not in the notes: refused
    calls += [("answer_request", {"request_id": q["request_id"], "decision": "accept", "reason": "The CFO decides, and no CFO is a contact yet."})
              for q in case["requests"]]
    gap = next(g for g in case["open_gaps"] if g["blocks_readiness"])
    calls += [("suggest_action", {"gap_key": gap["gap_key"], "title": "Confirm the CFO's approval path", "reason": gap["label"],
                                  "question": "How does the CFO review spend above $300K?"}),
              ("message_owner", {"text": "Emma Chen promised the 2027 budget plan within ten days; I will check it arrives."})]
    return calls


class AI:
    """Plays every agent. triage(case) decides the manager's triage; fail=True makes the triage's AI requests fail."""

    def __init__(self, triage=remembering, evidence=asks_for_cfo, fail=False):
        self.triage, self.evidence, self.fail, self.seen = triage, evidence, fail, []

    def __call__(self, url, headers, payload, **kw):
        names = tool_names(payload)
        self.seen.append("triage" if "case" in names else "next_step" if "suggest" in names else "other")
        if self.fail and "case" in names:
            raise LLMError("The AI service is overloaded.", "temporary")
        return play(payload, evidence=self.evidence, triage=self.triage, manager=lambda board: [])


@pytest.fixture
def make(tmp_path):
    def build(ai, mode="on"):
        db = tmp_path / f"t-{mode}.sqlite3"
        create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
        app = create_app(Settings(app_mode="live", db_path=db))
        app.state.provider_factory = lambda: make_provider("gemini", "m", key="k", transport=ai)
        api = TestClient(app)
        assert api.patch("/api/agents", headers=ME, json={"manager_mode": mode}).json()["managerMode"] == mode
        return app, api
    return build


def board(api, opp="meridian"):
    return api.get("/api/agents/jobs", params={"opportunity": opp, "limit": 200}).json()


def messages(api, opp="meridian"):
    return [i for i in api.get("/api/inbox?status=all").json() if i["opportunityId"] == opp and i["kind"] in ("actions", "advice")]


def test_on_mode_a_new_note_builds_memory_and_sends_one_message(make):
    ai = AI(evidence=lambda t: {"proposals": [], "people_to_find": []})
    app, api = make(ai)
    assert api.post("/api/opportunities/meridian/notes", headers=ME, json={"text": NOTES}).status_code == 200
    triage = next(j for j in board(api) if j["kind"] == "manager_triage")
    assert triage["status"] == "queued" and triage["trigger"] == "event"
    ran = AU.run_pending(app)
    assert next(r for r in ran if r["kind"] == "manager_triage")["status"] == "done"
    mem = api.get("/api/opportunities/meridian/memory").json()
    assert "CFO decides above $300K" in mem["summary"]
    assert [(c["who"], c["quote"], c["status"]) for c in mem["commitments"]] == [("Emma Chen", PROMISE, "open")]  # the invented one was refused
    msgs = messages(api)
    assert len(msgs) == 1 and msgs[0]["agent"] == "Manager agent" and msgs[0]["kind"] == "actions"
    assert msgs[0]["body"].startswith("Emma Chen promised") and "- Confirm the CFO's approval path" in msgs[0]["body"]
    assert "next_step" not in ai.seen  # the manager planned the next actions itself
    decisions = api.get("/api/manager/decisions", params={"scope": "triage"}).json()
    assert {(d["kind"], d["outcome"]) for d in decisions} >= {("gap_actions", "suggested"), ("note", "sent")}
    run = next(r for r in api.get("/api/agent-runs").json() if r["agent"] == "Manager agent")
    assert run["prompt"] == "manager-triage-1.2"
    trace = api.get(f"/api/agent-runs/{run['id']}").json()["trace"]
    refused = [t for t in trace if t["type"] == "tool" and t.get("tool") == "add_commitment" and "word for word" in str(t.get("result", ""))]
    assert refused  # the model was told why, in the trace a person can read


def test_on_mode_handoffs_go_to_the_manager_which_accepts(make):
    app, api = make(AI())
    r = api.post("/api/opportunities/meridian/notes", headers=ME, json={"text": NOTES}).json()
    assert r["handoffs"] == ["CFO"]
    jobs = board(api)
    assert not [j for j in jobs if j["kind"] == "person_lookup"]  # not queued directly: the manager decides first
    triage = next(j for j in jobs if j["kind"] == "manager_triage")
    assert "Evidence agent asks the research agent" in triage["reason"]
    AU.run_pending(app)
    lookup = next(j for j in board(api) if j["kind"] == "person_lookup")
    assert lookup["trigger"] == "handoff" and lookup["depth"] == 1 and lookup["requestedBy"] == "Evidence agent via Manager agent"
    assert lookup["status"] in ("done", "skipped")
    d = next(d for d in api.get("/api/manager/decisions", params={"scope": "triage"}).json() if d["kind"] == "person_lookup")
    assert d["outcome"] == "accepted" and "Request from Evidence agent" in d["detail"]


def test_on_mode_the_manager_can_decline_a_request(make):
    def declining(case):
        return [("remember", {"summary": "Meridian: Emma Chen (VP Finance) leads; the CFO is already engaged through her."}),
                *[("answer_request", {"request_id": q["request_id"], "decision": "decline",
                                      "reason": "Emma is bringing the CFO to the workshop herself."}) for q in case["requests"]]]
    app, api = make(AI(triage=declining))
    api.post("/api/opportunities/meridian/notes", headers=ME, json={"text": NOTES})
    AU.run_pending(app)
    assert not [j for j in board(api) if j["kind"] == "person_lookup"]
    d = next(d for d in api.get("/api/manager/decisions", params={"scope": "triage"}).json() if d["kind"] == "person_lookup")
    assert d["outcome"] == "declined"
    activity = api.get("/api/opportunities/meridian").json()["activity"]
    assert any("Manager agent declined Evidence agent's request" in a["text"] for a in activity)
    assert not messages(api)  # nothing new for the owner, so no message


def test_a_triage_that_fails_loses_nothing(make):
    app, api = make(AI(fail=True))
    api.post("/api/opportunities/meridian/notes", headers=ME, json={"text": NOTES})
    ran = AU.run_pending(app)
    t = next(r for r in ran if r["kind"] == "manager_triage")
    assert t["status"] == "done" and "Carried out without the manager: person_lookup" in t["result"]
    lookup = next(j for j in board(api) if j["kind"] == "person_lookup")
    assert lookup["requestedBy"] == "Evidence agent"  # carried out as the evidence agent asked
    failed = next(r for r in api.get("/api/agent-runs").json() if r["agent"] == "Manager agent")
    assert failed["status"] == "failed"  # the failure stays visible


def test_a_triage_that_stops_early_passes_next_actions_to_the_next_step_agent(make):
    ai = AI(triage=lambda case: [("remember", {"summary": "Northstar: the approver returned the pack with a question."})])
    app, api = make(ai)
    pack = next(p for p in api.get("/api/opportunities/northstar").json()["packs"] if p["status"] == "open")
    api.post(f"/api/packs/{pack['id']}/decision", headers=ME, json={"decision": "return", "role": "Director of Operations",
                                                                    "questions": "Who signs off above $250K?"})
    assert [j["kind"] for j in board(api, "northstar") if j["status"] == "queued"] == ["manager_triage"]
    ran = AU.run_pending(app)
    t = next(r for r in ran if r["kind"] == "manager_triage")
    assert "Carried out without the manager: gap_actions" in t["result"]  # finish was refused: the approver was not answered
    assert any(r["kind"] == "gap_actions" and r["opportunityId"] == "northstar" and r["status"] == "done" for r in ran)


def test_on_mode_a_returned_pack_gets_next_actions_that_answer_the_approver_first(make):
    ai = AI(triage=default_triage)
    app, api = make(ai)
    pack = next(p for p in api.get("/api/opportunities/northstar").json()["packs"] if p["status"] == "open")
    api.post(f"/api/packs/{pack['id']}/decision", headers=ME, json={"decision": "return", "role": "Director of Operations",
                                                                    "questions": "Who signs off above $250K?"})
    AU.run_pending(app)
    assert "next_step" not in ai.seen and not [j for j in board(api, "northstar") if j["kind"] == "gap_actions"]
    msgs = messages(api, "northstar")
    assert len(msgs) == 1 and msgs[0]["data"]["suggestions"][0]["gap_key"] == "approver"


def test_a_promise_past_its_date_sends_the_opportunity_back_to_the_manager(make):
    app, api = make(AI(evidence=lambda t: {"proposals": [], "people_to_find": []}))
    api.post("/api/opportunities/meridian/notes", headers=ME, json={"text": NOTES})
    AU.run_pending(app)  # the triage keeps Emma's promise, due in ten days
    api.post("/api/demo/advance-days?days=11", headers=ME)
    queued = api.post("/api/agents/patrol", headers=ME).json()["queued"]
    due = [j for j in queued if j["kind"] == "manager_triage" and j["opportunityId"] == "meridian"]
    assert due and "Emma Chen promised to send the 2027 budget plan" in due[0]["reason"] and due[0]["trigger"] == "patrol"
    AU.run_pending(app)
    api.post("/api/demo/advance-days?days=1", headers=ME)
    again = [j for j in api.post("/api/agents/patrol", headers=ME).json()["queued"] if j["kind"] == "manager_triage" and j["opportunityId"] == "meridian"]
    assert not again  # at most once a week for the same promise
    # a person closes it; it no longer sends the opportunity back
    r = api.post("/api/opportunities/meridian/commitments/c1", headers=ME, json={"status": "done", "why": "The budget plan arrived by email."})
    assert r.status_code == 200 and r.json()["commitments"][0]["status"] == "done"
    assert api.post("/api/opportunities/meridian/commitments/c1", headers=ME, json={"status": "done", "why": "Again, twice."}).status_code == 400
    with app.state.db.session() as s:
        assert MEM.overdue(s, date.today() + timedelta(days=60)) == []


def test_shadow_mode_records_and_remembers_but_changes_nothing(make):
    app, api = make(AI(), mode="shadow")
    api.post("/api/opportunities/meridian/notes", headers=ME, json={"text": NOTES})
    jobs = board(api)
    lookup = next(j for j in jobs if j["kind"] == "person_lookup")
    assert lookup["requestedBy"] == "Evidence agent"  # handoffs are queued directly, as before
    assert any(j["kind"] == "manager_triage" for j in jobs)
    AU.run_pending(app)
    assert not messages(api)  # nothing reached the owner
    assert api.get("/api/opportunities/meridian/memory").json()["summary"]  # the manager's own notes are kept
    decisions = api.get("/api/manager/decisions", params={"scope": "triage"}).json()
    assert decisions and all(d["outcome"] in ("recorded", "refused", "declined") for d in decisions)
    assert api.get("/api/manager/compare").json()["day"] is None  # triage decisions are not part of the daily comparison


def test_off_mode_and_demo_mode_have_no_triage(make, tmp_path):
    app, api = make(AI(), mode="off")
    api.post("/api/opportunities/meridian/notes", headers=ME, json={"text": NOTES})
    assert not [j for j in board(api) if j["kind"] == "manager_triage"]
    assert next(j for j in board(api) if j["kind"] == "person_lookup")["requestedBy"] == "Evidence agent"
    demo = TestClient(create_app(Settings(app_mode="demo", db_path=tmp_path / "demo.sqlite3")))
    demo.patch("/api/agents", headers=ME, json={"manager_mode": "on"})
    demo.post("/api/opportunities/meridian/notes", headers=ME, json={"text": NOTES})
    assert not [j for j in board(demo) if j["kind"] == "manager_triage"]


def test_memory_checks(make):
    app, _ = make(AI(), mode="off")
    with app.state.db.session() as s:
        today = AU.agent_ctx(app, s).today
        notes = {"n1": {"text": NOTES, "on": today}}
        ok = {"who": "Emma Chen", "side": "client", "what": "send the budget plan", "quote": PROMISE, "note_id": "n1"}
        item = MEM.add_commitment(s, "meridian", {**ok, "due": (today + timedelta(days=10)).isoformat()}, notes, today)
        assert item["id"] == "c1" and item["status"] == "open"
        for bad, why in (({"quote": "Emma will sign."}, "word for word"), ({"note_id": "n9"}, "Unknown note"),
                         ({"due": (today - timedelta(days=1)).isoformat()}, "before the note"), ({"due": "next week"}, "YYYY-MM-DD"),
                         ({"side": "partner"}, "side is client"), ({}, "already in memory")):
            with pytest.raises(MEM.Invalid, match=why):
                MEM.add_commitment(s, "meridian", {**ok, **bad}, notes, today)
        with pytest.raises(MEM.Invalid, match="characters"):
            MEM.set_summary(s, "meridian", "Too short.", today)
        with pytest.raises(MEM.Invalid, match="done .* or dropped"):
            MEM.close_commitment(s, "meridian", "c1", "finished", "It arrived.", today, "Alex Morgan")
        assert MEM.overdue(s, today + timedelta(days=11))[0][1]["id"] == "c1"
        assert MEM.view(MEM.get(s, "meridian"), today + timedelta(days=11))["commitments"][0]["daysOverdue"] == 1
        assert s.scalar(select(m.OpportunityMemory.summary).where(m.OpportunityMemory.opportunity_id == "meridian")) == ""


def test_orchestrator_check_runs_the_whole_story_offline(tmp_path, monkeypatch):
    """The live check (Check Orchestrator.cmd) with a fake AI: every step it checks must pass."""
    import api.check_orchestrator as CO
    from test_autonomy import SchemaAI, orchard_evidence

    def careful(case):  # the default careful triage, plus Daniel's promise with its due date
        calls = default_triage(case)
        for n in case["notes"]:
            line = next((x.strip() for x in n["text"].split("\n") if "within two weeks" in x), None)
            if line:
                due = (date.fromisoformat(n["on"]) + timedelta(days=14)).isoformat()
                calls.insert(1, ("add_commitment", {"who": "Daniel Ruiz", "side": "client", "what": "talk to the CEO and come back",
                                                    "due": due, "quote": line, "note_id": n["id"]}))
        return calls

    ai = SchemaAI(label="meet", evidence=orchard_evidence)
    plain = ai.__call__

    def answer(url, headers, payload, **kw):
        if "tools" in payload and "case" in tool_names(payload):
            return play(payload, triage=careful)
        return plain(url, headers, payload, **kw)

    monkeypatch.setattr(CO, "ROOT", tmp_path)
    code = CO.main(prepare=lambda app: setattr(app.state, "provider_factory", lambda: make_provider("gemini", "m", key="k", transport=answer)))
    out = "\n".join(CO.LINES)
    assert code == 0, out
    assert "FAIL" not in out and "RESULT: the manager runs the agents as intended" in out
    assert (tmp_path / "runtime" / "orchestrator-check-result.txt").exists()


@pytest.mark.parametrize("mode", ["on", "off"])
def test_an_agent_that_cannot_deliver_reports_to_the_manager_when_it_is_on(make, mode):
    """The manager accepted the request, so the result comes back to it (one voice); with the manager off the owner is told."""
    from api import jobs as J
    app, api = make(AI(), mode=mode)
    with app.state.db.session() as s:
        job = J.enqueue(s, AU.agent_ctx(app, s).today, "person_lookup", "meridian", "handoff", "Find the CFO",
                        {"role": "CFO", "quote": CFO}, requested_by="Evidence agent")
        s.commit()
        jid = job.id
    assert AU.run_job(app, jid) == "done"
    result = next(j for j in board(api) if j["id"] == jid)["result"]
    reminders = [i for i in api.get("/api/inbox?status=all").json() if i["kind"] == "reminder" and i["opportunityId"] == "meridian"]
    triage = [j for j in board(api) if j["kind"] == "manager_triage"]
    if mode == "on":
        assert result.endswith("told the manager.") and not reminders
        assert triage and "Could not find the CFO" in triage[0]["reason"]
    else:
        assert result.endswith("told the owner.") and reminders and not triage
