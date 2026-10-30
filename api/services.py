"""Workspace actions: every change a person makes in Steps 1-8 and the learning loop.

Each function checks the workflow rules, writes the change, and appends history. The rules engine
(backend/rules.py) is the only thing that calculates scores. Agents only propose; people decide.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from datetime import date, datetime, timedelta, timezone
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api import crm_sync as CRM
from api import demo_agents as A
from api import jobs as J
from api import knowledge
from api import live_agents as L
from api.llm import LLMError, make_provider
from api import models as m
from api import toolkit as TK
from api.settings import Settings
from backend.rules import CONDITIONS, LABELS, REASONS, RULE_VERSION, WEIGHTS, calculate, validate_score

WHITELIST = ("problem", "signals", "buyer_roles", "exclusions", "excluded_domains", "company_size", "problems")
SIZE_KEYS = ("min_employees", "max_employees", "min_revenue_musd", "max_revenue_musd")


def problem_list(value) -> list[dict]:
    """Problems for the direction, edited by hand: [{"problem", "signals": [...], "buyer_roles": [...]}]; ids are given here."""
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
        raise ActionError('Give a list of problems, each {"problem": "...", "signals": [...], "buyer_roles": [...]}.', 400, "invalid_value")
    out = []
    for i, x in enumerate(value):
        text = str(x.get("problem", "")).strip()
        signals = [str(s).strip() for s in x.get("signals") or [] if str(s).strip()]
        roles = [str(s).strip() for s in x.get("buyer_roles") or [] if str(s).strip()]
        if len(text) < 10 or not signals:
            raise ActionError(f"Problem {i + 1} needs a problem text and at least one signal.", 400, "invalid_value")
        out.append({"id": f"p{i + 1}", "problem": text, "signals": signals, "buyer_roles": roles,
                    **{k: x[k] for k in ("solution", "passage_id", "quote") if x.get(k)}})
    return out


def size_range(value) -> dict:
    """A company size range from a proposal: {"max_employees": 5000, ...} (revenue in millions of US dollars). {} = no limit."""
    import json as _json
    if isinstance(value, str):
        try:
            value = _json.loads(value) if value.strip() else {}
        except ValueError:
            raise ActionError('Give the size range as JSON, for example {"min_employees": 100, "max_employees": 5000}.', 400, "invalid_value")
    if not isinstance(value, dict) or set(value) - set(SIZE_KEYS):
        raise ActionError(f"The size range may only have {SIZE_KEYS}.", 400, "invalid_value")
    out = {}
    for k in SIZE_KEYS:
        v = value.get(k)
        if v in (None, ""):
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise ActionError(f"{k} must be a number of 0 or more.", 400, "invalid_value")
        out[k] = int(v) if "employees" in k else float(v)
    for lo, hi in (("min_employees", "max_employees"), ("min_revenue_musd", "max_revenue_musd")):
        if lo in out and hi in out and out[lo] > out[hi]:
            raise ActionError(f"{lo} is larger than {hi}.", 400, "invalid_value")
    return out
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
    research_factory: Callable[[], Any] | None = None  # Steps 2-3 live research (Aubrey's pipeline)
    search_factory: Callable[[], Any] | None = None    # web search for Step 1 discovery
    mail_factory: Callable[[], Any] | None = None      # the mailbox (api/toolkit/mail.py); None = mail is off
    crm_factory: Callable[[], Any] | None = None       # the CRM (api/toolkit/crm.py); None = the CRM is off
    crm_dirty: set = field(default_factory=set)        # opportunities to sync to the CRM once this request is saved

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
            J.enqueue(c.s, c.today, "ready_reminder", o.id, "event", f"The rules now say Ready (Q {r['ql']:g})", requested_by="Rules engine")


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


def run_agent(c: Ctx, agent: str, o: m.Opportunity | None, prompt: str, fn, calls=1, subject="", live=None, live_prompt=None,
              trace: list | None = None):
    """Run an agent and log the run. Returns (result, run_id).

    Demo mode runs fn() (text rules). Live mode runs live(provider) when that agent has a real version;
    otherwise, or when the AI call fails, it records a failed run and raises. It never falls back to demo results.
    trace: a list a tool-using agent fills with its steps; it is saved with the run, also when the run fails.
    """
    if c.demo:
        return fn(), record_run(c, agent, o, prompt, calls, subject=subject)
    prompt = live_prompt or prompt
    p = None
    try:
        if live is None:
            A.require_demo(c.settings.app_mode, agent)
        p = provider(c)
        result, usage = live(p)
    except (A.AgentUnavailable, LLMError) as e:
        # a failed request may still use quota, so the calls already sent count toward the budget
        rid = record_run(c, agent, o, prompt, getattr(p, "calls", 0), "failed", str(e), subject, model=None)
        if trace:
            c.s.get(m.AgentRun, rid).trace = trace
        c.s.commit()  # keep the failure record even though the request fails
        code = "agent_unavailable" if isinstance(e, A.AgentUnavailable) else f"ai_{e.kind}"
        raise ActionError(str(e), 503, code)
    # the real number of AI requests (discovery makes two; the next-actions step with no open gaps makes none)
    rid = record_run(c, agent, o, prompt, getattr(p, "calls", 1), subject=subject, model=p.label, usage=usage)
    if trace:
        c.s.get(m.AgentRun, rid).trace = trace
    return result, rid


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


def shared_base(version: str) -> str:
    """The shared version a personal version builds on. Personal versions are named <shared>-<person>-p<n>, e.g. v4-alex-morgan-p2."""
    return version.split("-", 1)[0]


def latest_personal(c: Ctx, owner: str) -> m.DirectionVersion | None:
    return c.s.scalars(select(m.DirectionVersion).where(m.DirectionVersion.scope == "personal", m.DirectionVersion.owner == owner)
                       .order_by(m.DirectionVersion.created_at.desc())).first()


def current_direction(c: Ctx, personal_for: str | None = None) -> m.DirectionVersion:
    """The shared direction, or that person's personal version when it is built on the current shared version.
    A personal version built on an older shared version is never used, so shared changes (new exclusions) always reach everyone."""
    shared = c.s.scalars(select(m.DirectionVersion).where(m.DirectionVersion.scope == "shared")
                         .order_by(m.DirectionVersion.created_at.desc(), m.DirectionVersion.version.desc())).first()
    if personal_for and shared:
        p = latest_personal(c, personal_for)
        if p and shared_base(p.version) == shared.version:
            return p
    return shared


def personal_version_name(c: Ctx, shared_version: str, owner: str) -> str:
    n = c.s.scalar(select(func.count()).select_from(m.DirectionVersion).where(m.DirectionVersion.scope == "personal",
                                                                              m.DirectionVersion.owner == owner)) + 1
    return f"{shared_version}-{slug(owner)}-p{n}"


def merged_direction(old: m.DirectionVersion | None, mine: m.DirectionVersion, new: m.DirectionVersion) -> tuple[dict, list[str]]:
    """Carry one person's changes (mine compared with the shared version it was built on, old) over to the new shared version.
    Lists keep what the person added and drop what they removed; the problem text keeps the person's wording if they changed it.
    Returns (fields, names of the fields that still differ from the new shared version)."""
    data, kept = {}, []
    for f in WHITELIST:
        base, mine_v, new_v = getattr(old or new, f), getattr(mine, f), getattr(new, f)
        if f in ("problem", "company_size"):  # one value, not a list: keep the person's change if they made one
            data[f] = mine_v if mine_v != base else new_v
        else:
            base, mine_v, new_v = base or [], mine_v or [], new_v or []  # versions made before a list field existed hold null
            removed = [x for x in base if x not in mine_v]
            added = [x for x in mine_v if x not in base]
            data[f] = [x for x in new_v if x not in removed] + [x for x in added if x not in new_v]
        if data[f] != new_v:
            kept.append(f)
    return data, kept


def rebase_personal(c: Ctx, new_shared: m.DirectionVersion) -> list[str]:
    """After a shared change is approved, give each person with a personal direction a new personal version on top of it."""
    created = []
    owners = c.s.scalars(select(m.DirectionVersion.owner).where(m.DirectionVersion.scope == "personal").distinct()).all()
    for owner in sorted(o for o in owners if o):
        mine = latest_personal(c, owner)
        if not mine or shared_base(mine.version) == new_shared.version:
            continue
        data, kept = merged_direction(c.s.get(m.DirectionVersion, shared_base(mine.version)), mine, new_shared)
        if not kept:
            continue  # nothing personal is left; this person now simply follows the shared direction
        version = personal_version_name(c, new_shared.version, owner)
        c.s.add(m.DirectionVersion(version=version, scope="personal", owner=owner, approved_by=c.actor, approved_on=c.today,
                                   basis=new_shared.basis, change=f"Moved to {new_shared.version}; kept personal changes to {', '.join(kept)}", **data))
        c.s.flush()
        created.append(version)
    return created


def propose_exclusion(c: Ctx, target: str, domain: str, scope: str, why: str, fb: m.Feedback) -> None:
    scope = "theme" if scope == "opportunity" else scope
    c.s.add(m.DirectionProposal(id=new_id("dp"), type="exclude", target=target, domain=domain, scope=scope,
                                origin="feedback", rationale=why, feedback_ids=[fb.id], proposed_by=c.actor,
                                base_version=current_direction(c, c.actor if scope == "consultant" else None).version,
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


def latest_task(c: Ctx, o: m.Opportunity) -> m.ResearchTask:
    return c.s.scalars(select(m.ResearchTask).where(m.ResearchTask.opportunity_id == o.id).order_by(m.ResearchTask.started_at.desc())).first()


def run_research(c: Ctx, o: m.Opportunity) -> None:
    """Steps 2-3. Demo mode finishes at once from fixtures. Live mode starts Aubrey's research pipeline in the
    background (Gemini + Tavily); sync_research() collects the brief when it is done."""
    task = latest_task(c, o)
    if not c.demo:
        # save first: the web search and the research start below can take seconds, and SQLite lets only one
        # connection write at a time, so holding unsaved changes here would block the agents and other people
        c.s.commit()
        try:
            if not o.company.domain:
                from api import discovery_agent as D
                try:
                    found = D.resolve_domain(TK.web_for("Research agent", c), o.company.name)
                except LLMError:
                    found = ""
                if not found:
                    raise LLMError("The research agent needs the company website (domain), and a search did not find it. Add it, then retry.", "config")
                o.company.domain = found
                log(c, o.id, "a", f"Research agent found the company website: {found}. Check it is the right company.")
            TK.require("Research agent", "web", c.s)  # whichever engine runs the research, the permission table decides
            bridge = c.research_factory() if c.research_factory else None
            if bridge is None:
                raise A.AgentUnavailable("Research agent: not available in this server.")
            task.external_run_id = bridge.start(o.company.name, o.company.domain, c.actor, **({"as_of": c.today} if getattr(bridge, "dated", False) else {}),
                                                **({"focus": task.returned_with} if getattr(bridge, "focus", False) and task.returned_with else {}),
                                                **({"size": current_direction(c, c.actor).company_size or {},
                                                    "problem": o.target_problem or ""} if getattr(bridge, "criteria", False) else {}))
            task.status, task.error = "running", None
            log(c, o.id, "a", "Research agent started (Gemini + web search, up to about 8 minutes)" +
                (f". Reviewer question noted: {task.returned_with}" if task.returned_with else ""))
        except (LLMError, A.AgentUnavailable) as e:
            task.status, task.error = "failed", str(e)
            record_run(c, "Research agent", o, "research-v2 (Aubrey)", 0, "failed", str(e))
            log(c, o.id, "a", f"Research could not run: {e}")
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
        o.candidate_id = cand.id
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
        o.target_problem, o.candidate_id = cand.problem, cand.id
        run_research(c, o)
        created.append(o.id)
    return created


def recommend(c: Ctx, intent: str, budget=None) -> dict:
    """Step 1: a new round of up to 10 candidates. Demo: prepared list.
    Live: the tool-using discovery agent searches, reads and nominates; code checks every nomination (api/discovery_agent.py).
    budget(provider) -> may_call: the agents' daily AI budget, for rounds the agents start on their own."""
    from api import discovery_agent as D
    d = current_direction(c, c.actor)
    direction = {f: getattr(d, f) for f in WHITELIST}
    known = {D._norm(n) for n in c.s.scalars(select(m.Company.name))} | {D._norm(n) for n in c.s.scalars(select(m.Candidate.company))}

    def live(p):
        from api.search import search_config
        from api.settings import ROOT
        search = TK.web_for("Discovery agent", c)
        from api import discovery_memory as DM
        result, usage, steps = D.discover_with_agent(p, search, direction, intent, known, c.today, memory=DM.for_agent(c.s),
                                                     debug_file=ROOT / "runtime" / "discovery-last-round.json", config=search_config(),
                                                     may_call=budget(p) if budget else None,
                                                     crm=TK.crm_if_allowed("Discovery agent", c))
        trace.extend(steps)
        return result, usage

    trace: list = []
    (found, info), _ = run_agent(c, "Discovery agent", None, "discovery-demo-1.0", lambda: (A.demo_round(direction, known), {}),
                                 subject=f"Direction {d.version}", live=live, live_prompt=D.AGENT_VERSION, trace=trace)
    row = c.s.get(m.Setting, "round")
    rnd = (row.value if row else 0) + 1
    while c.s.get(m.Candidate, f"r{rnd}-1"):  # another round was saved while this one searched
        rnd += 1
    if row:
        row.value = rnd
    else:
        c.s.add(m.Setting(key="round", value=rnd))
    for i, x in enumerate(found):
        c.s.add(m.Candidate(id=f"r{rnd}-{i + 1}", round=rnd, company=x["company"], domain=x.get("domain", ""), person=x.get("person", ""),
                            title=x.get("title", ""), why_person=x.get("why_person", "Matches a buyer role in the research direction" if x.get("person") else "No named person in the source"),
                            why=x["why"], signal=x["signal"], signal_date=x["signal_date"], flags=x.get("flags", []), status="new",
                            direction_version=d.version, source_url=x.get("source_url"), evidence_quote=x.get("quote"),
                            problem_id=x.get("problem_id"), problem=x.get("problem"), source_query=x.get("source_query")))
    return {"round": rnd, "candidates": len(found), "checks": info}


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


