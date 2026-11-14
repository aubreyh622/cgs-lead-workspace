"""Backend additions behind the redesigned page (docs/REDESIGN_PLAN.md D6 B-a..B-e and gaps G5, G7, G12, G16, G27):
record ids on to-do rows, research steps, directors in /api/config, the 140-character exception, brief versions, skipping a
round, bringing a rejected company back, correcting a reply label, and what was settled this week."""
from datetime import datetime, timezone

from api import agent_runtime as R
from api import models as m
from api.research_agent import STEPS, _track_steps, step_view
from tests.v2.test_actions import ME, accept_body, api, get, post  # noqa: F401  (api is a fixture)

SARAH = {"X-User": "Sarah Patel"}


def rows(api, who=ME):
    t = api.get("/api/todo", headers=who).json()
    return [it for g in t["groups"] for it in g["items"]]


# ---------- B-a, G27: record ids, owner and date on to-do rows ----------
def test_todo_rows_carry_since_owner_and_record_ids(api):
    for who in (ME, SARAH):
        for r in rows(api, who):
            assert set(r) >= {"since", "owner", "ref"}
            if r["since"]:
                assert len(r["since"]) == 10 and r["since"][4] == "-"
            if r["opportunity"]:
                assert r["owner"] is not None or r["label"] in ("Accept or reject", "Research stopped")
    packs = [r for who in (ME, SARAH) for r in rows(api, who) if r["label"] == "Approve or return"]
    assert packs, "the demo has a pack waiting for approval"
    for r in packs:
        assert r["ref"]["type"] == "pack" and r["ref"]["opportunity"] == r["opportunity"]["id"] and r["since"]
        assert any(p["id"] == r["ref"]["id"] for p in get(api, r["opportunity"]["id"])["packs"])
    for r in [r for who in (ME, SARAH) for r in rows(api, who) if r["key"].startswith(("proposal:", "lesson:"))]:
        assert r["ref"]["id"] == r["key"].split(":", 1)[1]


# ---------- B-b: research steps ----------
def test_research_steps_only_move_forward():
    record = {}
    calls = []
    tool = lambda name: R.Tool(name, "", {}, lambda _args, n=name: calls.append(n) or "ok")
    spec = R.AgentSpec("Research agent", "v", "", [tool("company_pages"), tool("read_page"), tool("add_fact"), tool("search_web")], tool("finish"))
    _track_steps(spec, record)
    assert record["step"] == 1
    by = {t.name: t for t in spec.tools}
    by["read_page"].run({})
    assert record["step"] == 2
    by["add_fact"].run({})
    by["search_web"].run({})  # searching again while recording facts does not go back
    assert record["step"] == 3
    spec.finish.run({})
    assert record["step"] == 4 and calls == ["read_page", "add_fact", "search_web", "finish"]
    assert step_view(2) == {"n": 2, "of": len(STEPS), "label": STEPS[1]} and step_view(9)["n"] == 4 and step_view(None) is None


