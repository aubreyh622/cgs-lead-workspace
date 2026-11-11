"""Write endpoints: every human action in Steps 1-8 and the learning loop (backend step 4).

On one computer there is no login: each request names the person acting in the X-User header, chosen from the
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


def after_response(request: Request):
    """Work that waits until the response is sent: the CRM sync of what the request saved."""
    pending: dict = {}
    yield pending
    if pending.get("ids"):  # the person's action is saved; now bring the CRM in line (a failure is recorded, never raised)
        from api import crm_sync
        crm_sync.after_request(request.app, pending["ids"], pending["today"])


def ctx(request: Request, x_user: str | None = Header(None, description="The person acting, from GET /api/users"),
        pending: dict = Depends(after_response, scope="request")):
    db = request.app.state.db
    session = db.sessions()
    c = S.Ctx(s=session, actor=x_user or "", today=demo_today(session, request.app.state.settings), settings=request.app.state.settings,
              provider_factory=getattr(request.app.state, "provider_factory", None),
              research_factory=lambda: research_bridge(request.app), search_factory=getattr(request.app.state, "search_factory", None),
              mail_factory=lambda: getattr(request.app.state, "mail", None), crm_factory=lambda: getattr(request.app.state, "crm", None))
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
    if c.crm_dirty:
        pending.update(ids=set(c.crm_dirty), today=c.today)


def require_user(request: Request, x_user: str | None = Header(None, description="The person acting, from GET /api/users")) -> str:
    """The X-User check alone, for endpoints that must not hold a database session open (a demo reset)."""
    with request.app.state.db.session() as s:
        row = s.get(m.Setting, "owners")
        users = list(row.value) if row else []
    if not x_user or x_user not in users:
        raise HTTPException(401, f"Say who is acting: send the X-User header with one of {users}.")
    return x_user


def research_bridge(app):
    """One research bridge per server, created when live research is first needed."""
    if getattr(app.state, "research_bridge", None) is None:
        from api.search import search_config
        st = app.state.settings
        if st.research_engine == "agent":  # the tool-using research agent, on whichever web config/search.json names
            from api.research_agent import AgentResearchBridge
            from api.llm import make_provider
            from api.search import make_search
            factory = getattr(app.state, "provider_factory", None) or (lambda: make_provider(st.ai_provider, st.model_for("Research agent")))
            search = getattr(app.state, "search_factory", None)
            app.state.research_bridge = AgentResearchBridge(factory, (lambda as_of: search()) if search else (lambda as_of: make_search(as_of=as_of)),
                                                            background=st.agent_worker, session_factory=app.state.db.session)
        elif search_config()["provider"] == "mock":
            from api.llm import make_provider
            from api.mock_research import MockResearchBridge
            st = app.state.settings
            factory = getattr(app.state, "provider_factory", None) or (lambda: make_provider(st.ai_provider, st.model_for("Research agent")))
            app.state.research_bridge = MockResearchBridge(factory)
        else:
            from api.research_bridge import ResearchBridge
            from api.settings import ROOT
            app.state.research_bridge = ResearchBridge(ROOT, app.state.settings.ai_model)
    return app.state.research_bridge


# scope="function": the change is committed before the response is sent, so a read straight after a write sees it.
# (With the default scope, FastAPI sends the response first and a quick follow-up read could get the old data.)
Actor = Depends(ctx, scope="function")


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
    count: int = Field(10, ge=1, le=10, description="How many companies this round may find (1-10)")


@router.get("/search/settings", tags=["step 1"], summary="Which search provider and queries discovery uses (config/search.json)")
def search_settings():
    from api.search import PROVIDERS, search_config
    return {**search_config(), "installed_providers": list(PROVIDERS)}


@router.post("/candidates/recommend", tags=["step 1"], summary="Recommend up to 10 new candidates (live: web search + AI, checked by code)")
def recommend(body: Recommend, c: S.Ctx = Actor):
    return S.recommend(c, body.intent, count=body.count)


# ---------- automatic searches (api/discovery_searches.py) ----------
class SearchIn(BaseModel):
    name: str = Field("", description="A short name, for example New Chief Digital Officers (not for the main search)")
    query: str = Field("", description="What to look for, as you would tell a researcher (not for the main search)")
    freq: Literal["day", "3days", "week", "2weeks", "4weeks"] = "week"
    weekday: int | None = Field(None, ge=0, le=4, description="0 Monday .. 4 Friday; only for week, 2weeks, 4weeks")
    per_run: int = Field(5, ge=1, le=10, description="Companies per run")


class SearchPatch(BaseModel):
    name: str | None = None
    query: str | None = None
    freq: Literal["day", "3days", "week", "2weeks", "4weeks"] | None = None
    weekday: int | None = Field(None, ge=-1, le=4, description="-1 clears the weekday")
    per_run: int | None = Field(None, ge=1, le=10)
    on: bool | None = None


def _search(c: S.Ctx, search_id: str):
    from api import discovery_searches as DS
    DS.ensure_main(c.s, c.today)
    x = c.s.get(m.DiscoverySearch, search_id)
    if not x:
        raise S.ActionError("Automatic search not found.", 404, "not_found")
    return x


@router.get("/discovery/searches", tags=["step 1"], summary="Automatic searches: what the discovery agent looks for on its own, how often, how many")
def searches(request: Request):
    from sqlalchemy import select
    from api import discovery_searches as DS
    with request.app.state.db.session() as s:
        today = demo_today(s, request.app.state.settings)
        DS.ensure_main(s, today)
        rows = s.scalars(select(m.DiscoverySearch).order_by(m.DiscoverySearch.main.desc(), m.DiscoverySearch.created_on)).all()
        return [DS.view(s, x, today) for x in rows]


@router.post("/discovery/searches", tags=["step 1"], summary="Add an automatic search; it runs with your own research direction")
def add_search(body: SearchIn, c: S.Ctx = Actor):
    from api import discovery_searches as DS
    try:
        x = DS.create(c.s, c.today, c.actor, body.name, body.query, body.freq, body.weekday, body.per_run)
    except DS.Invalid as e:
        raise S.ActionError(str(e))
    S.log(c, None, "h", f"{c.actor} added the automatic search {x.name} ({DS.FREQ_LABEL[x.freq].lower()}, {x.per_run} per run)")
    return DS.view(c.s, x, c.today)


@router.patch("/discovery/searches/{search_id}", tags=["step 1"], summary="Change an automatic search, or switch it on or off")
def edit_search(search_id: str, body: SearchPatch, c: S.Ctx = Actor):
    from api import discovery_searches as DS
    x = _search(c, search_id)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("weekday") == -1:
        fields["weekday"] = None
    try:
        DS.update(c.s, x, fields)
    except DS.Invalid as e:
        raise S.ActionError(str(e))
    S.log(c, None, "h", f"{c.actor} changed the automatic search {x.name}: " + ", ".join(sorted(fields)))
    return DS.view(c.s, x, c.today)


@router.delete("/discovery/searches/{search_id}", tags=["step 1"], summary="Delete an automatic search (the main search can only be switched off)")
def delete_search(search_id: str, c: S.Ctx = Actor):
    x = _search(c, search_id)
    if x.main:
        raise S.ActionError("The main search cannot be deleted. Switch it off instead.", 409, "main_search")
    c.s.delete(x)
    S.log(c, None, "h", f"{c.actor} deleted the automatic search {x.name}")
    return {"ok": True}


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


@router.post("/candidates/{cand_id}/unskip", tags=["step 1"], summary="Undo a skip: the candidate is new again and its feedback is withdrawn")
def unskip(cand_id: str, c: S.Ctx = Actor):
    from api import undo as U
    U.unskip(c, cand_id)
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
    undo_seconds: int = Field(0, ge=0, le=30, description="Accept or reject: the decision can be undone for this long, and the CRM "
                                                         "write and the agents' work wait until then. 0 = at once")


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
    from api import undo as U
    S.step4(c, opp_id, body)
    return {"ok": True, "undo": U.view(c.s.get(m.Opportunity, opp_id))}


@router.post("/opportunities/{opp_id}/step4/undo", tags=["step 4"], summary="Undo an accept or reject while its undo window is open")
def step4_undo(opp_id: str, c: S.Ctx = Actor):
    from api import undo as U
    return U.undo_step4(c, opp_id)


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


@router.get("/todo", tags=["system"], summary="What waits for the person acting, grouped by what they must do, plus workspace problems (the home page)")
def todo(request: Request, c: S.Ctx = Actor):
    from api import todo as TD
    return TD.todo(c, follow_days(c), getattr(request.app.state, "mail", None))


@router.post("/outbound/{out_id}/checked", tags=["step 5"], summary="Mark a failed, unsent or maybe-sent email as looked at: it leaves the to-do list; nothing is sent")
def outbound_checked(out_id: str, c: S.Ctx = Actor):
    from api import undo as U
    return U.mark_checked(c, out_id)


@router.get("/alerts", tags=["step 7"], summary="Progress monitor: stalled, overdue, follow-up due, out-of-date packs")
def alerts(c: S.Ctx = Actor):
    return S.alerts(c, follow_days(c))


@router.post("/opportunities/{opp_id}/outreach/sent", tags=["step 5"], summary="Record that you sent the message yourself")
def sent(opp_id: str, body: Sent, c: S.Ctx = Actor):
    S.mark_sent(c, opp_id, body.via, body.on, follow_days(c))
    return {"ok": True}


class EmailOut(BaseModel):
    to: str
    mode: Literal["draft", "send"]
    seenHash: str
    itemId: str | None = None
    delay: int = Field(0, ge=0, le=30, description="send only: seconds to wait so the person can undo; 0 sends now")


@router.get("/opportunities/{opp_id}/outreach/email", tags=["step 5"],
            summary="Preview exactly what would go out by email (first email from the saved draft, or a follow-up/reconnect inbox draft)")
def email_preview(opp_id: str, to: str | None = None, itemId: str | None = None, c: S.Ctx = Actor):
    return S.email_preview(c, opp_id, to, itemId)


@router.post("/opportunities/{opp_id}/outreach/email", tags=["step 5"],
             summary="A person puts the email in their Gmail drafts (mode draft) or sends it now (mode send); seenHash comes from the preview")
def email_out(opp_id: str, body: EmailOut, c: S.Ctx = Actor):
    return S.email_out(c, opp_id, body.to, body.mode, body.seenHash, body.itemId, body.delay)


@router.post("/outbound/{out_id}/undo", tags=["step 5"], summary="Undo an approved email while it waits: nothing is sent")
def outbound_undo(out_id: str, c: S.Ctx = Actor):
    from api import undo as U
    return U.undo_email(c, out_id)


@router.post("/outbound/{out_id}/send-now", tags=["step 5"], summary="Send an approved email now instead of waiting for the undo window")
def outbound_send_now(out_id: str, c: S.Ctx = Actor):
    from api import undo as U
    return U.send_now(c, out_id)


@router.post("/mail/sync", tags=["step 5"], summary="Check the email threads now: replies go to the inbox, drafts sent from Gmail are recorded")
def mail_sync(c: S.Ctx = Actor):
    return S.sync_mail(c)


@router.get("/toolkit", tags=["agents"], summary="Connectors, the permission table (which agent may use which connector) and the mailbox")
def toolkit(request: Request, c: S.Ctx = Actor):
    from api import toolkit as TK
    box = getattr(request.app.state, "mail", None)
    crm = getattr(request.app.state, "crm", None)
    return {**TK.catalog(c.s), "mail": {"provider": box.name if box else "off", "ready": bool(box and box.ready())},
            "crm": {"provider": crm.name if crm else "off", "mode": c.settings.crm_mode, "ready": bool(crm and crm.ready())}}


# ---------- the CRM (Insightly): api/toolkit/crm.py, api/crm_sync.py ----------
@router.get("/crm", tags=["crm"], summary="The CRM connection: provider, mode, account, and opportunities waiting to sync or failed")
def crm_status(request: Request, c: S.Ctx = Actor):
    from sqlalchemy import select
    from api import crm_sync as CRM
    from api.toolkit.crm import CrmError
    crm = getattr(request.app.state, "crm", None)
    out = {"provider": crm.name if crm else "off", "mode": c.settings.crm_mode, "reads": CRM.reads_on(c), "writes": CRM.writes_on(c)}
    if crm is not None:
        try:
            out["account"] = crm.account()
        except CrmError as e:
            out["error"] = str(e)
    rows = c.s.scalars(select(m.Opportunity).where(m.Opportunity.crm_sync.in_(("sync_pending", "sync_failed", "synced"))))
    out["opportunities"] = [{"id": o.id, "company": o.company.name, "stage": o.stage, "crmSync": o.crm_sync, "crm": CRM.view(o)} for o in rows]
    return out


@router.get("/crm/state", tags=["crm"], summary="The CRM sync state from the workspace database alone (no call to the CRM): mode, last sync, waiting and failed counts")
def crm_state(request: Request, c: S.Ctx = Actor):
    from sqlalchemy import func, select
    from api import crm_sync as CRM
    crm = getattr(request.app.state, "crm", None)
    counts = dict(c.s.execute(select(m.Opportunity.crm_sync, func.count()).group_by(m.Opportunity.crm_sync)).all())
    from datetime import timezone
    utc = lambda t: t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t  # SQLite returns naive UTC times
    last, pulled = utc(c.s.scalar(select(func.max(m.CrmLink.synced_at)))), utc(c.s.scalar(select(func.max(m.CrmLink.pulled_at))))
    conflicts = sum(1 for x in c.s.scalars(select(m.CrmLink.conflicts)) if x)
    return {"provider": crm.name if crm else "off", "mode": c.settings.crm_mode, "reads": CRM.reads_on(c), "writes": CRM.writes_on(c),
            "lastSyncedAt": last.isoformat() if last else None, "lastReadAt": pulled.isoformat() if pulled else None,
            "pending": counts.get("sync_pending", 0), "failed": counts.get("sync_failed", 0), "conflicts": conflicts}


@router.get("/updates", tags=["system"], summary="What happened for the person acting since a moment (the page polls this for notices and the notification history)")
def updates(since: str | None = None, days: int = 7, c: S.Ctx = Actor):
    from datetime import datetime, timezone
    from api import updates as UP
    now = datetime.now(timezone.utc)
    return {"now": now.isoformat(), "items": UP.feed(c.s, c.actor, UP.since_from(since, days))}


@router.post("/crm/pull", tags=["crm"], summary="Read back now what changed in the CRM: edits there (kept for a person to decide), the CRM opportunity's stage, tasks done")
def crm_pull_now(c: S.Ctx = Actor):
    from api import crm_sync as CRM
    if not CRM.reads_on(c):
        raise S.ActionError(f"The CRM is off (CRM_MODE={c.settings.crm_mode}).", 409, "crm_off")
    res = CRM.pull(c)
    if CRM.writes_on(c):  # what the read-back found out of step goes out now
        res["sync"] = CRM.sync(c)
    return res


class ResolveIn(BaseModel):
    field: str = Field(description="The lead field the CRM and the workspace disagree on, from crm.conflicts")
    use: Literal["workspace", "crm"] = Field(description="workspace: write the workspace value to the CRM. crm: keep the CRM's value")


@router.post("/opportunities/{opp_id}/crm/resolve", tags=["crm"], summary="Decide one disagreement between the CRM and the workspace")
def crm_resolve(opp_id: str, body: ResolveIn, c: S.Ctx = Actor):
    from api import crm_sync as CRM
    o = S.opp(c, opp_id)
    CRM.resolve(c, o, body.field, body.use)
    return {"ok": True, "crm": CRM.view(o)}


@router.post("/crm/sync", tags=["crm"], summary="Sync the opportunities waiting for it (or all, to fill an empty CRM) now")
def crm_sync_now(all: bool = False, c: S.Ctx = Actor):
    from api import crm_sync as CRM
    if not CRM.writes_on(c):
        raise S.ActionError(f"Writing to the CRM is off (CRM_MODE={c.settings.crm_mode}). Set CRM_MODE=write_approved.", 409, "crm_off")
    return CRM.sync_all(c) if all else CRM.sync(c)


@router.get("/opportunities/{opp_id}/crm", tags=["crm"], summary="What the CRM already holds for this company and person (the duplicate check), and this opportunity's CRM records")
def crm_matches(opp_id: str, c: S.Ctx = Actor):
    from api import crm_sync as CRM
    return CRM.matches(c, S.opp(c, opp_id))


@router.post("/opportunities/{opp_id}/crm/sync", tags=["crm"], summary="Sync this opportunity to the CRM now")
def crm_sync_one(opp_id: str, c: S.Ctx = Actor):
    from api import crm_sync as CRM
    if not CRM.writes_on(c):
        raise S.ActionError(f"Writing to the CRM is off (CRM_MODE={c.settings.crm_mode}).", 409, "crm_off")
    o = S.opp(c, opp_id)
    CRM.mark(c, o)
    if o.crm_sync != "sync_pending":
        raise S.ActionError("Nothing to sync: this opportunity is not a CRM lead yet (it becomes one when accepted at Step 4).", 409, "not_yet")
    c.s.commit()
    c.crm_dirty.discard(o.id)  # synced here, not again after the request
    res = CRM.sync(c, [o.id])
    return {**res, "crm": CRM.view(c.s.get(m.Opportunity, o.id))}


@router.post("/opportunities/{opp_id}/crm/unlink", tags=["crm"], summary="Forget this opportunity's CRM records (after a lead was deleted in the CRM); the next sync finds or creates the lead again")
def crm_unlink(opp_id: str, c: S.Ctx = Actor):
    from api import crm_sync as CRM
    CRM.unlink(c, S.opp(c, opp_id))
    return {"ok": True}


class PermissionIn(BaseModel):
    enabled: bool | None = Field(..., description="true = on, false = off, null = back to the default")
    reason: str


@router.get("/toolkit/permissions", tags=["agents"],
            summary="The permission table for a management screen: every agent x connector, its state, where it comes from, who changed it")
def permissions(c: S.Ctx = Actor):
    from api import toolkit as TK
    return {**TK.table(c.s), "limits": TK.limits(c.s), "canChange": S.may_manage_permissions(c), "admins": list(c.settings.admins)}


@router.put("/toolkit/permissions/{agent}/{connector}", tags=["agents"],
            summary="Switch a connector on or off for one agent, or back to the default. What the code forbids cannot be switched on")
def set_permission(agent: str, connector: str, body: PermissionIn, c: S.Ctx = Actor):
    return S.set_permission(c, agent, connector, body.enabled, body.reason)


@router.put("/toolkit/connectors/{connector}", tags=["agents"], summary="Switch a connector on or off for every agent (or back to the default)")
def set_connector(connector: str, body: PermissionIn, c: S.Ctx = Actor):
    return S.set_permission(c, "*", connector, body.enabled, body.reason)


@router.get("/toolkit/permissions/history", tags=["agents"], summary="Every change to the permission table and limits, newest first")
def permission_history(limit: int = 100, c: S.Ctx = Actor):
    return S.permission_history(c, limit)


class ReasonIn(BaseModel):
    reason: str


@router.post("/toolkit/permissions/history/{change_id}/undo", tags=["agents"],
             summary="Put a setting back as it was before this change (only the latest change to that setting)")
def undo_permission(change_id: int, body: ReasonIn, c: S.Ctx = Actor):
    return S.undo_permission_change(c, change_id, body.reason)


class LimitIn(BaseModel):
    perDay: int | None = Field(..., description="Calls per day; 0 = no limit; null = back to the config file's default")
    reason: str


@router.put("/toolkit/limits/{connector}", tags=["agents"], summary="Set a connector's daily limit (all agents and people together)")
def set_limit(connector: str, body: LimitIn, c: S.Ctx = Actor):
    return S.set_limit(c, connector, body.perDay, body.reason)


@router.get("/toolkit/usage", tags=["agents"], summary="Connector calls per day, agent and connector, with limits and today's use")
def usage(days: int = 7, c: S.Ctx = Actor):
    return S.connector_usage(c, days)


@router.post("/opportunities/{opp_id}/outreach/followup", tags=["step 5"], summary="Record a follow-up")
def followup(opp_id: str, c: S.Ctx = Actor):
    S.followup_sent(c, opp_id, follow_days(c))
    return {"ok": True}


@router.post("/opportunities/{opp_id}/outreach/reply", tags=["step 6"], summary="Record a reply; the opportunity becomes Active at once")
def reply(opp_id: str, body: Reply, c: S.Ctx = Actor):
    return S.record_reply(c, opp_id, body.text, body.on)


class NurtureReview(BaseModel):
    review_on: date
    reason: str


class CloseLead(BaseModel):
    reason: str
    comment: str | None = None


@router.post("/opportunities/{opp_id}/nurture/review", tags=["step 5"], summary="Change the nurture review date")
def nurture_review(opp_id: str, body: NurtureReview, c: S.Ctx = Actor):
    S.nurture_review(c, opp_id, body.review_on, body.reason)
    return {"ok": True}


@router.post("/opportunities/{opp_id}/nurture/reconnect", tags=["step 5"],
             summary="Review a nurture lead now: the research agent checks for news and the outreach agent drafts a reconnect email")
def nurture_reconnect(opp_id: str, c: S.Ctx = Actor):
    return S.reconnect_now(c, opp_id)


@router.post("/opportunities/{opp_id}/close", tags=["step 5"], summary="Close a lead in nurture, with a reason")
def close_lead(opp_id: str, body: CloseLead, c: S.Ctx = Actor):
    S.close_lead(c, opp_id, body.reason, body.comment)
    return {"ok": True}


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


@router.post("/opportunities/{opp_id}/notes/{note_id}/analyze", tags=["step 6"],
             summary="Ask the evidence agent to read a note saved earlier (for example the first reply) and propose evidence")
def note_analyze(opp_id: str, note_id: str, c: S.Ctx = Actor):
    return S.analyze_note(c, opp_id, note_id)


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
    handover_accepted: bool = Field(False, description="Ignored: only the new owner can accept, by approving themselves or "
                                                       "through POST /api/packs/{id}/handover")
    questions: str | None = None
    due: date | None = None
    review_on: date | None = None
    reason: str | None = None


@router.post("/packs/{pack_id}/decision", tags=["step 8"],
             summary="Approve, return, nurture or close. An approval that hands over to someone else waits for them to accept")
def step8(pack_id: str, body: Step8, c: S.Ctx = Actor):
    return {"ok": True, **S.decide_pack(c, pack_id, body)}


class HandoverAnswer(BaseModel):
    accept: bool
    reason: str | None = Field(None, description="Required when declining")


@router.post("/packs/{pack_id}/handover", tags=["step 8"], summary="The new owner accepts or declines a handover (X-User must be them)")
def handover(pack_id: str, body: HandoverAnswer, c: S.Ctx = Actor):
    return S.answer_handover(c, pack_id, body.accept, body.reason)


# ---------- learning loop ----------
class DirectionIn(BaseModel):
    field: str = Field(examples=["buyer_roles"])
    scope: Literal["theme", "consultant"] = "theme"
    value: str | list | dict = Field(description='A list (for problems, a list of {problem, signals, buyer_roles}), a text, or for company_size {"min_employees": 100, "max_employees": 5000, '
                                                   '"min_revenue_musd": 20, "max_revenue_musd": 1000} (any may be left out)')
    rationale: str


@router.post("/direction/proposals", tags=["learning loop"], summary="Propose a direction change by hand (whitelisted fields only)")
def propose(body: DirectionIn, c: S.Ctx = Actor):
    return {"id": S.manual_direction_proposal(c, body.field, body.scope, body.value, body.rationale)}


class ProblemsIn(BaseModel):
    scope: Literal["theme", "consultant"] = "theme"


@router.post("/direction/problems", tags=["learning loop"],
             summary="Ask the direction agent to turn the CGS library into problems to watch for (a proposal a person approves)")
def problems(body: ProblemsIn, c: S.Ctx = Actor):
    return S.propose_problems(c, body.scope)


@router.post("/direction/analyze", tags=["learning loop"], summary="Ask the direction agent to review eligible feedback")
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