def sync_research(c: Ctx) -> list[dict]:
    """Live mode: check running research and turn finished company cards into Step 4 briefs."""
    from api.research_bridge import card_to_brief
    out = []
    running = c.s.scalars(select(m.ResearchTask).where(m.ResearchTask.status == "running", m.ResearchTask.external_run_id.is_not(None))).all()
    if not running:
        return out
    bridge = c.research_factory() if c.research_factory else None
    if bridge is None:
        return out
    for task in running:
        o = c.s.get(m.Opportunity, task.opportunity_id)
        try:
            st = bridge.status(task.external_run_id)
        except Exception as e:  # one unreachable run must not stop the others; it is checked again next time
            out.append({"opportunityId": o.id, "status": "running", "error": f"Could not check the research run ({type(e).__name__}: {e})"})
            continue
        if hasattr(bridge, "uses"):  # the research thread's connector calls join this request's transaction
            TK.save_uses(c.s, bridge.uses(task.external_run_id))
        task.log = (task.log or [])[-20:] + [{"m": e} for e in st.get("events", []) if {"m": e} not in (task.log or [])]
        u = st.get("usage") or {}
        o.brief.usage = {"searches": u.get("searches", 0), "pages": u.get("extractions", 0), "calls": u.get("model_calls", 0)}
        if st["status"] in ("running", "stopping", "queued"):
            out.append({"opportunityId": o.id, "status": "running", "stage": st.get("stage")})
            continue
        brief = None
        try:
            card = bridge.result(task.external_run_id) if st["status"] in ("completed", "incomplete") else None
            if card:
                owner = o.owner or (c.actor if c.actor in owners_list(c) else "")
                brief, person = card_to_brief(card, owner, (c.today + timedelta(days=4)).isoformat(), u)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            st = {**st, "status": "failed", "error": f"The research result could not be read ({type(e).__name__}: {e}). Retry the research."}
        if brief:
            for k, v in brief.items():
                setattr(o.brief, k, v)
            if person and not o.contact.name:
                o.contact.name, o.contact.title = person["name"], person["title"]
            task.status, task.progress, o.stage = "done", 5, "review"
            o.revision += 1
            rid = record_run(c, "Research agent", o, getattr(bridge, "prompt", "research-v2 (Aubrey)"), u.get("model_calls", 0), model=getattr(bridge, "label", "gemini+tavily"),
                             usage=type("U", (), {"tokens_in": u.get("tokens_in"), "tokens_out": u.get("tokens_out")})())
            if hasattr(bridge, "trace"):  # the tool-using research agent: keep every step with the run
                c.s.get(m.AgentRun, rid).trace = bridge.trace(task.external_run_id) or None
            log(c, o.id, "a", f"Research agent finished the brief: {len(brief['facts'])} quoted sources" + (f", {len(brief['flags'])} flags" if brief["flags"] else ""))
            out.append({"opportunityId": o.id, "status": "review"})
        else:
            reason = st.get("error") or ("Paused at a research limit." if st["status"] == "paused" else "No reviewable brief was produced.")
            task.status, task.error = ("paused" if st["status"] == "paused" else "failed"), reason
            task.pause_reason = "budget" if st["status"] == "paused" else None
            rid = record_run(c, "Research agent", o, getattr(bridge, "prompt", "research-v2 (Aubrey)"), u.get("model_calls", 0), "failed", reason, model=getattr(bridge, "label", "gemini+tavily"))
            if hasattr(bridge, "trace"):
                c.s.get(m.AgentRun, rid).trace = bridge.trace(task.external_run_id) or None
            log(c, o.id, "a", f"Research ended without a brief: {reason}")
            out.append({"opportunityId": o.id, "status": task.status, "error": reason})
    return out