def test_a_running_research_task_shows_its_step_and_start(api):
    app = api.app
    with app.state.db.session() as s:
        t = m.ResearchTask(id="rt-step", opportunity_id="ostrander", status="running", progress=2,
                           started_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
        s.add(t)
        s.commit()
    r = get(api, "ostrander")["research"]
    assert r["status"] == "running" and r["step"]["n"] == 2 and r["step"]["of"] == 4 and r["startedAt"].startswith("2030-01-01")


# ---------- B-c, B-d ----------
def test_config_names_the_directors_and_the_exception_limit(api):
    c = api.get("/api/config").json()
    assert c["directors"] == [] and c["exceptionMax"] == 140


def test_the_exception_reason_is_limited_to_140_characters(api):
    r = post(api, "/api/opportunities/meridian/packs", {"exception": "x" * 141}, ok=400)
    assert r["code"] == "too_long"
    assert get(api, "meridian")["stage"] == "active"


# ---------- B-e: brief versions ----------
def test_a_returned_brief_keeps_its_earlier_version_and_says_what_changed(api):
    before = get(api, "ostrander")["brief"]
    assert before["versions"] == [] and before["changes"] is None
    post(api, "/api/opportunities/ostrander/step4", {"decision": "return", "questions": "Who now leads the automation program?", "scope": "opportunity"})
    b = get(api, "ostrander")["brief"]
    assert [v["n"] for v in b["versions"]] == [1] and b["versions"][0]["question"] == "Who now leads the automation program?"
    ch = b["changes"]
    assert ch["since"] == 1 and ch["by"] == "Alex Morgan" and len(ch["factsAdded"]) == 1 and ch["factsDropped"] == []
    assert ch["recommendation"]["now"] == "accept" and ch["flagsNow"] == 0 and ch["flagsBefore"] >= 1


# ---------- G7: skip a round ----------
def test_skip_a_whole_round(api):
    cands = api.get("/api/candidates", headers=ME).json()
    cands = cands if isinstance(cands, list) else cands.get("candidates", [])
    rnd = next(c["round"] for c in cands if c["status"] == "new")
    waiting = [c for c in cands if c["round"] == rnd and c["status"] == "new"]
    assert post(api, f"/api/candidates/rounds/{rnd}/skip", {"reason": "Too small", "scope": "theme"})["skipped"] == len(waiting)
    after = api.get("/api/candidates", headers=ME).json()
    after = after if isinstance(after, list) else after.get("candidates", [])
    assert all(c["status"] == "skipped" for c in after if c["round"] == rnd and c["id"] in {w["id"] for w in waiting})
    assert post(api, f"/api/candidates/rounds/{rnd}/skip", {"reason": "Too small"}, ok=409)["code"] == "nothing_to_skip"
    assert post(api, f"/api/candidates/rounds/{rnd}/skip", {"reason": "Not a reason"}, ok=400)


# ---------- G12: bring a rejected company back ----------
def test_bring_a_rejected_company_back(api):
    post(api, "/api/opportunities/pellham/step4", {"decision": "reject", "reason": "Outside our target client profile", "scope": "theme"})
    assert get(api, "pellham")["stage"] == "rejected"
    assert post(api, "/api/opportunities/pellham/reopen", {"reason": " "}, ok=400)
    post(api, "/api/opportunities/pellham/reopen", {"reason": "They raised new funding"})
    o = get(api, "pellham")
    assert o["stage"] == "review" and o["decisions"][-1]["decision"] == "Brought back"
    with api.app.state.db.session() as s:  # the rejection no longer teaches the learning loop
        fb = s.query(m.Feedback).filter_by(opportunity_id="pellham", outcome="Rejected").all()
        assert fb and all(f.withdrawn_by == "Alex Morgan" for f in fb)
    assert post(api, "/api/opportunities/pellham/reopen", {"reason": "again"}, ok=409)
    post(api, "/api/opportunities/pellham/step4", accept_body())  # the decision is open again
    assert get(api, "pellham")["stage"] == "identified"


# ---------- G16: correct the reply label ----------
def test_a_person_corrects_the_reply_label(api):
    post(api, "/api/opportunities/bluegate/outreach/reply", {"text": "Thanks. Could we talk on Wednesday?"})
    assert post(api, "/api/opportunities/bluegate/reply-label", {"label": "maybe"}, ok=422)
    post(api, "/api/opportunities/bluegate/reply-label", {"label": "need", "note": "They described the problem"})
    o = get(api, "bluegate")
    assert o["replyLabel"] == "need" and o["stage"] == "active"
    assert any("Reply label changed by Alex Morgan" in a["text"] for a in o["activity"])
    assert post(api, "/api/opportunities/ostrander/reply-label", {"label": "need"}, ok=409)  # not in a stage with a reply


# ---------- G5: settled this week ----------
def test_settled_lists_this_weeks_decisions_and_replies(api):
    post(api, "/api/opportunities/pellham/step4", {"decision": "reject", "reason": "Outside our target client profile", "scope": "theme"})
    post(api, "/api/opportunities/bluegate/outreach/reply", {"text": "Thanks. Could we talk on Wednesday?"})
    r = api.get("/api/settled", headers=ME).json()
    kinds = {(x["kind"], x["opportunity"]["id"]) for x in r["items"]}
    assert ("Rejected", "pellham") in kinds and ("Client replied", "bluegate") in kinds
    assert [x["on"] for x in r["items"]] == sorted((x["on"] for x in r["items"]), reverse=True)
    assert all(x["on"] >= r["since"] for x in r["items"])
    assert api.get("/api/settled", headers=ME, params={"days": 0}).status_code == 400
