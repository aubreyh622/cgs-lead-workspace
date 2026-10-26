"""Workspace actions: every change a person makes in Steps 1-8 and the learning loop.

Each function checks the workflow rules, writes the change, and appends history. The rules engine
(backend/rules.py) is the only thing that calculates scores. Agents only propose; people decide.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from datetime import date, datetime, timedelta, timezone
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api import demo_agents as A
from api import live_agents as L
from api.llm import LLMError, make_provider
from api import models as m
from api.settings import Settings
from backend.rules import CONDITIONS, LABELS, REASONS, RULE_VERSION, WEIGHTS, calculate, validate_score

WHITELIST = ("problem", "signals", "buyer_roles", "exclusions", "excluded_domains")
SCOPES = ("opportunity", "theme", "consultant", "cgs")
REJECT_REASONS = ("Outside our target client profile", "No clear business problem", "Contact lacks decision authority",
                  "Existing relationship or already in progress", "Evidence too thin or unreliable", "Other")
ACCEPT_REASONS = ("Relevant signal", "Strong contact", "Relationship to build on", "Other")
SKIP_REASONS = ("Wrong industry or company type", "Too small", "Wrong contact", "Existing relationship or already in progress",
                "Competitor or company we do not work with", "Signal too old or unreliable", "Other")
CLOSE_REASONS = ("Client declined", "No budget this year", "Problem solved internally", "Went with another advisor", "Other")
APPROVER_ROLES = ("Lead owner", "Another consultant", "Director of Operations")
QUAL_STAGES = ("active", "approval")


class ActionError(Exception):
    """A request that breaks a workflow rule. status 409 = wrong moment, 400 = missing or invalid input."""

    def __init__(self, message: str, status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass
class Ctx:
    s: Session
    actor: str
    today: date
    settings: Settings
    provider_factory: Callable[[], Any] | None = None  # tests inject a fake AI company here

    @property
    def demo(self) -> bool:
        return self.settings.app_mode == "demo"


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "x"


def fmt(d: date) -> str:
    return f"{d.day} {d:%b}"


def need_text(value: str | None, label: str) -> str:
    if not value or not value.strip():
        raise ActionError(f"{label} is required.", 400, "reason_required")
    return value.strip()


# ---------- shared helpers ----------
def opp(c: Ctx, opp_id: str, *stages: str) -> m.Opportunity:
    o = c.s.get(m.Opportunity, opp_id)
    if not o:
        raise ActionError("Opportunity not found.", 404, "not_found")
    if stages and o.stage not in stages:
        raise ActionError(f"This action needs the opportunity to be {' or '.join(stages)}; it is {o.stage}.", 409, "wrong_stage")
    return o


def check_revision(o: m.Opportunity, expected: int | None) -> None:
    if expected is not None and expected != o.revision:
        raise ActionError("Someone else changed this opportunity. Reload it and try again.", 409, "revision_conflict")


def log(c: Ctx, opp_id: str | None, actor_type: str, text: str) -> None:
    c.s.add(m.Activity(opportunity_id=opp_id, on=c.today, actor_type=actor_type, text=text))


def touch(c: Ctx, o: m.Opportunity) -> None:
    o.revision += 1
    o.last_progress = c.today


def open_pack(c: Ctx, o: m.Opportunity) -> m.Pack | None:
    return c.s.scalars(select(m.Pack).where(m.Pack.opportunity_id == o.id, m.Pack.status == "open")).first()


def qualification_changed(c: Ctx, o: m.Opportunity, what: str) -> None:
    """Any change to scores, conditions, blocker, revenue or need makes a submitted pack out of date."""
    touch(c, o)
    p = open_pack(c, o)
    if p and not p.stale_reason:
        p.stale_reason = f"{what} on {fmt(c.today)}"
        log(c, o.id, "s", f"Qualification pack marked out of date: {what}")
    check_ready(c, o)


def check_ready(c: Ctx, o: m.Opportunity) -> None:
    if o.stage == "active":
        r = calculate(rules_input(o))
        if r["readiness"] == "Ready" and o.next_action != "Submit the qualification pack":
            o.next_action, o.next_due = "Submit the qualification pack", c.today + timedelta(days=2)
            log(c, o.id, "s", f"Rules engine ({RULE_VERSION}): Ready. Q {r['ql']:g}, coverage {r['coverage']}%")


def rules_input(o: m.Opportunity) -> dict:
    return {"scores": dict(o.scores), "conditions": dict(o.conditions), "blocker": o.blocker, "revenue": o.revenue}


def score_delta_text(o: m.Opportunity, key: str, value) -> str:
    before = calculate(rules_input(o))
    after = calculate({**rules_input(o), "scores": {**o.scores, key: value}})
    sign = lambda x: f"{'+' if x >= 0 else ''}{round(x, 1):g}"
    old = o.scores.get(key)
    return f"{key} {'pending' if old is None else f'{old:g}'} → {'pending' if value is None else f'{value:g}'}: Q {sign(after['ql'] - before['ql'])}, P {sign(after['pl'] - before['pl'])}"


def record_run(c: Ctx, agent: str, o: m.Opportunity | None, prompt: str, calls: int, status="completed", error=None, subject="",
               model: str | None = None, usage=None) -> str:
    rid = A.run_id()
    c.s.add(m.AgentRun(id=rid, agent=agent, opportunity_id=o.id if o else None, subject=subject or (o.company.name if o else ""),
                       model=model or (A.DEMO_MODEL if c.demo else "live-provider"), prompt_version=prompt, status=status, calls=calls,
                       tokens_in=getattr(usage, "tokens_in", None), tokens_out=getattr(usage, "tokens_out", None), error=error, on=c.today))
    c.s.flush()
    return rid


def provider(c: Ctx):
    if c.provider_factory:
        return c.provider_factory()
    return make_provider(c.settings.ai_provider, c.settings.ai_model)


def run_agent(c: Ctx, agent: str, o: m.Opportunity | None, prompt: str, fn, calls=1, subject="", live=None, live_prompt=None):
    """Run an agent and log the run. Returns (result, run_id).

    Demo mode runs fn() (text rules). Live mode runs live(provider) when that agent has a real version;
    otherwise, or when the AI call fails, it records a failed run and raises. It never falls back to demo results.
    """
    if c.demo:
        return fn(), record_run(c, agent, o, prompt, calls, subject=subject)
    prompt = live_prompt or prompt
    try:
        if live is None:
            A.require_demo(c.settings.app_mode, agent)
        p = provider(c)
        result, usage = live(p)
    except (A.AgentUnavailable, LLMError) as e:
        record_run(c, agent, o, prompt, 0, "failed", str(e), subject, model=None)
        c.s.commit()  # keep the failure record even though the request fails
        code = "agent_unavailable" if isinstance(e, A.AgentUnavailable) else f"ai_{e.kind}"
        raise ActionError(str(e), 503, code)
    return result, record_run(c, agent, o, prompt, 1, subject=subject, model=p.label, usage=usage)


def feedback(c: Ctx, subject: str, step: int, outcome: str, reason: str, scope: str, version: str,
             comment: str = "", minutes: float | None = None, opp_id: str | None = None) -> m.Feedback:
    if scope not in SCOPES:
        raise ActionError(f"Scope must be one of {SCOPES}.")
    if minutes is not None and minutes < 0:
        raise ActionError("Review time cannot be negative.")
    f = m.Feedback(id=new_id("fb"), subject=subject, opportunity_id=opp_id, step=step, outcome=outcome, reason=reason,
                   comment=comment, scope=scope, by=c.actor, on=c.today, minutes=minutes, direction_version=version)
    c.s.add(f)
    return f


def current_direction(c: Ctx, personal_for: str | None = None) -> m.DirectionVersion:
    if personal_for:
        p = c.s.scalars(select(m.DirectionVersion).where(m.DirectionVersion.scope == "personal", m.DirectionVersion.owner == personal_for)
                        .order_by(m.DirectionVersion.created_at.desc())).first()
        if p:
            return p
    return c.s.scalars(select(m.DirectionVersion).where(m.DirectionVersion.scope == "shared")
                       .order_by(m.DirectionVersion.created_at.desc(), m.DirectionVersion.version.desc())).first()


def propose_exclusion(c: Ctx, target: str, domain: str, scope: str, why: str, fb: m.Feedback) -> None:
    c.s.add(m.DirectionProposal(id=new_id("dp"), type="exclude", target=target, domain=domain, scope="theme" if scope == "opportunity" else scope,
                                origin="feedback", rationale=why, feedback_ids=[fb.id], base_version=current_direction(c).version,
                                status="pending", created_on=c.today))


# ---------- creating opportunities and running research ----------
def new_opportunity(c: Ctx, *, opp_id: str, company: str, domain: str, title: str, team: str, person: str | None, role: str,
                    source_type: str, source_note: str, brief: dict, rating=3, company_id: str | None = None) -> m.Opportunity:
    existing = c.s.scalars(select(m.Company).where(func.lower(m.Company.name) == company.lower())).first()
    if company_id is None:
        company_id = existing.id if existing else slug(company)
    if not c.s.get(m.Company, company_id):
        c.s.add(m.Company(id=company_id, name=company, domain=domain or ""))
    contact = m.Contact(id=new_id("ct"), company_id=company_id, name=person or None, title=role or "Contact to confirm")
    c.s.add(contact)
    c.s.flush()
    o = m.Opportunity(id=opp_id, company_id=company_id, contact_id=contact.id, title=title, team=team, stage="researching", rating=rating,
                      source_type=source_type, source_note=source_note, direction_version=current_direction(c, c.actor).version,
                      scores={k: None for k in WEIGHTS}, conditions={k: None for k in CONDITIONS}, need={}, last_progress=c.today)
    c.s.add(o)
    c.s.flush()
    c.s.add(m.ResearchBrief(opportunity_id=o.id, **brief))
    c.s.add(m.ResearchTask(id=new_id("rt"), opportunity_id=o.id))
    log(c, o.id, "h", f"{'Picked for research' if source_type == 'ai' else 'Added'} by {c.actor}")
    c.s.flush()
    return o


def unique_opp_id(c: Ctx, base: str) -> str:
    oid, n = base, 2
    while c.s.get(m.Opportunity, oid):
        oid, n = f"{base}-{n}", n + 1
    return oid


def run_research(c: Ctx, o: m.Opportunity) -> None:
    """Steps 2-3. Demo mode finishes at once from fixtures; live mode records a failure until step 6 connects providers."""
    task = c.s.scalars(select(m.ResearchTask).where(m.ResearchTask.opportunity_id == o.id).order_by(m.ResearchTask.started_at.desc())).first()
    try:
        A.require_demo(c.settings.app_mode, "Research agent")
    except A.AgentUnavailable as e:
        task.status, task.error = "failed", str(e)
        record_run(c, "Research agent", o, "research-v2.3", 0, "failed", str(e))
        log(c, o.id, "a", "Research stopped: the live provider is not connected")
        return
    b = o.brief
    if task.returned_with:
        sp = A.second_pass(o.id, task.returned_with, c.today)
        b.facts = b.facts + [{"id": f"f{len(b.facts) + 1}", **sp["fact"]}]
        b.flags, b.recommendation = [], sp["recommendation"]
        if o.id in A.SECOND_PASS_NAMES or not o.contact.name:
            o.contact.name = sp["contact_name"]
            if sp["contact_title"]:
                o.contact.title = sp["contact_title"]
    task.status, task.progress = "done", 5
    task.log = task.log + [{"m": "Verified identity"}, {"m": "Checked duplicates"}, {"m": "Gathered sources"}, {"m": "Wrote brief"}, {"m": "Estimated priority"}]
    o.stage = "review"
    o.revision += 1
    record_run(c, "Research agent", o, "research-v2.3", (b.usage or {}).get("calls", 3))
    log(c, o.id, "a", f"Research agent finished the brief: {len(b.facts)} sources" + (f", {len(b.flags)} flag" if b.flags else ""))


def pick_candidate(c: Ctx, cand_id: str) -> list[str]:
    cand = c.s.get(m.Candidate, cand_id)
    if not cand:
        raise ActionError("Candidate not found.", 404, "not_found")
    if cand.status != "new":
        raise ActionError(f"This candidate was already {cand.status}.", 409, "wrong_status")
    cand.status = "sent"
    created = []
    if cand.demo_star:
        o = new_opportunity(c, opp_id=unique_opp_id(c, "corvane"), company=cand.company, domain=cand.domain, title="Scaling AI inspection across twelve plants",
                            team="Digital office", person=cand.person, role=cand.title, source_type="ai",
                            source_note=f"AI recommendation, round {cand.round}, direction {cand.direction_version}",
                            brief=A.candidate_brief(cand, c.actor, c.today), rating=4, company_id="corvane")
        run_research(c, o)
        created.append(o.id)
        if c.demo and not c.s.get(m.Opportunity, "corvane-ops"):
            sib = new_opportunity(c, opp_id="corvane-ops", company=cand.company, domain=cand.domain, title="Arden plant integration plan",
                                  team="Operations", person=None, role="Chief Operating Officer", source_type="ai",
                                  source_note="Found by the research agent while researching Corvane Industrial",
                                  brief=A.star_sibling_brief(c.today), company_id="corvane")
            run_research(c, sib)
            log(c, o.id, "a", "Research agent also found a second opportunity: Arden plant integration plan (Operations team)")
            created.append(sib.id)
    else:
        o = new_opportunity(c, opp_id=unique_opp_id(c, slug(cand.company)), company=cand.company, domain=cand.domain,
                            title=f"{cand.signal}: planning support", team="", person=cand.person, role=cand.title, source_type="ai",
                            source_note=f"AI recommendation, round {cand.round}, direction {cand.direction_version}",
                            brief=A.candidate_brief(cand, c.actor, c.today))
        run_research(c, o)
        created.append(o.id)
    return created


def skip_candidate(c: Ctx, cand_id: str, reason: str, scope: str) -> None:
    cand = c.s.get(m.Candidate, cand_id)
    if not cand:
        raise ActionError("Candidate not found.", 404, "not_found")
    if cand.status != "new":
        raise ActionError(f"This candidate was already {cand.status}.", 409, "wrong_status")
    if reason not in SKIP_REASONS:
        raise ActionError(f"Reason must be one of {SKIP_REASONS}.")
    cand.status, cand.skip_reason, cand.skip_scope = "skipped", reason, scope
    fb = feedback(c, cand.company, 1, "Skipped", reason, scope, cand.direction_version)
    c.s.flush()
    if re.search(r"Existing relationship|Competitor", reason):
        propose_exclusion(c, cand.company, cand.domain, scope, f"Skipped at Step 1 by {c.actor}: {reason.lower()}.", fb)


def add_manual(c: Ctx, company: str, domain: str, name: str, title: str, source: str, notes: str) -> str:
    company = need_text(company, "Company")
    o = new_opportunity(c, opp_id=unique_opp_id(c, slug(company)), company=company, domain=domain, title="Opportunity to define", team="",
                        person=name.strip() or None, role=title.strip(), source_type="manual", source_note=f"{source}, added by {c.actor}",
                        brief=A.manual_brief(company, notes.strip(), source, bool(name.strip()), c.actor, c.today))
    run_research(c, o)
    return o.id


def research_control(c: Ctx, opp_id: str, action: str) -> None:
    o = opp(c, opp_id, "researching")
    task = c.s.scalars(select(m.ResearchTask).where(m.ResearchTask.opportunity_id == o.id).order_by(m.ResearchTask.started_at.desc())).first()
    if action == "stop":
        if task.status != "running":
            raise ActionError("Only running research can be stopped.", 409, "wrong_status")
        task.status, task.pause_reason = "paused", "user"
        log(c, o.id, "h", f"Research stopped by {c.actor}")
    elif action == "resume":
        if task.status != "paused":
            raise ActionError("Only paused research can be resumed.", 409, "wrong_status")
        task.budget_extended = task.pause_reason == "budget"
        task.status, task.pause_reason = "running", None
        log(c, o.id, "h", f"Research resumed by {c.actor}")
        run_research(c, o)
    elif action == "retry":
        if task.status != "failed":
            raise ActionError("Only failed research can be retried.", 409, "wrong_status")
        task.status, task.error = "running", None
        log(c, o.id, "h", f"Research retried by {c.actor}")
        run_research(c, o)


# ---------- Step 4 ----------
def step4(c: Ctx, opp_id: str, b) -> None:
    o = opp(c, opp_id, "review")
    check_revision(o, b.revision)
    owners = owners_list(c)
    if b.decision == "accept":
        if not b.duplicates_checked:
            raise ActionError("Confirm that you checked for existing relationships and duplicates.", 400, "check_required")
        if b.owner not in owners:
            raise ActionError(f"Owner must be one of {owners}.")
        if b.rating not in (1, 2, 3, 4, 5):
            raise ActionError("Rating must be 1 to 5.")
        o.stage, o.owner, o.rating = "identified", b.owner, b.rating
        o.next_action, o.next_due = need_text(b.next_action, "Next step"), b.due or c.today + timedelta(days=3)
        o.brief.frozen_on = c.today
        feedback(c, o.company.name, 4, "Accepted", b.accept_reason or "Relevant signal", b.scope, o.direction_version, b.comment or "", b.minutes, o.id)
        c.s.add(m.Decision(opportunity_id=o.id, step=4, decision="Accepted", by=c.actor, note=f"Rating {b.rating}, owner {b.owner}", on=c.today))
        log(c, o.id, "h", f"Accepted by {c.actor}: rating {b.rating}, owner {b.owner}")
    elif b.decision == "return":
        q = need_text(b.questions, "What the agent should find out")
        o.stage = "researching"
        c.s.add(m.ResearchTask(id=new_id("rt"), opportunity_id=o.id, returned_with=q, started_at=datetime.now(timezone.utc) + timedelta(seconds=1)))
        feedback(c, o.company.name, 4, "Returned", "Returned for research", b.scope, o.direction_version, q, b.minutes, o.id)
        c.s.add(m.Decision(opportunity_id=o.id, step=4, decision="Returned for research", by=c.actor, note=q, on=c.today))
        log(c, o.id, "h", f"Returned for research by {c.actor}: {q}")
        c.s.flush()
        run_research(c, o)
    else:
        if b.reason not in REJECT_REASONS:
            raise ActionError(f"Reason must be one of {REJECT_REASONS}.")
        if b.reason == "Other":
            need_text(b.comment, "A comment (the reason is Other)")
        o.stage = "rejected"
        fb = feedback(c, o.company.name, 4, "Rejected", b.reason, b.scope, o.direction_version, b.comment or "", b.minutes, o.id)
        c.s.add(m.Decision(opportunity_id=o.id, step=4, decision="Rejected", by=c.actor, note=b.reason, on=c.today))
        log(c, o.id, "h", f"Rejected by {c.actor}: {b.reason}")
        c.s.flush()
        if b.suggest_exclusion:
            propose_exclusion(c, o.company.name, o.company.domain, b.scope, f"Rejected at Step 4 by {c.actor}: {b.reason.lower()}.", fb)
    o.revision += 1


def confirm_contact(c: Ctx, opp_id: str, name: str, title: str, note: str) -> None:
    o = opp(c, opp_id)
    name, note = need_text(name, "Name"), need_text(note, "Where you verified it")
    ct = o.contact
    ct.name, ct.title = name, title.strip() or ct.title
    ct.confirmed_by, ct.confirmed_on, ct.confirmed_note = c.actor, c.today, note
    b = o.brief
    b.flags = [f for f in b.flags if f["type"] not in ("No contact found", "Identity mismatch")]
    if o.stage == "review" and not b.flags:
        b.recommendation = {"decision": "accept", "reason": "Contact confirmed by a person. The rest of the brief is unchanged."}
    touch(c, o)
    log(c, o.id, "h", f"Contact confirmed by {c.actor}: {name}, {ct.title} ({note}). Confirms name and role, not buying authority.")


def save_sibling(c: Ctx, opp_id: str, title: str, team: str, name: str, role: str) -> str:
    src = opp(c, opp_id)
    title = need_text(title, "Opportunity title")
    brief = {k: getattr(src.brief, k) for k in ("facts", "hypothesis", "relevance", "alternative", "unknowns", "priority", "suggested", "usage")}
    brief["flags"] = [] if name.strip() else [{"type": "No contact found", "text": f"No contact named yet for {role.strip() or 'this opportunity'}."}]
    brief["recommendation"] = ({"decision": "accept", "reason": "Saved by a consultant from an existing brief."} if name.strip()
                               else {"decision": "return", "reason": "Find a named contact before accepting."})
    o = new_opportunity(c, opp_id=unique_opp_id(c, f"{src.company_id}-{slug(title)}"), company=src.company.name, domain=src.company.domain,
                        title=title, team=team.strip(), person=name.strip() or None, role=role.strip(), source_type="manual",
                        source_note=f"Saved from the “{src.title}” brief by {c.actor}", brief=brief, rating=src.rating, company_id=src.company_id)
    task = c.s.scalars(select(m.ResearchTask).where(m.ResearchTask.opportunity_id == o.id)).first()
    task.status, task.progress = "done", 5
    o.stage = "review"
    return o.id


# ---------- Step 5 ----------
def outreach_row(c: Ctx, o: m.Opportunity) -> m.Outreach:
    row = c.s.get(m.Outreach, o.id)
    if not row:
        row = m.Outreach(opportunity_id=o.id, status="none")
        c.s.add(row)
    return row


def save_draft(c: Ctx, opp_id: str, subject: str, body: str, angle: str | None, tone: str, channel: str | None) -> None:
    o = opp(c, opp_id, "identified")
    r = outreach_row(c, o)
    r.subject, r.body, r.angle, r.tone, r.channel, r.status = need_text(subject, "Subject"), need_text(body, "Message"), angle, tone, channel, "draft"
    touch(c, o)
    log(c, o.id, "h", f"Outreach draft saved by {c.actor}")


def mark_sent(c: Ctx, opp_id: str, via: str, on: date | None, follow_days: int) -> None:
    o = opp(c, opp_id, "identified")
    r = outreach_row(c, o)
    r.status, r.sent_on, r.sent_via = "sent", on or c.today, need_text(via, "Channel")
    o.stage, o.next_action, o.next_due = "contacted", "Follow up if no reply", r.sent_on + timedelta(days=follow_days)
    touch(c, o)
    log(c, o.id, "h", f"{via} sent by {c.actor}. The workspace did not send it.")


def followup_sent(c: Ctx, opp_id: str, follow_days: int) -> None:
    o = opp(c, opp_id, "contacted")
    r = outreach_row(c, o)
    r.status, r.sent_on = "followup", c.today
    o.next_action, o.next_due = "Follow up if no reply", c.today + timedelta(days=follow_days)
    touch(c, o)
    log(c, o.id, "h", f"Follow-up sent by {c.actor}")


def to_nurture(c: Ctx, opp_id: str, review_on: date | None, reason: str) -> None:
    o = opp(c, opp_id, "identified", "contacted", "active")
    o.stage, o.next_action, o.next_due = "nurture", "Revisit the opportunity", review_on or c.today + timedelta(days=60)
    touch(c, o)
    log(c, o.id, "h", f"Moved to nurture by {c.actor}: {need_text(reason, 'Reason')}")


def record_reply(c: Ctx, opp_id: str, text: str, on: date | None) -> dict:
    o = opp(c, opp_id, "contacted", "identified")
    text = need_text(text, "Reply text")
    r = outreach_row(c, o)
    r.status, r.reply_on, r.reply_text = "replied", on or c.today, text
    c.s.add(m.Note(id=new_id("n"), opportunity_id=o.id, type="Email reply", on=r.reply_on, text=text, added_by=c.actor))
    o.stage = "active"  # any reply moves the opportunity to Active at once, whatever it says
    o.next_action, o.next_due = "Hold the first conversation", r.reply_on + timedelta(days=7)
    touch(c, o)
    log(c, o.id, "h", f"Reply recorded by {c.actor}; stage set to Active")
    try:
        label, _ = run_agent(c, "A2 Evidence and engagement", o, "a2-1.4", lambda: A.label_reply(text),
                             live=lambda p: L.label_reply(p, text), live_prompt=L.LABEL_PROMPT_VERSION)
    except ActionError as e:
        o.reply_label = "pending"
        log(c, o.id, "s", f"Reply label pending ({e}). Classify by hand; the opportunity is Active either way.")
        return {"replyLabel": "pending", "warning": str(e)}
    o.reply_label = label
    log(c, o.id, "a", f"Evidence agent labeled the reply: {label}")
    return {"replyLabel": label}


# ---------- Step 6: notes, proposals, reviews ----------
def add_note(c: Ctx, opp_id: str, type_: str, on: date | None, text: str, analyze: bool) -> dict:
    o = opp(c, opp_id, *QUAL_STAGES)
    note = m.Note(id=new_id("n"), opportunity_id=o.id, type=type_, on=on or c.today, text=need_text(text, "Notes"), added_by=c.actor)
    c.s.add(note)
    touch(c, o)
    log(c, o.id, "h", f"{type_} added by {c.actor}")
    c.s.flush()
    if not analyze:
        return {"noteId": note.id, "proposals": 0}
    c.s.commit()  # the original text is saved even if analysis fails
    star = o.company_id == "corvane" and o.title.startswith("Scaling AI")
    context = {"company": o.company.name, "opportunity": o.title, "team": o.team,
               "confirmed_scores": o.scores, "confirmed_conditions": o.conditions, "confirmed_blocker": o.blocker}
    (found, dropped_by_check), run = run_agent(
        c, "A2 + A3 Evidence and qualification", o, "a2-1.4 / a3-1.2", lambda: (A.extract_evidence(note.text, star), {}), calls=2,
        live=lambda p: L.extract_evidence(p, note.text, context), live_prompt=L.EVIDENCE_PROMPT_VERSION)
    existing = {(p.kind, p.key) for p in c.s.scalars(select(m.Proposal).where(m.Proposal.opportunity_id == o.id, m.Proposal.status != "rejected"))}
    kept, dropped = 0, 0
    for p in found:
        if star and (p["kind"], p["key"]) in existing:
            continue
        if p["type"] == "client_statement" and not A.quote_in_text(p["quote"], note.text):
            dropped += 1  # an invented or altered quote never reaches a person
            continue
        c.s.add(m.Proposal(id=new_id("pr"), opportunity_id=o.id, note_id=note.id, agent_run_id=run, kind=p["kind"], key=p["key"],
                           value=p["value"], quote=p["quote"], why=p["why"], evidence_type=p["type"]))
        kept += 1
    dropped += dropped_by_check.get("quote_not_found", 0)
    others = {k: v for k, v in dropped_by_check.items() if k != "quote_not_found"}
    log(c, o.id, "a", f"Evidence agent proposed {kept} item{'s' if kept != 1 else ''} for review"
        + (f"; {dropped} dropped because the quote is not in the notes" if dropped else "")
        + (f"; also dropped: {others}" if others else ""))
    return {"noteId": note.id, "proposals": kept, "droppedForInvalidQuote": dropped, "droppedOther": others}


def _valid_value(kind: str, key: str, value):
    if kind == "score":
        try:
            validate_score(key, value)
        except ValueError as e:
            raise ActionError(f"{e} Scores are 0-5 (History also allows 2.5).", 422, "invalid_value") from None
    elif kind == "cond":
        if key not in CONDITIONS or value not in (True, False, None):
            raise ActionError("A condition is true, false or null (pending).")
    elif kind == "blocker":
        if value not in REASONS:
            raise ActionError(f"Blocker must be one of {tuple(REASONS)}.")
    return value


def _current_value(o: m.Opportunity, kind: str, key: str):
    return o.scores.get(key) if kind == "score" else o.conditions.get(key) if kind == "cond" else o.blocker


def _apply(c: Ctx, o: m.Opportunity, kind: str, key: str, value) -> str:
    if kind == "score":
        text = score_delta_text(o, key, value)
        o.scores = {**o.scores, key: value}
        return text
    if kind == "cond":
        o.conditions = {**o.conditions, key: value}
        return f"{CONDITIONS[key]}: {'met' if value is True else 'not met' if value is False else 'pending'}"
    o.blocker = value
    return f"Blocker: {REASONS[value]}"


def decide_proposal(c: Ctx, prop_id: str, action: str, value=None, reason: str | None = None) -> dict:
    p = c.s.get(m.Proposal, prop_id)
    if not p:
        raise ActionError("Suggestion not found.", 404, "not_found")
    if p.status != "pending":
        raise ActionError(f"This suggestion was already {p.status}.", 409, "wrong_status")
    o = opp(c, p.opportunity_id, *QUAL_STAGES)
    p.decided_by, p.decided_at = c.actor, datetime.now(timezone.utc)
    if action == "reject":
        p.status, p.reason = "rejected", need_text(reason, "A reason for rejecting")
        log(c, o.id, "h", f"Suggestion rejected by {c.actor}: {p.key} ({p.reason})")
        return {"status": "rejected"}
    final = p.value if action == "accept" else _valid_value(p.kind, p.key, value)
    if action == "edit":
        reason = need_text(reason, "A reason for editing")
    current = _current_value(o, p.kind, p.key)
    if current is not None and current != final and not (reason and reason.strip()):
        raise ActionError(f"This conflicts with the confirmed value ({current}). Give a reason to replace it.", 400, "conflict_needs_reason")
    msg = _apply(c, o, p.kind, p.key, final)
    p.status, p.final_value, p.reason = ("edited" if action == "edit" else "accepted"), final, (reason or "").strip() or None
    c.s.add(m.Evidence(opportunity_id=o.id, proposal_id=p.id, kind=p.kind, key=p.key, value=final, quote=p.quote, evidence_type=p.evidence_type,
                       how=p.status, reason=p.reason or "", by=c.actor, on=c.today))
    if p.kind == "cond" and p.key == "problem" and final is True and not (o.need or {}).get("problem"):
        o.need = A.star_need() if o.company_id == "corvane" else {**(o.need or {}), "problem": p.quote}
    log(c, o.id, "h", f"{'Edited and accepted' if action == 'edit' else 'Accepted'} by {c.actor}: {msg}" + (f" ({p.reason})" if p.reason else ""))
    qualification_changed(c, o, f"{LABELS.get(p.key) or CONDITIONS.get(p.key) or 'Blocker'} changed")
    return {"status": p.status, "change": msg}


def manual_review(c: Ctx, opp_id: str, kind: str, key: str, value, reason: str, blocker: str | None = None, revision: int | None = None) -> str:
    o = opp(c, opp_id, *QUAL_STAGES)
    check_revision(o, revision)
    reason = need_text(reason, "A reason for the review")
    if kind == "revenue":
        before = o.revenue
        if value is None:
            o.revenue_low = o.revenue_high = None
        else:
            lo, hi = int(value["low"]), int(value["high"])
            if lo <= 0 or hi < lo:
                raise ActionError("Revenue needs a low value above 0 and a high value at least as large.")
            o.revenue_low, o.revenue_high = lo, hi
        msg = f"CGS revenue {before or 'to estimate'} → {o.revenue or 'to estimate'}"
        what = "Revenue estimate changed"
    elif kind == "blocker_clear":
        if not o.blocker:
            raise ActionError("There is no blocker to clear.", 409, "wrong_status")
        msg, o.blocker, what = f"Blocker cleared: {REASONS[o.blocker]}", None, "Blocker cleared"
    else:
        _valid_value(kind, key, value)
        msg = _apply(c, o, kind, key, value)
        what = f"{LABELS.get(key) or CONDITIONS.get(key)} changed"
        if kind == "cond" and value is False and blocker:
            _valid_value("blocker", "", blocker)
            msg += "; " + _apply(c, o, "blocker", "", blocker)
    c.s.add(m.Evidence(opportunity_id=o.id, kind="revenue" if kind == "revenue" else kind if kind in ("score", "cond") else "blocker",
                       key=key or kind, value=value if kind != "blocker_clear" else None, quote=reason, evidence_type="internal_note",
                       how="manual review", reason=reason, by=c.actor, on=c.today))
    log(c, o.id, "h", f"Reviewed by {c.actor}: {msg} ({reason})")
    qualification_changed(c, o, what)
    return msg


def update_need(c: Ctx, opp_id: str, fields: dict) -> None:
    o = opp(c, opp_id, *QUAL_STAGES)
    allowed = ("team", "process", "problem", "impact", "target", "service")
    bad = set(fields) - set(allowed)
    if bad:
        raise ActionError(f"Unknown need fields: {sorted(bad)}. Use {allowed}.")
    o.need = {**(o.need or {}), **{k: (v or "").strip() for k, v in fields.items()}}
    log(c, o.id, "h", f"Client need statement edited by {c.actor}")
    qualification_changed(c, o, "Client need statement edited")


# ---------- Step 7: actions, tasks, packs ----------
def suggest_actions(c: Ctx, opp_id: str) -> int:
    o = opp(c, opp_id, *QUAL_STAGES)
    gaps = calculate(rules_input(o))["gaps"]
    taken = {t.title for t in c.s.scalars(select(m.Task).where(m.Task.opportunity_id == o.id))}
    items, _ = run_agent(c, "A4 Action and reporting", o, "a4-1.0", lambda: A.next_actions(gaps, taken))
    for x in c.s.scalars(select(m.Suggestion).where(m.Suggestion.opportunity_id == o.id, m.Suggestion.status == "open")):
        x.status = "dismissed"
    for it in items:
        c.s.add(m.Suggestion(id=new_id("sg"), opportunity_id=o.id, **it))
    log(c, o.id, "a", f"Action agent suggested {len(items)} next action{'s' if len(items) != 1 else ''} from the open gaps")
    return len(items)


def suggestion_to_task(c: Ctx, sug_id: str, title: str | None, side: str, assignee: str, due: date) -> str:
    sg = c.s.get(m.Suggestion, sug_id)
    if not sg or sg.status != "open":
        raise ActionError("Suggestion not found or already used.", 404, "not_found")
    if side not in ("CGS", "Client"):
        raise ActionError("Side must be CGS or Client.")
    o = opp(c, sg.opportunity_id)
    t = m.Task(id=new_id("t"), opportunity_id=o.id, title=(title or sg.title).strip(), side=side, assignee=need_text(assignee, "Owner"), due=due)
    c.s.add(t)
    sg.status = "converted"
    touch(c, o)
    log(c, o.id, "h", f"Task confirmed by {c.actor}: {t.title} ({side} side, {t.assignee}, {fmt(due)})")
    return t.id


def toggle_task(c: Ctx, task_id: str) -> str:
    t = c.s.get(m.Task, task_id)
    if not t:
        raise ActionError("Task not found.", 404, "not_found")
    t.status = "open" if t.status == "done" else "done"
    o = opp(c, t.opportunity_id)
    touch(c, o)  # progress, but never a score change
    log(c, o.id, "h", f"Task {'completed' if t.status == 'done' else 'reopened'} by {c.actor}: {t.title}")
    return t.status


def snooze(c: Ctx, opp_id: str, days: int) -> date:
    o = opp(c, opp_id)
    if not 1 <= days <= 30:
        raise ActionError("Snooze for 1 to 30 days.")
    o.snoozed_until = c.today + timedelta(days=days)
    log(c, o.id, "h", f"Stalled reminder snoozed by {c.actor} until {fmt(o.snoozed_until)}")
    return o.snoozed_until


def submit_pack(c: Ctx, opp_id: str, exception: str | None, revision: int | None) -> str:
    o = opp(c, opp_id, "active", "approval")
    check_revision(o, revision)
    old = open_pack(c, o)
    if o.stage == "approval" and (not old or not old.stale_reason):
        raise ActionError("A current pack is already waiting for approval.", 409, "pack_exists")
    r = calculate(rules_input(o))
    if r["readiness"] != "Ready" and not (exception and exception.strip()):
        raise ActionError(f"The rules say {r['readiness']}. Collect the missing evidence, or request an exception with a reason.", 400, "not_ready")
    if old:
        old.status = "superseded"
        exception = exception or old.exception
    snap = {"scores": dict(o.scores), "conditions": dict(o.conditions), "blocker": o.blocker, "revenue": o.revenue, "need": dict(o.need or {})}
    pk = m.Pack(id=new_id("pk"), opportunity_id=o.id, submitted_by=c.actor, submitted_on=c.today, snapshot=snap, rule_version=RULE_VERSION,
                exception=(exception or "").strip() or None)
    c.s.add(pk)
    o.stage, o.next_action, o.next_due = "approval", "Awaiting qualification approval", c.today + timedelta(days=2)
    touch(c, o)
    log(c, o.id, "h", f"Qualification pack {'resubmitted' if old else 'submitted'} by {c.actor}" + (f" with an exception: {pk.exception}" if pk.exception else ""))
    return pk.id


# ---------- Step 8 ----------
def decide_pack(c: Ctx, pack_id: str, b) -> None:
    pk = c.s.get(m.Pack, pack_id)
    if not pk:
        raise ActionError("Pack not found.", 404, "not_found")
    if pk.status != "open":
        raise ActionError(f"This pack is {pk.status}.", 409, "wrong_status")
    o = opp(c, pk.opportunity_id, "approval")
    if b.role not in APPROVER_ROLES:
        raise ActionError(f"Role must be one of {APPROVER_ROLES}.")
    rec = lambda decision, note="", **kw: c.s.add(m.Decision(opportunity_id=o.id, step=8, decision=decision, by=c.actor, role=b.role,
                                                             note=note, pack_id=pk.id, on=c.today, **kw))
    if b.decision == "approve":
        if pk.stale_reason:
            raise ActionError(f"This pack is out of date ({pk.stale_reason}). Return it so the owner can resubmit.", 409, "stale_pack")
        new_owner = b.new_owner or o.owner
        if new_owner not in owners_list(c):
            raise ActionError("The new owner must be a workspace user.")
        handover = new_owner != o.owner
        if handover and not b.handover_accepted:
            raise ActionError(f"{new_owner} has to accept the handover first. Until then {o.owner} stays responsible.", 400, "handover_not_accepted")
        rec("Approved with exception" if pk.exception else "Approved", f"Handed over from {o.owner} to {new_owner}; accepted by {new_owner}" if handover else "",
            handover_from=o.owner if handover else None, handover_to=new_owner if handover else None)
        log(c, o.id, "h", f"Approved as Qualification by {c.actor} ({b.role})" + (f"; handed over from {o.owner} to {new_owner}" if handover else ""))
        pk.status, o.stage, o.owner = "approved", "qualification", new_owner
        o.next_action, o.next_due = "Needs analysis meeting", c.today + timedelta(days=7)
        o.crm_sync = "sync_pending"
        log(c, o.id, "s", f"Insightly sync pending (CRM mode: {c.settings.crm_mode}). Nothing was written to Insightly.")
    elif b.decision == "return":
        q = need_text(b.questions, "What evidence is missing") if not pk.stale_reason else (b.questions or "Resubmit the updated pack")
        pk.status, o.stage = "returned", "active"
        o.next_action, o.next_due = q, b.due or c.today + timedelta(days=7)
        rec("Returned", q)
        log(c, o.id, "h", f"Returned at Step 8 by {c.actor}: {q}")
    elif b.decision == "nurture":
        pk.status, o.stage = "nurture", "nurture"
        o.next_action, o.next_due = "Revisit the opportunity", b.review_on or c.today + timedelta(days=60)
        rec("Moved to nurture", f"Review on {o.next_due.isoformat()}")
        log(c, o.id, "h", f"Moved to nurture at Step 8 by {c.actor}")
    else:
        if b.reason not in CLOSE_REASONS:
            raise ActionError(f"Reason must be one of {CLOSE_REASONS}.")
        pk.status, o.stage = "closed", "closed"
        rec("Closed", b.reason)
        log(c, o.id, "h", f"Closed by {c.actor}: {b.reason}")
    o.revision += 1


# ---------- learning loop ----------
def manual_direction_proposal(c: Ctx, field: str, scope: str, value, rationale: str) -> str:
    if field not in WHITELIST:
        raise ActionError(f"Only these fields can change: {WHITELIST}. Weights, budgets, models and rules cannot.", 400, "not_whitelisted")
    if scope not in ("theme", "consultant"):
        raise ActionError("Scope must be theme (shared) or consultant (personal).")
    base = current_direction(c, c.actor if scope == "consultant" else None)
    after = value.strip() if field == "problem" else [x.strip() for x in value if str(x).strip()] if isinstance(value, list) else [x.strip() for x in str(value).split(",") if x.strip()]
    if after == getattr(base, field):
        raise ActionError("Nothing changed. Edit the value first.")
    p = m.DirectionProposal(id=new_id("dp"), type="direction", field=field, scope=scope, origin="manual", title=f"Change {field.replace('_', ' ')}",
                            rationale=need_text(rationale, "A reason"), before=getattr(base, field), after=after, feedback_ids=[],
                            base_version=base.version, status="pending", created_on=c.today)
    c.s.add(p)
    return p.id


def analyze_feedback(c: Ctx, scope: str) -> dict:
    shared = scope != "consultant"
    base = current_direction(c, None if shared else c.actor)
    latest = {}
    for f in c.s.scalars(select(m.Feedback).order_by(m.Feedback.on)):
        latest[f.subject] = f
    eligible = [f for f in latest.values() if f.direction_version == base.version and
                (f.scope in ("theme", "cgs") if shared else f.scope == "consultant" and f.by == c.actor)]
    res, _ = run_agent(c, "Feedback agent", None, "manager-1.1", lambda: A.analyze_feedback(eligible, base.buyer_roles), subject=f"Direction {base.version}")
    created = None
    if res["proposal"]:
        dup = c.s.scalars(select(m.DirectionProposal).where(m.DirectionProposal.status == "pending", m.DirectionProposal.field == res["proposal"]["field"])).first()
        if dup:
            res["summary"] += " A matching proposal is already waiting, so no duplicate was created."
        else:
            pr = res["proposal"]
            created = new_id("dp")
            c.s.add(m.DirectionProposal(id=created, type="direction", field=pr["field"], scope="theme" if shared else "consultant", origin="ai",
                                        title=pr["title"], rationale=pr["rationale"], before=base.buyer_roles, after=pr["after"],
                                        feedback_ids=[f.id for f in eligible], base_version=base.version, status="pending", created_on=c.today))
            res["summary"] += " A proposal was created for approval."
    return {"eligible": len(eligible), "summary": res["summary"], "proposalId": created}


def decide_direction(c: Ctx, prop_id: str, approve: bool) -> str | None:
    p = c.s.get(m.DirectionProposal, prop_id)
    if not p:
        raise ActionError("Proposal not found.", 404, "not_found")
    if p.status != "pending":
        raise ActionError(f"This proposal was already {p.status}.", 409, "wrong_status")
    p.decided_by, p.decided_on = c.actor, c.today
    if not approve:
        p.status = "rejected"
        return None
    personal = p.scope == "consultant"
    target = current_direction(c, c.actor if personal else None)
    if p.type == "direction" and p.base_version and p.base_version != target.version:
        raise ActionError(f"This proposal was made on {p.base_version}, but the direction is now {target.version}. Reject it and create a new one.",
                          409, "stale_proposal")
    data = {f: list(getattr(target, f)) if isinstance(getattr(target, f), list) else getattr(target, f) for f in WHITELIST}
    if p.type == "exclude":
        if p.domain and p.domain not in data["excluded_domains"]:
            data["excluded_domains"].append(p.domain)
        change = f"Excluded {p.domain}"
    else:
        data[p.field] = p.after
        change = p.title
    if personal:
        n = c.s.scalar(select(func.count()).select_from(m.DirectionVersion).where(m.DirectionVersion.scope == "personal", m.DirectionVersion.owner == c.actor)) + 1
        base = target.version.split("-p")[0] if target.scope == "personal" else target.version
        version = f"{base}-{slug(c.actor)}-p{n}"
    else:
        nums = [int(v[1:]) for v in c.s.scalars(select(m.DirectionVersion.version).where(m.DirectionVersion.scope == "shared")) if re.fullmatch(r"v\d+", v)]
        version = f"v{max(nums) + 1}"
    c.s.add(m.DirectionVersion(version=version, scope="personal" if personal else "shared", owner=c.actor if personal else None, approved_by=c.actor,
                               approved_on=c.today, basis="mock", change=change, **data))
    p.status = "approved"
    record_run(c, "Workflow manager", None, "approve-direction", 0, subject=f"Direction {version}")
    return version


def owners_list(c: Ctx) -> list[str]:
    row = c.s.get(m.Setting, "owners")
    return list(row.value) if row else []