def set_domain(c: Ctx, opp_id: str, domain: str) -> None:
    o = opp(c, opp_id)
    d = need_text(domain, "Website domain").lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").strip("/")
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", d):
        raise ActionError("Give the website as a domain, for example corvane.com.")
    o.company.domain = d
    touch(c, o)
    log(c, o.id, "h", f"Company website set to {d} by {c.actor}")


def research_control(c: Ctx, opp_id: str, action: str) -> None:
    o = opp(c, opp_id, "researching")
    task = latest_task(c, o)
    bridge = c.research_factory() if (not c.demo and c.research_factory) else None
    if action == "stop":
        if task.status != "running":
            raise ActionError("Only running research can be stopped.", 409, "wrong_status")
        if bridge and task.external_run_id:
            bridge.stop(task.external_run_id)
        task.status, task.pause_reason = "paused", "user"
        log(c, o.id, "h", f"Research stopped by {c.actor}")
    elif action == "resume":
        if task.status != "paused":
            raise ActionError("Only paused research can be resumed.", 409, "wrong_status")
        task.budget_extended = task.pause_reason == "budget"
        task.status, task.pause_reason = "running", None
        log(c, o.id, "h", f"Research resumed by {c.actor}")
        if bridge and task.external_run_id:
            try:
                bridge.resume(task.external_run_id)
            except LLMError as e:
                task.status, task.error = "failed", str(e)
        else:
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
        CRM.mark(c, o)
        J.enqueue(c.s, c.today, "first_draft", o.id, "event", f"Accepted at Step 4 by {c.actor}: write the first email", requested_by=c.actor)
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
    CRM.mark(c, o)


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


def generate_draft(c: Ctx, opp_id: str, angle: str | None, tone: str) -> dict:
    o = opp(c, opp_id, "identified")
    if tone not in ("standard", "brief"):
        raise ActionError("Tone must be standard or brief.")
    b = o.brief
    contact = {"name": o.contact.name if o.contact else None, "title": o.contact.title if o.contact else "", "company": o.company.name}
    star = o.company_id == "corvane" and o.title.startswith("Scaling AI")
    trace: list[dict] = []

    def live(p):  # the tool-using outreach agent (api/outreach_agent.py)
        from api import outreach_agent as OA
        d, usage, steps = OA.write(c, o, p, purpose="first outreach email", angle=angle, tone=tone, sender=o.owner or c.actor)
        trace.extend(steps)
        return d, usage

    from api.outreach_agent import VERSION as OUTREACH_AGENT_VERSION
    draft, _ = run_agent(c, "Outreach agent", o, "outreach-demo-1.0",
                         lambda: A.demo_draft(star, contact["name"], o.company.name, o.owner or c.actor, b.facts, tone, angle),
                         live=live, live_prompt=OUTREACH_AGENT_VERSION, trace=trace)
    new_facts = draft.pop("new_facts", [])
    if new_facts:  # a person asked for this draft, so the verified sentences the agent added join the brief at once
        b.facts = b.facts + new_facts
        log(c, o.id, "a", f"Outreach agent added {len(new_facts)} newer fact{'s' if len(new_facts) != 1 else ''} to the brief: "
            + "; ".join(f"{f['id']} from {f['src']}" for f in new_facts))
    r = outreach_row(c, o)
    r.subject, r.body, r.angle, r.tone, r.status = draft["subject"], draft["body"], angle, tone, "draft"
    touch(c, o)
    log(c, o.id, "a", "Outreach agent wrote a draft from the research brief. Nothing was sent.")
    if new_facts:
        draft["newFacts"] = new_facts
    lib = knowledge.library().by_id
    synthetic = sorted({m.group(1) for m in re.finditer(r"\[\[([^|\]]+)\|", draft["body"]) if m.group(1) in lib and lib[m.group(1)].synthetic})
    warnings = []
    if synthetic:
        warnings.append(f"The draft cites a SYNTHETIC demo document ({', '.join(synthetic)}). Replace or remove that sentence before sending.")
        log(c, o.id, "s", warnings[-1])
    from api import related as REL
    crossing = REL.warning(REL.related(c.s, o, c.today))
    if crossing:
        warnings.append(crossing)
    return {**draft, "warnings": warnings} if warnings else draft


def alerts(c: Ctx, follow_days: int) -> list[dict]:
    """Progress monitor (S1): rules only, no AI. Stalled = no recorded progress for 14 days; it does not mean the client is inactive."""
    out = []
    for o in c.s.scalars(select(m.Opportunity).where(m.Opportunity.stage.in_(("identified", "contacted", "active", "approval")))):
        base = {"opportunityId": o.id, "company": o.company.name, "title": o.title, "owner": o.owner}
        if o.last_progress and (c.today - o.last_progress).days > 14 and not (o.snoozed_until and o.snoozed_until > c.today) and o.stage != "approval":
            out.append({**base, "type": "stalled", "message": f"No progress recorded for {(c.today - o.last_progress).days} days."})
        if o.next_due and o.next_due < c.today:
            out.append({**base, "type": "overdue", "message": f"Next step overdue since {o.next_due.isoformat()}: {o.next_action}"})
        r = c.s.get(m.Outreach, o.id)
        if o.stage == "contacted" and r and r.sent_on and not r.reply_on and (c.today - r.sent_on).days >= follow_days:
            out.append({**base, "type": "follow_up_due", "message": f"No reply {(c.today - r.sent_on).days} days after sending."})
        pk = open_pack(c, o)
        if pk and pk.stale_reason:
            out.append({**base, "type": "stale_pack", "message": f"Qualification pack out of date: {pk.stale_reason}."})
    return out


