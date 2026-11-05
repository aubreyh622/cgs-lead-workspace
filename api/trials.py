""""Try it on a company" (B2): run the research agent on a company a person names, and show them the brief.

What it does: the same research run as Steps 2-3 (the same agent, web tools, limits, checks, approved lessons and
connector permissions), started from the agent's page. What it does not do: create a company, candidate or
opportunity, write to the CRM, or send anything. The brief is kept with the trial for the person to read; the AI run is
logged with its trace and counted like any other, and the web searches it makes are metered as usual.

It needs live mode: in demo mode the research agent does not run (briefs come from fixtures).
"""
from __future__ import annotations

from datetime import timedelta
import uuid

from sqlalchemy import select

from api import models as m
from api import services as S
from api import toolkit as TK
from api.llm import LLMError

KEEP = 10  # trials listed per person


def start(c: S.Ctx, company: str, domain: str) -> m.TrialRun:
    if c.demo:
        raise S.ActionError("Try it runs the real research agent, which needs live mode. In demo mode briefs come from prepared examples.",
                            409, "demo_mode")
    company = S.need_text(company, "Company")
    domain = (domain or "").strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").strip("/")
    if not domain or "." not in domain:
        raise S.ActionError("Give the company website (for example arbor-fiberworks.com): the research agent starts from it.")
    TK.require("Research agent", "web", c.s)
    bridge = c.research_factory() if c.research_factory else None
    if bridge is None:
        raise S.ActionError("The research agent is not available in this server.", 409, "unavailable")
    t = m.TrialRun(id=f"try-{uuid.uuid4().hex[:8]}", company=company, domain=domain, requested_by=c.actor, on=c.today, status="running")
    c.s.add(t)
    c.s.commit()  # the run below can take a while; do not hold the database
    try:
        t.external_run_id = bridge.start(company, domain, c.actor, **({"as_of": c.today} if getattr(bridge, "dated", False) else {}),
                                         **({"size": S.current_direction(c, c.actor).company_size or {}} if getattr(bridge, "criteria", False) else {}))
    except LLMError as e:
        t.status, t.error = "failed", str(e)
    S.log(c, None, "h", f"{c.actor} tried the research agent on {company} ({domain}); nothing is saved to the workflow")
    return collect(c, t)


def collect(c: S.Ctx, t: m.TrialRun) -> m.TrialRun:
    """Check a running trial; when it is done, keep the brief and log the AI run."""
    if t.status != "running" or not t.external_run_id:
        return t
    bridge = c.research_factory() if c.research_factory else None
    if bridge is None:
        return t
    st = bridge.status(t.external_run_id)
    if hasattr(bridge, "uses"):
        TK.save_uses(c.s, bridge.uses(t.external_run_id))
    if st["status"] in ("running", "stopping", "queued"):
        return t
    from api.research_bridge import card_to_brief
    u = st.get("usage") or {}
    card = bridge.result(t.external_run_id) if st["status"] in ("completed", "incomplete") else None
    if card:
        t.brief, t.person = card_to_brief(card, t.requested_by, (c.today + timedelta(days=4)).isoformat(), u)
        t.status = "completed"
    else:
        t.status, t.error = "failed", st.get("error") or "No brief was produced."
    t.run_id = S.record_run(c, "Research agent", None, getattr(bridge, "prompt", "research"), u.get("model_calls", 0),
                            "completed" if card else "failed", t.error, subject=f"Try it: {t.company}", model=getattr(bridge, "label", None),
                            usage=type("U", (), {"tokens_in": u.get("tokens_in"), "tokens_out": u.get("tokens_out")})())
    if hasattr(bridge, "trace"):
        c.s.get(m.AgentRun, t.run_id).trace = bridge.trace(t.external_run_id) or None
    return t


def recent(c: S.Ctx) -> list[m.TrialRun]:
    rows = c.s.scalars(select(m.TrialRun).where(m.TrialRun.requested_by == c.actor).order_by(m.TrialRun.created_at.desc())
                       .limit(KEEP)).all()
    return [collect(c, t) for t in rows]


def view(t: m.TrialRun) -> dict:
    return {"id": t.id, "agent": t.agent, "company": t.company, "domain": t.domain, "requestedBy": t.requested_by, "on": t.on.isoformat(),
            "status": t.status, "brief": t.brief, "person": t.person, "error": t.error, "runId": t.run_id}
