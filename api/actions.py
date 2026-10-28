"""Write endpoints: every human action in Steps 1-8 and the learning loop (backend step 4).

No login yet: each request names the person acting in the X-User header, chosen from the
workspace users (GET /api/users). Every change records that name.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from api import models as m
from api import services as S

router = APIRouter(prefix="/api")
Scope = Literal["opportunity", "theme", "consultant", "cgs"]


def simulated(settings) -> bool:
    """Time travel works on demo data, and in live mode on the synthetic internet; never against real companies."""
    if settings.app_mode == "demo":
        return True
    from api.search import search_config
    return search_config()["provider"] == "mock"


def demo_today(s, settings) -> date:
    shift = s.get(m.Setting, "day_offset")
    days = timedelta(days=shift.value if shift else 0)
    if settings.app_mode != "demo":
        return date.today() + (days if simulated(settings) else timedelta(0))
    base = s.get(m.Setting, "demo_today")
    return date.fromisoformat(base.value) + days


def ctx(request: Request, x_user: str | None = Header(None, description="The person acting, from GET /api/users")):
    db = request.app.state.db
    session = db.sessions()
    c = S.Ctx(s=session, actor=x_user or "", today=demo_today(session, request.app.state.settings), settings=request.app.state.settings,
              provider_factory=getattr(request.app.state, "provider_factory", None),
              research_factory=lambda: research_bridge(request.app), search_factory=getattr(request.app.state, "search_factory", None))
    users = S.owners_list(c)
    if not x_user or x_user not in users:
        session.close()
        raise HTTPException(401, f"Say who is acting: send the X-User header with one of {users}.")
    try:
        yield c
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def research_bridge(app):
    """One research bridge per server, created when live research is first needed."""
    if getattr(app.state, "research_bridge", None) is None:
        from api.search import search_config
        if search_config()["provider"] == "mock":
            from api.llm import make_provider
            from api.mock_research import MockResearchBridge
            st = app.state.settings
            factory = getattr(app.state, "provider_factory", None) or (lambda: make_provider(st.ai_provider, st.ai_model))
            app.state.research_bridge = MockResearchBridge(factory)
        else:
            from api.research_bridge import ResearchBridge
            from api.settings import ROOT
            app.state.research_bridge = ResearchBridge(ROOT, app.state.settings.ai_model)
    return app.state.research_bridge


Actor = Depends(ctx)


@router.get("/users", tags=["system"], summary="Workspace users (choose one; there is no login yet)")
def users(request: Request):
    with request.app.state.db.session() as s:
        row = s.get(m.Setting, "owners")
        return row.value if row else []


# ---------- Step 1 ----------
class Skip(BaseModel):
    reason: str = Field(examples=["Too small"])
    scope: Scope = "theme"


class ManualLead(BaseModel):
    company: str
    domain: str = ""
    name: str = ""
    title: str = ""
    source: str = "Conference"
    notes: str = ""


class Recommend(BaseModel):
    intent: str = Field("", description="What to look for, in your own words. Empty = the research direction only.")


@router.get("/search/settings", tags=["step 1"], summary="Which search provider and queries discovery uses (config/search.json)")
def search_settings():
    from api.search import PROVIDERS, search_config
    return {**search_config(), "installed_providers": list(PROVIDERS)}


@router.post("/candidates/recommend", tags=["step 1"], summary="Recommend up to 10 new candidates (live: web search + AI, checked by code)")
def recommend(body: Recommend, c: S.Ctx = Actor):
    return S.recommend(c, body.intent)


@router.post("/research/sync", tags=["steps 2-3"], summary="Live mode: collect finished research and move briefs to Step 4")
def sync(c: S.Ctx = Actor):
    return {"updates": S.sync_research(c)}


class DomainIn(BaseModel):
    domain: str


@router.patch("/opportunities/{opp_id}/company", tags=["steps 2-3"], summary="Set the company website, which research needs")
def company(opp_id: str, body: DomainIn, c: S.Ctx = Actor):
    S.set_domain(c, opp_id, body.domain)
    return {"ok": True}


@router.post("/candidates/{cand_id}/pick", tags=["step 1"], summary="Pick a candidate for research (Steps 2-3 run on their own)")
def pick(cand_id: str, c: S.Ctx = Actor):
    return {"opportunities": S.pick_candidate(c, cand_id)}


@router.post("/candidates/{cand_id}/skip", tags=["step 1"], summary="Skip a candidate with a reason (feedback)")
def skip(cand_id: str, body: Skip, c: S.Ctx = Actor):
    S.skip_candidate(c, cand_id, body.reason, body.scope)
    return {"ok": True}


@router.post("/opportunities", tags=["step 1"], summary="Add one lead by hand; research starts at once")
def add(body: ManualLead, c: S.Ctx = Actor):
    return {"id": S.add_manual(c, body.company, body.domain, body.name, body.title, body.source, body.notes)}


@router.post("/opportunities/{opp_id}/research/{action}", tags=["steps 2-3"], summary="Stop, resume or retry research")
def research(opp_id: str, action: Literal["stop", "resume", "retry"], c: S.Ctx = Actor):
    S.research_control(c, opp_id, action)
    return {"ok": True}


# ---------- Step 4 ----------
class Step4(BaseModel):
    decision: Literal["accept", "return", "reject"]
    rating: int = 3
    owner: str = ""
    next_action: str = ""
    due: date | None = None
    duplicates_checked: bool = False
    accept_reason: str = "Relevant signal"
    questions: str = ""
    reason: str = ""
    comment: str = ""
    suggest_exclusion: bool = False
    scope: Scope = "theme"
    minutes: float | None = Field(None, description="Optional review time. Leave empty if not measured; it is never counted as zero.")
    revision: int | None = None


class ContactIn(BaseModel):
    name: str
    title: str = ""
    note: str = Field(description="Where the name and role were verified")


class Sibling(BaseModel):
    title: str
    team: str = ""
    name: str = ""
    role: str = ""


@router.post("/opportunities/{opp_id}/step4", tags=["step 4"], summary="Accept, return or reject a new opportunity")
def step4(opp_id: str, body: Step4, c: S.Ctx = Actor):
    S.step4(c, opp_id, body)
    return {"ok": True}


@router.post("/opportunities/{opp_id}/contact", tags=["step 4"], summary="Confirm the contact's name and role")
def contact(opp_id: str, body: ContactIn, c: S.Ctx = Actor):
    S.confirm_contact(c, opp_id, body.name, body.title, body.note)
    return {"ok": True}


@router.post("/opportunities/{opp_id}/siblings", tags=["step 4"], summary="Save another opportunity at the same company")
def sibling(opp_id: str, body: Sibling, c: S.Ctx = Actor):
    return {"id": S.save_sibling(c, opp_id, body.title, body.team, body.name, body.role)}


# ---------- Step 5 ----------
class Draft(BaseModel):
    subject: str
    body: str
    angle: str | None = None
    tone: Literal["standard", "brief"] = "standard"
    channel: str | None = None


class Sent(BaseModel):
    via: str = "Email"
    on: date | None = None


class Reply(BaseModel):
    text: str
    on: date | None = None


class Nurture(BaseModel):
    reason: str
    review_on: date | None = None


def follow_days(c: S.Ctx) -> int:
    row = c.s.get(m.Setting, "follow_days")
    return int(row.value) if row else 7


@router.put("/opportunities/{opp_id}/outreach/draft", tags=["step 5"], summary="Save the edited outreach draft")
def draft(opp_id: str, body: Draft, c: S.Ctx = Actor):
    S.save_draft(c, opp_id, body.subject, body.body, body.angle, body.tone, body.channel)
    return {"ok": True}


class Generate(BaseModel):
    angle: str | None = None
    tone: Literal["standard", "brief"] = "standard"


@router.post("/opportunities/{opp_id}/outreach/generate", tags=["step 5"], summary="Ask the outreach agent for a draft (never sent automatically)")
def generate(opp_id: str, body: Generate, c: S.Ctx = Actor):
    return S.generate_draft(c, opp_id, body.angle, body.tone)


@router.get("/alerts", tags=["step 7"], summary="Progress monitor: stalled, overdue, follow-up due, out-of-date packs")
def alerts(c: S.Ctx = Actor):
    return S.alerts(c, follow_days(c))


@router.post("/opportunities/{opp_id}/outreach/sent", tags=["step 5"], summary="Record that you sent the message yourself")
def sent(opp_id: str, body: Sent, c: S.Ctx = Actor):
    S.mark_sent(c, opp_id, body.via, body.on, follow_days(c))
    return {"ok": True}


@router.post("/opportunities/{opp_id}/outreach/followup", tags=["step 5"], summary="Record a follow-up")
def followup(opp_id: str, c: S.Ctx = Actor):
    S.followup_sent(c, opp_id, follow_days(c))
    return {"ok": True}


@router.post("/opportunities/{opp_id}/outreach/reply", tags=["step 6"], summary="Record a reply; the opportunity becomes Active at once")
def reply(opp_id: str, body: Reply, c: S.Ctx = Actor):
    return S.record_reply(c, opp_id, body.text, body.on)


@router.post("/opportunities/{opp_id}/nurture", tags=["step 5"], summary="Move to nurture with a review date")
def nurture(opp_id: str, body: Nurture, c: S.Ctx = Actor):
    S.to_nurture(c, opp_id, body.review_on, body.reason)
    return {"ok": True}


# ---------- Step 6 ----------
class NoteIn(BaseModel):
    type: Literal["Meeting notes", "Email", "Phone call"] = "Meeting notes"
    on: date | None = None
    text: str
    analyze: bool = Field(True, description="Ask the evidence agent for proposals. The note is saved even if analysis fails.")


class Decide(BaseModel):
    action: Literal["accept", "edit", "reject"]
    value: Any = None
    reason: str | None = None


class Review(BaseModel):
    kind: Literal["score", "cond", "revenue", "blocker_clear"]
    key: str = ""
    value: Any = None
    blocker: str | None = None
    reason: str
    revision: int | None = None


@router.post("/opportunities/{opp_id}/notes", tags=["step 6"], summary="Add conversation notes and get evidence proposals")
def note(opp_id: str, body: NoteIn, c: S.Ctx = Actor):
    return S.add_note(c, opp_id, body.type, body.on, body.text, body.analyze)


@router.post("/proposals/{prop_id}", tags=["step 6"], summary="Accept, edit or reject an AI suggestion")
def decide(prop_id: str, body: Decide, c: S.Ctx = Actor):
    return S.decide_proposal(c, prop_id, body.action, body.value, body.reason)


@router.post("/opportunities/{opp_id}/review", tags=["step 6"], summary="Review a score, condition, revenue or blocker by hand")
def review(opp_id: str, body: Review, c: S.Ctx = Actor):
    return {"change": S.manual_review(c, opp_id, body.kind, body.key, body.value, body.reason, body.blocker, body.revision)}


@router.patch("/opportunities/{opp_id}/need", tags=["step 6"], summary="Edit the client need statement")
def need(opp_id: str, body: dict[str, str], c: S.Ctx = Actor):
    S.update_need(c, opp_id, body)
    return {"ok": True}


# ---------- Step 7 ----------
class TaskIn(BaseModel):
    title: str | None = None
    side: Literal["CGS", "Client"] = "CGS"
    assignee: str
    due: date


class PackIn(BaseModel):
    exception: str | None = Field(None, description="Required when the rules do not say Ready")
    revision: int | None = None


@router.post("/opportunities/{opp_id}/suggestions", tags=["step 7"], summary="Ask for up to 3 next actions from the open gaps")
def suggest(opp_id: str, c: S.Ctx = Actor):
    return {"suggestions": S.suggest_actions(c, opp_id)}


@router.post("/suggestions/{sug_id}/task", tags=["step 7"], summary="Turn a suggestion into an agreed task")
def to_task(sug_id: str, body: TaskIn, c: S.Ctx = Actor):
    return {"id": S.suggestion_to_task(c, sug_id, body.title, body.side, body.assignee, body.due)}


@router.post("/tasks/{task_id}/toggle", tags=["step 7"], summary="Mark a task done, or reopen it")
def toggle(task_id: str, c: S.Ctx = Actor):
    return {"status": S.toggle_task(c, task_id)}


@router.post("/opportunities/{opp_id}/snooze", tags=["step 7"], summary="Snooze the stalled reminder")
def snooze(opp_id: str, days: int = 7, c: S.Ctx = Actor):
    return {"until": S.snooze(c, opp_id, days)}


@router.post("/opportunities/{opp_id}/packs", tags=["step 7"], summary="Submit (or resubmit) the qualification pack")
def pack(opp_id: str, body: PackIn, c: S.Ctx = Actor):
    return {"id": S.submit_pack(c, opp_id, body.exception, body.revision)}


# ---------- Step 8 ----------
class Step8(BaseModel):
    decision: Literal["approve", "return", "nurture", "close"]
    role: Literal["Lead owner", "Another consultant", "Director of Operations"] = "Lead owner"
    new_owner: str | None = None
    handover_accepted: bool = False
    questions: str | None = None
    due: date | None = None
    review_on: date | None = None
    reason: str | None = None


@router.post("/packs/{pack_id}/decision", tags=["step 8"], summary="Approve, return, nurture or close")
def step8(pack_id: str, body: Step8, c: S.Ctx = Actor):
    S.decide_pack(c, pack_id, body)
    return {"ok": True}


# ---------- learning loop ----------
class DirectionIn(BaseModel):
    field: str = Field(examples=["buyer_roles"])
    scope: Literal["theme", "consultant"] = "theme"
    value: str | list[str]
    rationale: str


@router.post("/direction/proposals", tags=["learning loop"], summary="Propose a direction change by hand (whitelisted fields only)")
def propose(body: DirectionIn, c: S.Ctx = Actor):
    return {"id": S.manual_direction_proposal(c, body.field, body.scope, body.value, body.rationale)}


@router.post("/direction/analyze", tags=["learning loop"], summary="Ask the feedback agent to review eligible feedback")
def analyze(scope: Literal["theme", "consultant"] = "theme", c: S.Ctx = Actor):
    return S.analyze_feedback(c, scope)


@router.post("/direction/proposals/{prop_id}/{verdict}", tags=["learning loop"], summary="Approve or reject a direction proposal")
def decide_direction(prop_id: str, verdict: Literal["approve", "reject"], c: S.Ctx = Actor):
    return {"version": S.decide_direction(c, prop_id, verdict == "approve")}


# ---------- demo helpers ----------
@router.post("/demo/advance-days", tags=["system"], summary="Simulation only: move the date forward, for example to reach a follow-up or a nurture review")
def advance(days: int = 7, c: S.Ctx = Actor):
    if not simulated(c.settings):
        raise S.ActionError("Time travel works only with demo data or the synthetic internet.", 403, "demo_only")
    if not 1 <= days <= 366:
        raise S.ActionError("Move between 1 and 366 days.")
    row = c.s.get(m.Setting, "day_offset")
    if row:
        row.value = row.value + days
    else:
        c.s.add(m.Setting(key="day_offset", value=days))
    return {"today": (c.today + timedelta(days=days)).isoformat()}