def mark_sent(c: Ctx, opp_id: str, via: str, on: date | None, follow_days: int) -> None:
    o = opp(c, opp_id, "identified")
    r = outreach_row(c, o)
    r.status, r.sent_on, r.sent_via = "sent", on or c.today, need_text(via, "Channel")
    o.stage, o.next_action, o.next_due = "contacted", "Follow up if no reply", r.sent_on + timedelta(days=follow_days)
    touch(c, o)
    log(c, o.id, "h", f"{via} sent by {c.actor}. The workspace did not send it.")
    CRM.mark(c, o)


# ---------- Step 5: email through the mail connector (api/toolkit/mail.py) ----------
EMAIL_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[A-Za-z]{2,}$")


def mailbox(c: Ctx, who: str = "Workspace (person)"):
    box = TK.metered(c.mail_factory() if c.mail_factory else None, who, "mail", c)
    if box is None:
        raise ActionError("Email is off. Set MAIL_PROVIDER=gmail (live mode) or mock in .env, then restart.", 409, "mail_off")
    return box


def plain_email(body: str) -> str:
    """The text the client receives: citation markers [[f2|phrase]] become the phrase alone."""
    return L._CITE.sub(lambda x: x.group(2), body)


def mail_hash(to: str, subject: str, body: str) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps([to.strip().lower(), subject, body]).encode()).hexdigest()[:16]


def follow_days_setting(c: Ctx) -> int:
    row = c.s.get(m.Setting, "follow_days")
    return int(row.value) if row else 7


def _mail_source(c: Ctx, o: m.Opportunity, item_id: str | None):
    """(purpose, subject, body, inbox item): the first email from the saved draft, or a follow-up/reconnect draft from the inbox."""
    if item_id:
        it = c.s.get(m.InboxItem, item_id)
        if not it or it.opportunity_id != o.id or it.kind != "draft":
            raise ActionError("That inbox item is not a draft for this opportunity.", 404, "not_found")
        if it.status not in ("new", "applied"):
            raise ActionError(f"That draft was {it.status}.", 409, "wrong_status")
        return it.data.get("purpose", "followup"), it.data.get("subject", ""), it.data.get("body", ""), it
    r = c.s.get(m.Outreach, o.id)
    if o.stage != "identified":
        raise ActionError("The first email was already sent. Send a follow-up from its inbox draft instead.", 409, "wrong_stage")
    if not r or not (r.subject or "").strip() or not (r.body or "").strip():
        raise ActionError("Write or generate the draft first.", 409, "no_draft")
    return "first", r.subject, r.body, None


def email_preview(c: Ctx, opp_id: str, to: str | None = None, item_id: str | None = None) -> dict:
    """Exactly what would go out, and the fingerprint a person confirms (seenHash)."""
    o = opp(c, opp_id, "identified", "contacted", "nurture")
    purpose, subject, body, _ = _mail_source(c, o, item_id)
    r = c.s.get(m.Outreach, o.id)
    to = (to or (r.to_email if r else None) or (o.contact.email if o.contact else None) or "").strip()
    lib = knowledge.library().by_id
    synthetic = sorted({x.group(1) for x in L._CITE.finditer(body) if x.group(1) in lib and lib[x.group(1)].synthetic})
    text = plain_email(body)
    return {"purpose": purpose, "to": to, "subject": subject, "body": text, "inThread": bool(r and r.mail_thread_id and purpose != "first"),
            "seenHash": mail_hash(to, subject, text),
            "blocked": (f"The draft cites a SYNTHETIC demo document ({', '.join(synthetic)}). Replace that sentence first." if synthetic else None)}


def record_mail_sent(c: Ctx, o: m.Opportunity, purpose: str, on: date | None = None, how: str = "") -> None:
    r = outreach_row(c, o)
    on = on or c.today
    fd = follow_days_setting(c)
    if purpose == "first":
        r.status, r.sent_on, r.sent_via = "sent", on, "Gmail"
        o.stage, o.next_action, o.next_due = "contacted", "Follow up if no reply", on + timedelta(days=fd)
    elif purpose == "followup":
        r.status, r.sent_on = "followup", on
        o.next_action, o.next_due = "Follow up if no reply", on + timedelta(days=fd)
    touch(c, o)
    log(c, o.id, "h", f"{purpose.capitalize()} email sent through Gmail{how} to {r.to_email}.")
    CRM.mark(c, o)


def email_out(c: Ctx, opp_id: str, to: str, mode: str, seen_hash: str, item_id: str | None = None) -> dict:
    """A person's click: put the email in their Gmail drafts (mode draft) or send it now (mode send).
    seen_hash must match email_preview, so exactly what the person looked at goes out."""
    from api.toolkit.mail import MailError
    if mode not in ("draft", "send"):
        raise ActionError("Mode must be draft or send.")
    box = mailbox(c)
    p = email_preview(c, opp_id, to, item_id)
    if p["blocked"]:
        raise ActionError(p["blocked"], 409, "synthetic")
    if not EMAIL_RE.match(p["to"]):
        raise ActionError("Give the client's email address.", 400, "no_address")
    if seen_hash != p["seenHash"]:
        raise ActionError("The email changed after you looked at it. Look at it again, then confirm.", 409, "changed")
    o = c.s.get(m.Opportunity, opp_id)
    r = outreach_row(c, o)
    _, _, _, it = _mail_source(c, o, item_id)
    if it is not None and it.status == "new":  # an inbox draft must still be valid before anything goes out
        from api import autonomy as AU
        AU.check_still_valid(c, it, o, it.payload_hash)
    try:
        if r.mail_draft_id and box.draft_exists(r.mail_draft_id):
            raise ActionError("A Gmail draft for this opportunity is waiting in Gmail. Send or delete it there first.", 409, "draft_waiting")
        thread = r.mail_thread_id if p["purpose"] != "first" else ""
        earlier = box.thread(thread) if thread else []
        reply_to = earlier[-1]["rfc_id"] if earlier else ""
        bcc = CRM.mail_bcc(c, o)  # the CRM lead's own address: the CRM logs the email on the lead
        if mode == "draft":
            res = box.create_draft(p["to"], p["subject"], p["body"], thread, reply_to, bcc=bcc)
        else:
            res = box.send(p["to"], p["subject"], p["body"], thread, reply_to, bcc=bcc)
    except MailError as e:
        raise ActionError(str(e), 502, "mail_failed") from None
    r.to_email, r.mail_thread_id = p["to"], res.get("thread_id") or r.mail_thread_id
    if o.contact and not o.contact.email:
        o.contact.email = p["to"]
    if it is not None and it.status == "new":
        AU.apply_item(c, it.id, "apply", it.payload_hash)
    if mode == "draft":
        r.mail_draft_id, r.mail_draft_purpose = res["draft_id"], p["purpose"]
        touch(c, o)
        log(c, o.id, "h", f"{c.actor} put the {p['purpose']} email in Gmail drafts for {p['to']}. Nothing was sent; "
                          "the workspace records it once it is sent from Gmail.")
        return {"mode": "draft", "to": p["to"], "draftId": res["draft_id"], "threadId": r.mail_thread_id}
    record_mail_sent(c, o, p["purpose"], how=f" by {c.actor}")
    return {"mode": "send", "to": p["to"], "messageId": res["message_id"], "threadId": r.mail_thread_id}


def sync_mail(c: Ctx) -> dict:
    """Rules, no AI: for every email thread, record drafts that were sent from Gmail and put new client replies in the
    owner's inbox. A reply is recorded only when a person applies it (an out-of-office message must not make a lead Active)."""
    from api.toolkit.mail import MailError
    box = TK.metered(c.mail_factory() if c.mail_factory else None, "Mail rules", "mail", c)
    out = {"checked": 0, "sent": 0, "replies": 0, "errors": []}
    if box is None:
        return {**out, "off": True}
    for r in list(c.s.scalars(select(m.Outreach).where(m.Outreach.mail_thread_id.is_not(None)))):
        o = c.s.get(m.Opportunity, r.opportunity_id)
        if not o or o.stage not in ("identified", "contacted", "nurture", "active", "approval"):  # qualification too
            continue
        out["checked"] += 1
        try:
            msgs = box.thread(r.mail_thread_id)
            if r.mail_draft_id and not box.draft_exists(r.mail_draft_id):
                purpose, r.mail_draft_id = r.mail_draft_purpose or "first", None
                mine = [x for x in msgs if x["ours"]]
                if mine and o.stage in QUAL_STAGES:  # sent during qualification: the stage and next action stay as they are
                    log(c, o.id, "a", f"{purpose.capitalize()} email sent through Gmail (seen in Gmail's Sent folder).")
                    out["sent"] += 1
                elif mine and (purpose != "first" or o.stage == "identified"):
                    record_mail_sent(c, o, purpose, on=min(date.fromisoformat(mine[-1]["date"]), c.today), how=" (seen in Gmail's Sent folder)")
                    out["sent"] += 1
                else:
                    log(c, o.id, "a", "The Gmail draft was deleted without being sent.")
            seen = {i.data.get("message_id") for i in c.s.scalars(select(m.InboxItem).where(
                m.InboxItem.opportunity_id == o.id, m.InboxItem.kind == "reply"))}
            for x in msgs:
                if x["ours"] or x["id"] in seen or not x["text"].strip():
                    continue
                it = J.to_inbox(c.s, c.today, None, "reply", f"Email reply from {x['from']} ({o.company.name})", x["text"][:4000],
                                {"message_id": x["id"], "from": x["from"], "on": x["date"], "text": x["text"][:4000]}, opp=o, agent="Mail rules")
                it.expires_on = None  # a client's reply stays until a person deals with it
                out["replies"] += 1
        except (MailError, TK.LimitReached) as e:
            out["errors"].append(f"{o.company.name}: {e}")
    return out


# ---------- connector permissions (api/toolkit): which agent may use which outside system ----------
def may_manage_permissions(c: Ctx) -> bool:
    return not c.settings.admins or c.actor in c.settings.admins


def set_permission(c: Ctx, agent: str, connector: str, enabled: bool | None, reason: str) -> dict:
    """A person switches a connector on or off for one agent (agent "*" = for every agent), or back to the default
    (enabled None). The code's ceiling always wins: what it forbids cannot be switched on. Every change is kept."""
    if not may_manage_permissions(c):
        raise ActionError(f"Only {', '.join(c.settings.admins)} can change agent permissions (CGS_ADMINS in .env).", 403, "wrong_role")
    reason = need_text(reason, "Reason")
    if len(reason) < 5:
        raise ActionError("Say why, in a few words.", 400, "no_reason")
    if connector not in TK.CONNECTORS:
        raise ActionError(f"Unknown connector: {connector}. Known: {', '.join(TK.CONNECTORS)}.", 404, "not_found")
    if agent != "*" and agent not in TK.CEILING:
        raise ActionError(f"Unknown agent: {agent}.", 404, "not_found")

    def now_state() -> str:
        if agent == "*":
            return "on" if TK.connector_detail(connector, c.s)["on"] else "off"
        return TK.state(agent, connector, c.s)

    if agent == "*":
        if not TK.CONNECTORS[connector][2]:
            raise ActionError(f"The {connector} connector is not built yet.", 409, "not_built")
    else:
        d = TK.detail(agent, connector, c.s)
        if not d["editable"]:
            why = "the code does not allow it" if d["state"] == "forbidden" else "that connector is not built yet"
            raise ActionError(f"{agent} cannot be given the {connector} connector: {why}.", 409, d["state"].replace(" ", "_"))
    before = now_state()
    row = c.s.get(m.ConnectorSwitch, (agent, connector))
    prev = "default" if row is None else ("on" if row.enabled else "off")
    if enabled is None:
        if row is not None:
            c.s.delete(row)
    elif row is None:
        c.s.add(m.ConnectorSwitch(agent=agent, connector=connector, enabled=enabled, changed_by=c.actor, changed_on=c.today, reason=reason))
    else:
        row.enabled, row.changed_by, row.changed_on, row.reason = enabled, c.actor, c.today, reason
    c.s.flush()
    after = now_state()
    who = "every agent" if agent == "*" else agent
    what = "back to the default" if enabled is None else ("on" if enabled else "off")
    c.s.add(m.PermissionChange(agent=agent, connector=connector, enabled=enabled, before=before, after=after, by=c.actor, on=c.today,
                               reason=reason, kind="switch", prev=prev))
    c.s.add(m.Activity(opportunity_id=None, on=c.today, actor_type="h",
                       text=f"{c.actor} switched the {connector} connector {what} for {who} ({before} -> {after}): {reason}"))
    warnings = []
    if connector == "mail" and after == "on" and not TK.client_data_allowed():
        warnings.append("The email tools also need CLIENT_DATA_TO_AI=allowed in .env before an agent can read client email.")
    if agent != "*" and after == "connector off":
        warnings.append(f"The {connector} connector is off for every agent; this switch applies once it is turned back on.")
    return {"agent": agent, "connector": connector, "before": before, "after": after, "warnings": warnings}


def _limit_label(n) -> str:
    return f"{n} per day" if n else "no limit"


def set_limit(c: Ctx, connector: str, per_day: int | None, reason: str) -> dict:
    """A person sets a connector's daily limit: a number, 0 for no limit, or None for the config file's default."""
    if not may_manage_permissions(c):
        raise ActionError(f"Only {', '.join(c.settings.admins)} can change agent permissions (CGS_ADMINS in .env).", 403, "wrong_role")
    reason = need_text(reason, "Reason")
    if len(reason) < 5:
        raise ActionError("Say why, in a few words.", 400, "no_reason")
    if connector not in TK.CONNECTORS or not TK.CONNECTORS[connector][2]:
        raise ActionError(f"Unknown or not built connector: {connector}.", 404, "not_found")
    if per_day is not None and (per_day < 0 or per_day > 100000):
        raise ActionError("A daily limit is a whole number from 0 (no limit) to 100000.", 400, "bad_limit")
    row = c.s.get(m.Setting, "connector_limits")
    current = dict((row.value if row else None) or {})
    prev = str(current[connector]) if connector in current else "default"
    before = _limit_label(TK.limits(c.s)[connector]["perDay"])
    if per_day is None:
        current.pop(connector, None)
    else:
        current[connector] = per_day
    if row is None:
        c.s.add(m.Setting(key="connector_limits", value=current))
    else:
        row.value = current
    c.s.flush()
    after = _limit_label(TK.limits(c.s)[connector]["perDay"])
    c.s.add(m.PermissionChange(agent="*", connector=connector, enabled=None, before=before, after=after, by=c.actor, on=c.today,
                               reason=reason, kind="limit", prev=prev))
    c.s.add(m.Activity(opportunity_id=None, on=c.today, actor_type="h",
                       text=f"{c.actor} set the {connector} connector's daily limit ({before} -> {after}): {reason}"))
    return {"connector": connector, "before": before, "after": after, "usedToday": TK.used_today(c.s, connector, c.today)}


def permission_history(c: Ctx, limit: int = 100) -> list[dict]:
    rows = c.s.scalars(select(m.PermissionChange).order_by(m.PermissionChange.id.desc()).limit(max(1, min(limit, 500))))
    return [{"id": x.id, "kind": x.kind or "switch", "agent": x.agent, "connector": x.connector,
             "set": (x.after if x.kind == "limit" else "default" if x.enabled is None else ("on" if x.enabled else "off")),
             "before": x.before, "after": x.after, "by": x.by, "on": x.on.isoformat(), "reason": x.reason, "undoable": x.prev is not None}
            for x in rows]


def undo_permission_change(c: Ctx, change_id: int, reason: str) -> dict:
    """Put a setting back as it was before one change. Only the latest change to that setting can be undone, so the
    history always reads in order; the undo is itself a new change in the history."""
    ch = c.s.get(m.PermissionChange, change_id)
    if ch is None:
        raise ActionError("Change not found.", 404, "not_found")
    kind = ch.kind or "switch"
    later = c.s.scalar(select(m.PermissionChange.id).where(
        m.PermissionChange.agent == ch.agent, m.PermissionChange.connector == ch.connector, m.PermissionChange.id > ch.id,
        (m.PermissionChange.kind == kind) if kind != "switch" else (m.PermissionChange.kind.is_(None) | (m.PermissionChange.kind == "switch")))
        .order_by(m.PermissionChange.id.desc()).limit(1))
    if later is not None:
        raise ActionError(f"A later change to this setting exists (#{later}). Undo that one first.", 409, "not_latest")
    if ch.prev is None:
        raise ActionError("This change cannot be undone automatically. Set it by hand.", 409, "no_prev")
    why = f"Undo of change #{ch.id}: {need_text(reason, 'Reason')}"
    if kind == "limit":
        return set_limit(c, ch.connector, None if ch.prev == "default" else int(ch.prev), why)
    return set_permission(c, ch.agent, ch.connector, {"on": True, "off": False, "default": None}[ch.prev], why)


def connector_usage(c: Ctx, days: int = 7) -> dict:
    """Calls per day, agent and connector (ok, failed, denied, limit), with each connector's limit and today's use."""
    from sqlalchemy import func as f
    days = max(1, min(days, 90))
    since = c.today - timedelta(days=days - 1)
    U = m.ConnectorUse
    rows = c.s.execute(select(U.on, U.agent, U.connector, U.outcome, f.count()).where(U.on >= since)
                       .group_by(U.on, U.agent, U.connector, U.outcome)).all()
    agg: dict[tuple, dict] = {}
    for on, agent, conn, outcome, n in rows:
        agg.setdefault((on, agent, conn), {"ok": 0, "failed": 0, "denied": 0, "limit": 0})[outcome] = n
    lim = TK.limits(c.s)
    return {"today": c.today.isoformat(), "days": days,
            "limits": [{"connector": k, **lim[k], "usedToday": TK.used_today(c.s, k, c.today)} for k, v in TK.CONNECTORS.items() if v[2]],
            "rows": [{"on": on.isoformat(), "agent": agent, "connector": conn, **v}
                     for (on, agent, conn), v in sorted(agg.items(), key=lambda x: (x[0][0], x[0][1], x[0][2]), reverse=True)]}


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
    CRM.mark(c, o)


def record_reply(c: Ctx, opp_id: str, text: str, on: date | None) -> dict:
    o = opp(c, opp_id, "contacted", "identified", "nurture")  # nurture: the client answered a reconnect email
    text = need_text(text, "Reply text")
    r = outreach_row(c, o)
    r.status, r.reply_on, r.reply_text = "replied", on or c.today, text
    c.s.add(m.Note(id=new_id("n"), opportunity_id=o.id, type="Email reply", on=r.reply_on, text=text, added_by=c.actor))
    o.stage = "active"  # any reply moves the opportunity to Active at once, whatever it says
    o.next_action, o.next_due = "Hold the first conversation", r.reply_on + timedelta(days=7)
    touch(c, o)
    log(c, o.id, "h", f"Reply recorded by {c.actor}; stage set to Active")
    CRM.mark(c, o)
    try:
        label, _ = run_agent(c, "Evidence agent", o, "reply-demo-1.4", lambda: A.label_reply(text),
                             live=lambda p: L.label_reply(p, text), live_prompt=L.LABEL_PROMPT_VERSION)
    except ActionError as e:
        o.reply_label = "pending"
        log(c, o.id, "s", f"Reply label pending ({e}). Classify by hand; the opportunity is Active either way.")
        return {"replyLabel": "pending", "warning": str(e)}
    o.reply_label = label
    log(c, o.id, "a", f"Evidence agent labeled the reply: {label}")
    notify_manager(c, o, "reply", f"The client replied ({label})")
    if label == "defer":
        J.enqueue(c.s, c.today, "defer_nurture", o.id, "event", "The client's reply says not now, but later", requested_by="Evidence agent")
    return {"replyLabel": label}


# ---------- Step 6: notes, proposals, reviews ----------
def add_note(c: Ctx, opp_id: str, type_: str, on: date | None, text: str, analyze: bool) -> dict:
    o = opp(c, opp_id, *QUAL_STAGES)
    note = m.Note(id=new_id("n"), opportunity_id=o.id, type=type_, on=on or c.today, text=need_text(text, "Notes"), added_by=c.actor)
    c.s.add(note)
    touch(c, o)
    log(c, o.id, "h", f"{type_} added by {c.actor}")
    CRM.mark(c, o)
    c.s.flush()
    if not analyze:
        notify_manager(c, o, "notes", f"New {type_.lower()} from {c.actor}")
        return {"noteId": note.id, "proposals": 0}
    c.s.commit()  # the original text is saved even if analysis fails
    star = o.company_id == "corvane" and o.title.startswith("Scaling AI")
    asks: list[dict] = []
    trace: list[dict] = []

    def live(p):  # the tool-using evidence agent (api/evidence_agent.py)
        from api import agent_runtime as R
        from api import evidence_agent as EA
        spec, record = EA.evidence_agent(c, o, note, asks)
        out = R.run(spec, p, EA.task(o))
        trace.extend(out.trace)
        if out.status != "done" and not record["kept"]:
            raise LLMError(f"The evidence agent stopped without an answer: {out.reason}", "invalid_output")
        return (record["kept"], record["dropped"]), out.usage

    from api.evidence_agent import VERSION as EVIDENCE_AGENT_VERSION
    (found, dropped_by_check), run = run_agent(
        c, "Evidence agent", o, "evidence-demo-1.4", lambda: (A.extract_evidence(note.text, star), {}), calls=2,
        live=live, live_prompt=EVIDENCE_AGENT_VERSION, trace=trace)
    note_text = {note.id: note.text}
    if c.demo:
        asks = A.people_to_find(note.text)
    existing = {(p.kind, p.key) for p in c.s.scalars(select(m.Proposal).where(m.Proposal.opportunity_id == o.id, m.Proposal.status != "rejected"))}
    kept, dropped = 0, 0
    for p in found:
        if star and (p["kind"], p["key"]) in existing:
            continue
        source_id = p.get("note_id") or note.id  # the agent may quote an earlier note that still applies
        if source_id not in note_text:
            earlier = c.s.get(m.Note, source_id)
            note_text[source_id] = earlier.text if earlier and earlier.opportunity_id == o.id else ""
        if p["type"] == "client_statement" and not A.quote_in_text(p["quote"], note_text[source_id]):
            dropped += 1  # an invented or altered quote never reaches a person
            continue
        c.s.add(m.Proposal(id=new_id("pr"), opportunity_id=o.id, note_id=source_id, agent_run_id=run, kind=p["kind"], key=p["key"],
                           value=p["value"], quote=p["quote"], why=p["why"], evidence_type=p["type"]))
        kept += 1
    dropped += dropped_by_check.get("quote_not_found", 0)
    others = {k: v for k, v in dropped_by_check.items() if k != "quote_not_found"}
    log(c, o.id, "a", f"Evidence agent proposed {kept} item{'s' if kept != 1 else ''} for review"
        + (f"; {dropped} dropped because the quote is not in the notes" if dropped else "")
        + (f"; also dropped: {others}" if others else ""))
    handoffs = []
    for ask in asks:
        if role_known(c, o.company_id, ask["role"]):
            continue
        job = J.hand_off(c.s, c.today, "person_lookup", o.id, f"Find the {ask['role']} at {o.company.name}, who must approve or decide",
                         {"role": ask["role"], "quote": ask["quote"]}, requested_by="Evidence agent", live=not c.demo)
        if job:
            handoffs.append(ask["role"])
    notify_manager(c, o, "notes", f"New {note.type.lower()} from {c.actor}")
    return {"noteId": note.id, "proposals": kept, "droppedForInvalidQuote": dropped, "droppedOther": others, "handoffs": handoffs}


ROLE_WORDS = {"ceo": "chief executive", "cfo": "chief financial", "coo": "chief operating", "cio": "chief information",
              "cto": "chief technology", "cdo": "chief digital"}


def job_title(role: str) -> str:
    """The job title alone: "CEO (approves spend)" or "CFO, who signs off" -> "CEO", "CFO"."""
    return re.split(r"\s*(?:[(\[,;:]|\s-\s|\s–\s|\swho\s|\sthat\s)", (role or "").strip(), maxsplit=1)[0].strip()


def role_matches(title: str, role: str) -> bool:
    title, role = (title or "").lower(), job_title(role).lower()
    words = ROLE_WORDS.get(role, role)
    return bool(title) and (role in title.split() or words in title or role in title)


def role_known(c: Ctx, company_id: str, role: str) -> bool:
    """True when the workspace already has a named contact with this role at the company."""
    return any(x.name and role_matches(x.title, role) for x in c.s.scalars(select(m.Contact).where(m.Contact.company_id == company_id)))


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
            try:
                lo, hi = int(value["low"]), int(value["high"])
            except (KeyError, TypeError, ValueError):
                raise ActionError("Revenue is {\"low\": ..., \"high\": ...} in USD, or null to clear the estimate.", 400, "invalid_value") from None
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
def suggest_actions(c: Ctx, opp_id: str, why: str = "", budget=None) -> int:
    """Step 7: up to 3 next actions. Demo: one per gap from a fixed list. Live: the manager's next-actions step (api/next_step_agent.py).
    why: what prompted the request (a stall, a returned pack). budget(provider) -> may_call, for work the agents start on their own."""
    from api import next_step_agent as NS
    from api import agent_runtime as R
    o = opp(c, opp_id, *QUAL_STAGES)
    rules = calculate(rules_input(o))
    gaps = rules["gaps"]
    taken = {t.title for t in c.s.scalars(select(m.Task).where(m.Task.opportunity_id == o.id))}
    approver_q = NS.approver_question(c, o)
    trace: list = []

    def live(p):
        if not gaps and not approver_q:
            return [], None  # nothing to close: no AI request
        spec, record = NS.next_step_agent(c, o, rules, approver_q, why)
        out = R.run(spec, p, NS.task(o), **({"may_call": budget(p)} if budget else {}))
        trace.extend(out.trace)
        if not record["kept"]:
            raise LLMError(f"The action agent suggested nothing that passes the checks ({out.reason or out.status}).", "invalid_output")
        return record["kept"], out.usage

    items, _ = run_agent(c, "Manager agent", o, "next-step-demo-1.0", lambda: A.next_actions(gaps, taken),
                         subject=o.company.name, live=live, live_prompt=NS.VERSION, trace=trace)
    save_suggestions(c, o, items, "Action agent")
    return len(items)


def save_suggestions(c: Ctx, o: m.Opportunity, items: list[dict], by: str) -> None:
    """The new next actions replace the open ones (at most three at a time)."""
    for x in c.s.scalars(select(m.Suggestion).where(m.Suggestion.opportunity_id == o.id, m.Suggestion.status == "open")):
        x.status = "dismissed"
    for it in items:
        c.s.add(m.Suggestion(id=new_id("sg"), opportunity_id=o.id, **{k: it[k] for k in ("title", "reason", "question", "gap_key")}))
    log(c, o.id, "a", f"{by} suggested {len(items)} next action{'s' if len(items) != 1 else ''} from the open gaps")


def notify_manager(c: Ctx, o: m.Opportunity, event: str, reason: str) -> None:
    """Something happened to an opportunity: when the manager is watching (shadow or on), it looks at it today."""
    if J.manager_mode(c.s, not c.demo) in ("shadow", "on"):
        J.triage(c.s, c.today, o.id, event, reason, requested_by=c.actor)


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
def check_approver_role(c: Ctx, o: m.Opportunity, role: str) -> None:
    """The role a person claims at Step 8 must fit who they are. There is no login yet, so this trusts the X-User name."""
    if role not in APPROVER_ROLES:
        raise ActionError(f"Role must be one of {APPROVER_ROLES}.")
    if role == "Lead owner" and c.actor != o.owner:
        raise ActionError(f"Only the lead owner ({o.owner}) acts as Lead owner. Choose Another consultant or Director of Operations.", 403, "wrong_role")
    if role == "Another consultant" and c.actor == o.owner:
        raise ActionError("You own this opportunity, so your role here is Lead owner.", 403, "wrong_role")
    directors = c.settings.directors
    if role == "Director of Operations" and directors and c.actor not in directors:
        raise ActionError(f"Only {', '.join(directors)} can act as Director of Operations (CGS_DIRECTORS in .env).", 403, "wrong_role")


def clear_handover(pk: m.Pack) -> None:
    pk.handover_to = pk.handover_by = pk.handover_role = None


def approve_pack(c: Ctx, o: m.Opportunity, pk: m.Pack, approver: str, role: str, new_owner: str) -> None:
    handover = new_owner != o.owner
    c.s.add(m.Decision(opportunity_id=o.id, step=8, decision="Approved with exception" if pk.exception else "Approved", by=approver, role=role,
                       note=f"Handed over from {o.owner} to {new_owner}; accepted by {new_owner}" if handover else "", pack_id=pk.id, on=c.today,
                       handover_from=o.owner if handover else None, handover_to=new_owner if handover else None))
    log(c, o.id, "h", f"Approved as Qualification by {approver} ({role})" + (f"; handed over from {o.owner} to {new_owner}, who accepted" if handover else ""))
    pk.status, o.stage, o.owner = "approved", "qualification", new_owner
    clear_handover(pk)
    o.next_action, o.next_due = "Needs analysis meeting", c.today + timedelta(days=7)
    o.crm_sync = "sync_pending"
    if CRM.writes_on(c):
        CRM.mark(c, o)
        log(c, o.id, "s", "Insightly sync pending: the lead is converted to an opportunity at the qualification stage once this is saved.")
    else:
        log(c, o.id, "s", f"Insightly sync pending (CRM mode: {c.settings.crm_mode}). Nothing was written to Insightly.")


def decide_pack(c: Ctx, pack_id: str, b) -> dict:
    pk = c.s.get(m.Pack, pack_id)
    if not pk:
        raise ActionError("Pack not found.", 404, "not_found")
    if pk.status != "open":
        raise ActionError(f"This pack is {pk.status}.", 409, "wrong_status")
    o = opp(c, pk.opportunity_id, "approval")
    check_approver_role(c, o, b.role)
    rec = lambda decision, note="", **kw: c.s.add(m.Decision(opportunity_id=o.id, step=8, decision=decision, by=c.actor, role=b.role,
                                                             note=note, pack_id=pk.id, on=c.today, **kw))
    out = {"status": b.decision}
    if b.decision == "approve":
        if pk.stale_reason:
            raise ActionError(f"This pack is out of date ({pk.stale_reason}). Return it so the owner can resubmit.", 409, "stale_pack")
        new_owner = b.new_owner or o.owner
        if new_owner not in owners_list(c):
            raise ActionError("The new owner must be a workspace user.")
        if new_owner != o.owner and c.actor != new_owner:
            # only the new owner can accept a handover; the approval waits for them (POST /api/packs/{id}/handover)
            pk.handover_to, pk.handover_by, pk.handover_role = new_owner, c.actor, b.role
            log(c, o.id, "h", f"Approved by {c.actor} ({b.role}) with a handover to {new_owner}. "
                              f"Waiting for {new_owner} to accept; until then {o.owner} stays responsible.")
            o.revision += 1
            return {"status": "handover_pending", "handoverTo": new_owner}
        approve_pack(c, o, pk, c.actor, b.role, new_owner)  # no handover, or the new owner is approving and so accepts
        out["status"] = "approved"
    elif b.decision == "return":
        q = need_text(b.questions, "What evidence is missing") if not pk.stale_reason else (b.questions or "Resubmit the updated pack")
        pk.status, o.stage = "returned", "active"
        o.next_action, o.next_due = q, b.due or c.today + timedelta(days=7)
        rec("Returned", q)
        log(c, o.id, "h", f"Returned at Step 8 by {c.actor}: {q}")
        J.ask_for_next_actions(c.s, c.today, o.id, "event", f"The approver returned the pack: {q}", requested_by=c.actor,
                               live=not c.demo, event="pack_returned")
        if J.manager_mode(c.s, not c.demo) == "shadow":
            notify_manager(c, o, "pack_returned", f"The approver returned the pack: {q}")
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
    if b.decision != "approve":
        clear_handover(pk)  # any other decision replaces an approval that was waiting for a handover
        CRM.mark(c, o)
    o.revision += 1
    return out


def answer_handover(c: Ctx, pack_id: str, accept: bool, reason: str | None) -> dict:
    """The new owner accepts the handover (the approval then takes effect) or declines it (the approver decides again)."""
    pk = c.s.get(m.Pack, pack_id)
    if not pk:
        raise ActionError("Pack not found.", 404, "not_found")
    if pk.status != "open" or not pk.handover_to:
        raise ActionError("No handover is waiting on this pack.", 409, "wrong_status")
    if c.actor != pk.handover_to:
        raise ActionError(f"Only {pk.handover_to} can accept or decline this handover.", 403, "not_new_owner")
    o = opp(c, pk.opportunity_id, "approval")
    if accept:
        if pk.stale_reason:
            raise ActionError(f"The pack changed after the approval ({pk.stale_reason}). The approver has to decide again.", 409, "stale_pack")
        approve_pack(c, o, pk, pk.handover_by, pk.handover_role, c.actor)
        result = {"status": "approved"}
    else:
        reason = need_text(reason, "A reason for declining")
        log(c, o.id, "h", f"{c.actor} declined the handover from {pk.handover_by}'s approval: {reason}. The pack waits for a new decision.")
        clear_handover(pk)
        result = {"status": "declined"}
    o.revision += 1
    return result


# ---------- learning loop ----------
def manual_direction_proposal(c: Ctx, field: str, scope: str, value, rationale: str) -> str:
    if field not in WHITELIST:
        raise ActionError(f"Only these fields can change: {WHITELIST}. Weights, budgets, models and rules cannot.", 400, "not_whitelisted")
    if scope not in ("theme", "consultant"):
        raise ActionError("Scope must be theme (shared) or consultant (personal).")
    base = current_direction(c, c.actor if scope == "consultant" else None)
    if field == "problem":
        if not isinstance(value, str):
            raise ActionError("The problem is one piece of text, not a list.", 400, "invalid_value")
        after = need_text(value, "The problem")
    elif field == "company_size":
        after = size_range(value)
    elif field == "problems":
        after = problem_list(value)
    elif isinstance(value, list):
        after = [str(x).strip() for x in value if str(x).strip()]
    else:
        after = [x.strip() for x in str(value).split(",") if x.strip()]
    if after == (getattr(base, field) or ({} if field == "company_size" else [] if field == "problems" else None)):
        raise ActionError("Nothing changed. Edit the value first.")
    p = m.DirectionProposal(id=new_id("dp"), type="direction", field=field, scope=scope, origin="manual", title=f"Change {field.replace('_', ' ')}",
                            rationale=need_text(rationale, "A reason"), before=getattr(base, field), after=after, feedback_ids=[],
                            base_version=base.version, proposed_by=c.actor, status="pending", created_on=c.today)
    c.s.add(p)
    return p.id


def propose_problems(c: Ctx, scope: str = "theme") -> dict:
    """The direction agent reads the CGS library and proposes the problems discovery should watch for. A person approves."""
    from api import agent_runtime as R
    from api import direction_agent as DA
    if scope not in ("theme", "consultant"):
        raise ActionError("Scope must be theme (shared) or consultant (personal).")
    if c.s.scalars(select(m.DirectionProposal).where(m.DirectionProposal.status == "pending", m.DirectionProposal.field == "problems")).first():
        raise ActionError("A proposal for the problems is already waiting. Approve or reject it first.", 409, "pending_proposal")
    base = current_direction(c, c.actor if scope == "consultant" else None)
    direction = {f: getattr(base, f) for f in WHITELIST}
    trace: list = []

    def live(p):
        spec, record = DA.direction_agent(direction, c)
        out = R.run(spec, p, DA.task())
        trace.extend(out.trace)
        if out.status != "done":
            raise LLMError(f"The direction agent did not finish ({out.reason}).", "invalid_output")
        return (record["problems"], out.result.get("summary", "")), out.usage

    (problems, summary), _ = run_agent(c, "Direction agent", None, "direction-demo-1.0", lambda: (DA.demo_problems(c), "Demo problems from the demo library."),
                                       subject=f"Direction {base.version}", live=live, live_prompt=DA.VERSION, trace=trace)
    p = m.DirectionProposal(id=new_id("dp"), type="direction", field="problems", scope=scope, origin="ai",
                            title="Problems to watch for, from the CGS library", rationale=summary or "Derived from the CGS library.",
                            before=base.problems or [], after=problems, feedback_ids=[], base_version=base.version,
                            proposed_by=c.actor, status="pending", created_on=c.today)
    c.s.add(p)
    return {"proposalId": p.id, "problems": problems, "summary": summary}


def analyze_feedback(c: Ctx, scope: str) -> dict:
    shared = scope != "consultant"
    base = current_direction(c, None if shared else c.actor)
    latest = {}
    for f in c.s.scalars(select(m.Feedback).order_by(m.Feedback.on)):
        latest[f.subject] = f
    eligible = [f for f in latest.values() if f.direction_version == base.version and
                (f.scope in ("theme", "cgs") if shared else f.scope == "consultant" and f.by == c.actor)]
    current = {f: getattr(base, f) for f in WHITELIST}
    res, _ = run_agent(c, "Direction agent", None, "manager-1.1", lambda: A.analyze_feedback(eligible, base.buyer_roles), subject=f"Direction {base.version}",
                       live=lambda p: L.analyze_feedback(p, eligible, current), live_prompt=L.FEEDBACK_PROMPT_VERSION)
    created = None
    if res["proposal"]:
        dup = c.s.scalars(select(m.DirectionProposal).where(m.DirectionProposal.status == "pending", m.DirectionProposal.field == res["proposal"]["field"])).first()
        if dup:
            res["summary"] += " A matching proposal is already waiting, so no duplicate was created."
        else:
            pr = res["proposal"]
            created = new_id("dp")
            c.s.add(m.DirectionProposal(id=created, type="direction", field=pr["field"], scope="theme" if shared else "consultant", origin="ai",
                                        title=pr["title"], rationale=pr["rationale"], before=getattr(base, pr["field"]), after=pr["after"],
                                        feedback_ids=pr.get("feedback_ids") or [f.id for f in eligible], base_version=base.version,
                                        proposed_by=c.actor, status="pending", created_on=c.today))
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
    owner = (p.proposed_by or c.actor) if personal else None  # a personal change belongs to the person who proposed it
    target = current_direction(c, owner)
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
        version = personal_version_name(c, shared_base(target.version), owner)
    else:
        nums = [int(v[1:]) for v in c.s.scalars(select(m.DirectionVersion.version).where(m.DirectionVersion.scope == "shared")) if re.fullmatch(r"v\d+", v)]
        version = f"v{max(nums) + 1}"
    new = m.DirectionVersion(version=version, scope="personal" if personal else "shared", owner=owner, approved_by=c.actor,
                             approved_on=c.today, basis="mock", change=change, **data)
    c.s.add(new)
    p.status = "approved"
    if not personal:
        c.s.flush()
        for v in rebase_personal(c, new):
            log(c, None, "s", f"Personal direction {v} created on {version}, keeping that person's own changes")
    record_run(c, "Workflow manager", None, "approve-direction", 0, subject=f"Direction {version}")
    return version


def owners_list(c: Ctx) -> list[str]:
    row = c.s.get(m.Setting, "owners")
    return list(row.value) if row else []
