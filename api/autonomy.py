"""Autonomous agents: they find work themselves and prepare it for people.

How agents find work (each piece of work is a job on the board, api/jobs.py):
  event    something happened: a reply says "not now", a pack was returned, the rules now say Ready
  patrol   once a day (and on demand) the patrol looks at every opportunity: no reply after a week,
           a nurture review date reached, no progress for two weeks, Ready but not submitted, weekly discovery and feedback
  handoff  an agent asks another for help: the evidence agent asks the research agent to find "the CFO who approves";
           the research agent finds news on a deferred client and asks the outreach agent for a reconnect draft

What agents may do on their own: prepare. Drafts, facts, contacts, suggestions and reminders go to a person's inbox.
Nothing is sent, no score changes, nothing is approved and nothing is written to Insightly until a person applies it.
Brakes: a daily AI call budget, a cap on handoffs per opportunity per day and on chain length, and a pause switch.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import re
import threading
import time

from sqlalchemy import select

from api import agent_runtime as R
from api import jobs as J
from api import live_agents as L
from api import manager_agent as MA
from api import outreach_agent as OA
from api import research_agent as RA
from api import models as m
from api import services as S
from api.llm import LLMError

MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")
AGENTS_ACTOR = "Agents"


def review_date(text: str, today: date) -> date:
    """The first of the month the client named for reconnecting ("reconnect in April"), else the last month named, else 90 days."""
    names = "|".join(x.capitalize() for x in MONTHS)  # capitalized, so the verb "may" is not a month
    mt = re.search(rf"(?i:reconnect|talk again|get back)\w*\s+(?i:in|after|from)\s+({names})\b", text) or \
        ([None] + list(re.finditer(rf"\b({names})\b", text)))[-1]
    if not mt:
        return today + timedelta(days=90)
    month = MONTHS.index(mt.group(1).lower()) + 1
    return date(today.year if month > today.month else today.year + 1, month, 1)


# ---------- context for work done without a person in the room ----------
def agent_ctx(app, s) -> S.Ctx:
    from api.actions import demo_today, research_bridge
    return S.Ctx(s=s, actor=AGENTS_ACTOR, today=demo_today(s, app.state.settings), settings=app.state.settings,
                 provider_factory=getattr(app.state, "provider_factory", None), research_factory=lambda: research_bridge(app),
                 search_factory=getattr(app.state, "search_factory", None))


def web(c: S.Ctx):
    if c.search_factory:
        return c.search_factory()
    from api.search import make_search
    return make_search(as_of=c.today)


def company_pages(c: S.Ctx, company: str, domain: str, query: str = "", since: date | None = None) -> list[dict]:
    """Pages about one company: the synthetic internet gives its pages directly; a real search service is asked."""
    w = web(c)
    if hasattr(w, "pages_for"):
        pages = w.pages_for(company, domain)
    else:
        pages = w.search(f"{company} {query}".strip(), since, news=bool(since))
    return [p for p in pages if not since or (p.get("published") and p["published"] > since.isoformat())] if since else pages


# ---------- the patrol ----------
def recent(c: S.Ctx, kind: str, opp_id: str | None, since: date) -> bool:
    return c.s.scalars(select(m.AgentJob).where(m.AgentJob.kind == kind, m.AgentJob.opportunity_id == opp_id, m.AgentJob.created_on >= since,
                                                m.AgentJob.status.in_(("queued", "running", "done")))).first() is not None


def patrol(c: S.Ctx) -> list[m.AgentJob]:
    """Look at every opportunity and put any work that is due on the board. Rules only: no AI calls here."""
    t, s = c.today, c.s
    follow, stall = int(J.setting(s, "follow_days")), int(J.setting(s, "stall_days"))
    found = []
    J.expire_inbox(s, t)

    def add(kind, o, reason, payload=None):
        job = J.enqueue(s, t, kind, o.id if o else None, "patrol", reason, payload, requested_by="Patrol")
        if job:
            found.append(job)

    mode = J.setting(s, "manager_mode")
    if mode in ("shadow", "on") and not c.demo:  # the manager needs a real AI model; it is queued first, so it decides before today's rule work runs
        add("manager_review", None, f"Daily review by the manager agent ({mode} mode)")
    for o in s.scalars(select(m.Opportunity)):
        if o.stage == "contacted":
            r = s.get(m.Outreach, o.id)
            if r and r.sent_on and not r.reply_on and (t - r.sent_on).days >= follow and not recent(c, "followup_draft", o.id, r.sent_on + timedelta(days=1)):
                add("followup_draft", o, f"No reply {(t - r.sent_on).days} days after the email of {S.fmt(r.sent_on)}")
        elif o.stage == "nurture" and o.next_due and o.next_due <= t and not recent(c, "research_refresh", o.id, o.next_due):
            add("research_refresh", o, f"Nurture review date reached ({S.fmt(o.next_due)}): check for news")
        elif o.stage == "active":
            snoozed = o.snoozed_until and o.snoozed_until > t
            if o.last_progress and (t - o.last_progress).days >= stall and not snoozed and not recent(c, "gap_actions", o.id, t - timedelta(days=stall)):
                add("gap_actions", o, f"No progress recorded for {(t - o.last_progress).days} days")
            if S.calculate(S.rules_input(o))["readiness"] == "Ready" and not S.open_pack(c, o) and not recent(c, "ready_reminder", o.id, t - timedelta(days=7)):
                add("ready_reminder", o, "The rules say Ready and the pack is not submitted")
    last = J.setting(s, "last_discovery")
    if not last or (t - date.fromisoformat(last)).days >= 7:
        add("discovery_round", None, "Weekly discovery round")
    last = J.setting(s, "last_feedback_review")
    if not last or (t - date.fromisoformat(last)).days >= 7:
        add("feedback_review", None, "Weekly review of consultant feedback")
    J.set_setting(s, "last_patrol", t.isoformat())
    s.add(m.Activity(opportunity_id=None, on=t, actor_type="a", text=f"Patrol: {len(found)} new piece{'s' if len(found) != 1 else ''} of work for the agents"))
    return found


# ---------- what each agent does with a job ----------
def _first(name: str | None) -> str:
    return (name or "there").split()[0]


def may_call(c: S.Ctx, p):
    """Agents working on their own stop when today's AI budget is used up, also in the middle of a task."""
    budget = int(J.setting(c.s, "agent_ai_budget"))
    return lambda: J.ai_calls_today(c.s, c.today) + p.calls < budget


def write_draft(c, o, p, trace, **kw):
    """The tool-using outreach agent (api/outreach_agent.py), for drafts the agents prepare on their own."""
    draft, usage, steps = OA.write(c, o, p, sender=o.owner or "CGS Advisors", may_call=may_call(c, p), **kw)
    trace.extend(steps)
    return draft, usage


def do_followup_draft(c, job, o):
    if o.stage != "contacted":
        return "skipped", f"No longer waiting for a reply (stage {o.stage})."
    r = c.s.get(m.Outreach, o.id)
    b = o.brief
    contact = {"name": o.contact.name if o.contact else None, "title": o.contact.title if o.contact else "", "company": o.company.name}
    angle = (f"A short follow-up to the first email sent on {r.sent_on.isoformat()} (subject: {r.subject or 'introduction'}). "
             "Do not repeat the first email. One new reason to talk, then offer a 20-minute call.")
    fact = (b.facts or [{"id": "f1", "text": o.company.name}])[0]
    trace: list[dict] = []
    draft, _ = S.run_agent(c, job.agent, o, "a1-followup-demo-1.0",
                           lambda: {"subject": f"Re: {r.subject or o.title}",
                                    "body": f"Hi {_first(contact['name'])}, I wanted to follow up on my note from {S.fmt(r.sent_on)}. "
                                            f"Since [[{fact['id']}|{fact['text'][:90]}]], would a 20-minute call in the next two weeks be useful? "
                                            f"{o.owner or 'CGS Advisors'}, CGS Advisors"},
                           live=lambda p: write_draft(c, o, p, trace, purpose="follow-up email", angle=angle, tone="brief"),
                           live_prompt=OA.VERSION + "+followup", trace=trace)
    J.to_inbox(c.s, c.today, job, "draft", f"Follow-up draft for {contact['name'] or 'the contact'} at {o.company.name}", draft["body"],
               {"subject": draft["subject"], "body": draft["body"], "purpose": "followup", "facts": draft.get("new_facts", [])}, opp=o)
    return "done", "Follow-up draft is in the owner's inbox. Nothing was sent."


def do_reconnect_draft(c, job, o):
    facts = job.payload.get("facts") or []
    if not facts:
        return "skipped", "No new facts to build on."
    contact = {"name": o.contact.name if o.contact else None, "title": o.contact.title if o.contact else "", "company": o.company.name}
    angle = "The client asked to talk again later. Reconnect by citing the new development; keep it short and offer a short call."
    f = facts[0]
    trace: list[dict] = []
    draft, _ = S.run_agent(c, job.agent, o, "a1-reconnect-demo-1.0",
                           lambda: {"subject": f"{o.company.name}: {f['text'][:60]}",
                                    "body": f"Hi {_first(contact['name'])}, I saw that [[{f['id']}|{f['text'][:100]}]]. When we spoke you suggested "
                                            f"reconnecting around now. Would a short call be useful? {o.owner or 'CGS Advisors'}, CGS Advisors"},
                           live=lambda p: write_draft(c, o, p, trace, purpose="reconnect email", angle=angle, tone="standard", extra_facts=facts),
                           live_prompt=OA.VERSION + "+reconnect", trace=trace)
    J.to_inbox(c.s, c.today, job, "draft", f"Reconnect draft for {contact['name'] or 'the contact'} at {o.company.name}", draft["body"],
               {"subject": draft["subject"], "body": draft["body"], "purpose": "reconnect", "facts": facts + draft.get("new_facts", [])}, opp=o)
    return "done", "Reconnect draft is in the owner's inbox. Nothing was sent."


def _known_since(o) -> date:
    dated = [f["date"] for f in (o.brief.facts or []) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(f.get("date", "")))]
    return date.fromisoformat(max(dated)) if dated else (o.last_progress or date.today()) - timedelta(days=30)


def do_research_refresh(c, job, o):
    since = _known_since(o)
    known = {f.get("src") for f in o.brief.facts or []}
    pages = [p for p in company_pages(c, o.company.name, o.company.domain, "news", since) if p["url"] not in known]
    if not pages:
        S.record_run(c, job.agent, o, "refresh-rules", 0, subject=o.company.name, model="rules")
        J.to_inbox(c.s, c.today, job, "reminder", f"Nurture review due for {o.company.name}: no news since {S.fmt(since)}",
                   "The research agent found nothing new. Decide whether to reconnect anyway or set a new review date.", opp=o)
        return "done", f"No news since {since.isoformat()}."

    def demo():
        return [{"text": re.split(r"(?<=\.)\s", p["content"], 1)[0], "quote": re.split(r"(?<=\.)\s", p["content"], 1)[0],
                 "src": p["url"], "date": p["published"] or "undated"} for p in pages[:3]]

    def live(p):
        from api.llm import Usage
        from api.mock_research import research
        from api.research_bridge import card_to_brief
        card, counts = research(p, pages, o.company.name, o.company.domain)
        brief, _ = card_to_brief(card, o.owner or "", "", counts)
        return [{k: f[k] for k in ("text", "quote", "src", "date")} for f in brief["facts"]], Usage(counts.get("tokens_in"), counts.get("tokens_out"))

    found, _ = S.run_agent(c, job.agent, o, "refresh-demo-1.0", demo, live=live, live_prompt="research-refresh-1.0")
    n = len(o.brief.facts or [])
    facts = [{"id": f"f{n + i + 1}", **f} for i, f in enumerate(found)]
    if not facts:
        J.to_inbox(c.s, c.today, job, "reminder", f"Nurture review due for {o.company.name}: news found, but nothing usable",
                   "The research agent read new pages, but no quote passed the checks.", opp=o)
        return "done", "New pages, no usable facts."
    J.to_inbox(c.s, c.today, job, "facts", f"{len(facts)} new fact{'s' if len(facts) != 1 else ''} about {o.company.name}",
               "\n".join(f"[{f['date']}] {f['text']}" for f in facts), {"facts": facts}, opp=o)
    if o.stage == "nurture":
        J.enqueue(c.s, c.today, "reconnect_draft", o.id, "handoff", f"News on a deferred client: {facts[0]['text'][:120]}",
                  {"facts": facts}, requested_by=job.agent, parent=job)
    found_text = f"{len(facts)} new fact{'s' if len(facts) != 1 else ''}"
    return "done", f"{found_text}; asked A1 for a reconnect draft." if o.stage == "nurture" else f"{found_text}."


_NAME_TITLE = re.compile(r"([A-Z][a-z]+(?: [A-Z][a-z]+)+), ([^,.]+)")


def do_person_lookup(c, job, o):
    role = job.payload.get("role", "")
    if S.role_known(c, o.company_id, role):
        return "skipped", f"The {role} is already a contact."
    asked = job.payload.get("quote", "") or job.reason

    def demo():  # demo mode: a text rule over the company's pages, no AI
        for p in company_pages(c, o.company.name, o.company.domain, f"{role} leadership"):
            for name, title in _NAME_TITLE.findall(p["content"]):
                if S.role_matches(title, role):
                    return {"found": True, "name": name, "title": title.strip(), "url": p["url"], "quote": f"{name}, {title}"}
        return {"found": False}

    trace: list[dict] = []

    def live(p):  # live mode: the tool-using research agent decides where to look (api/research_agent.py)
        spec = RA.person_agent(c, o.company_id, o.company.name, o.company.domain, role)
        out = R.run(spec, p, RA.task(o.company.name, o.company.domain, role, asked), may_call=may_call(c, p))
        trace.extend(out.trace)
        return (out.result if out.status == "done" else {"found": False, "notes": out.reason}), out.usage

    res, _ = S.run_agent(c, job.agent, o, "person-demo-1.0", demo, live=live, live_prompt=RA.VERSION, trace=trace)
    if not res.get("found"):
        checked = f" What the agent checked: {res['notes']}" if res.get("notes") else ""
        J.to_inbox(c.s, c.today, job, "reminder", f"Could not find the {role} at {o.company.name}",
                   f"The notes say: \"{asked}\". No public page names the {role}. Ask the client contact who approves.{checked}", opp=o)
        return "done", f"No page names the {role}."
    J.to_inbox(c.s, c.today, job, "contact", f"{role} at {o.company.name}: {res['name']} ({res['title']})",
               f"Why: the notes say \"{asked}\". Source: {res['url']}", {**res, "role": role}, opp=o)
    return "done", f"Found {res['name']} ({res['title']}); the owner confirms before it is added."


def do_gap_actions(c, job, o):
    if o.stage not in S.QUAL_STAGES:
        return "skipped", f"Stage is {o.stage}."
    n = S.suggest_actions(c, o.id, why=job.reason, budget=lambda p: may_call(c, p))
    if not n:
        if job.trigger == "event":  # a returned pack: the approver's question is the gap even when the rules show none
            J.to_inbox(c.s, c.today, job, "actions", f"Pack returned for {o.company.name}: answer the approver", job.reason,
                       {"why": job.reason}, opp=o)
            return "done", "No open gaps in the rules; the approver's question is in the owner's inbox."
        return "done", "No open gaps."
    titles = [x.title for x in c.s.scalars(select(m.Suggestion).where(m.Suggestion.opportunity_id == o.id, m.Suggestion.status == "open"))]
    J.to_inbox(c.s, c.today, job, "actions", f"{n} next action{'s' if n != 1 else ''} suggested for {o.company.name}",
               "\n".join(f"- {x}" for x in titles), {"why": job.reason}, opp=o)
    return "done", f"{n} suggestion{'s' if n != 1 else ''}; the owner turns them into tasks."


def do_defer_nurture(c, job, o):
    if o.stage not in ("contacted", "active"):
        return "skipped", f"Stage is {o.stage}."
    text = "\n".join([x.text for x in c.s.scalars(select(m.Note).where(m.Note.opportunity_id == o.id).order_by(m.Note.on))])
    when = review_date(text, c.today)
    J.to_inbox(c.s, c.today, job, "nurture", f"{o.company.name} said not now: move to nurture until {S.fmt(when)} {when.year}?",
               "The client deferred. The research agent will check for news on that date.", {"review_on": when.isoformat()}, opp=o)
    S.record_run(c, job.agent, o, "defer-rules-1.0", 0, subject=o.company.name, model="rules")
    return "done", f"Suggested nurture until {when.isoformat()}."


def do_ready_reminder(c, job, o):
    r = S.calculate(S.rules_input(o))
    if o.stage != "active" or r["readiness"] != "Ready" or S.open_pack(c, o):
        return "skipped", "No longer Ready, or already submitted."
    J.to_inbox(c.s, c.today, job, "ready", f"Ready to submit: {o.company.name}",
               f"Q {r['ql']:g}, coverage {r['coverage']}%. Submit the qualification pack for Step 8.", opp=o)
    S.record_run(c, job.agent, o, "ready-rules-1.0", 0, subject=o.company.name, model="rules")
    return "done", "Reminder in the owner's inbox."


def do_discovery_round(c, job, o):
    res = S.recommend(c, "Weekly round by the discovery agent", budget=lambda p: may_call(c, p))
    J.set_setting(c.s, "last_discovery", c.today.isoformat())
    if not res["candidates"]:
        return "done", f"Round {res['round']}: no new candidates this week."
    J.to_inbox(c.s, c.today, job, "candidates", f"{res['candidates']} new candidate{'s' if res['candidates'] != 1 else ''} (round {res['round']})",
               "The discovery agent's weekly round. Pick or skip each one in Step 1.", {"round": res["round"]}, owner=None)
    return "done", f"Round {res['round']}: {res['candidates']} candidates."


def do_feedback_review(c, job, o):
    res = S.analyze_feedback(c, "theme")
    J.set_setting(c.s, "last_feedback_review", c.today.isoformat())
    if res.get("proposalId"):
        J.to_inbox(c.s, c.today, job, "direction", "Direction change proposed by the feedback agent", res["summary"],
                   {"proposalId": res["proposalId"]}, owner=None)
    return "done", res["summary"][:300]


def do_manager_review(c, job, o):
    """The manager agent's daily review (api/manager_agent.py). Shadow mode records its decisions; on mode carries them out."""
    mode = J.setting(c.s, "manager_mode")
    if mode not in ("shadow", "on"):
        return "skipped", "The manager agent is off."
    trace: list[dict] = []
    record: dict = {"decisions": []}

    def live(p):
        spec, rec = MA.manager_agent(c, mode)
        record.update(rec)
        out = R.run(spec, p, MA.task(c.today), may_call=may_call(c, p))
        trace.extend(out.trace)
        return (out.result or {"summary": out.reason}), out.usage

    result, run_id = S.run_agent(c, job.agent, None, "manager-demo", lambda: {"summary": "The manager agent needs a real AI model."},
                                 subject="Daily review", live=live, live_prompt=MA.VERSION, trace=trace)
    for d in record["decisions"]:
        c.s.add(m.ManagerDecision(on=c.today, mode=mode, run_id=run_id, **d))
    ds = record["decisions"]
    counts = {k: sum(d["outcome"] == k for d in ds) for k in ("recorded", "queued", "already_queued", "sent", "refused")}
    shown = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items() if v) or "no decisions"
    return "done", f"{mode.capitalize()} mode: {shown}. {result.get('summary', '')}"[:300]


HANDLERS = {"followup_draft": do_followup_draft, "reconnect_draft": do_reconnect_draft, "research_refresh": do_research_refresh,
            "person_lookup": do_person_lookup, "gap_actions": do_gap_actions, "defer_nurture": do_defer_nurture,
            "ready_reminder": do_ready_reminder, "discovery_round": do_discovery_round, "feedback_review": do_feedback_review,
            "manager_review": do_manager_review}
assert set(HANDLERS) == set(J.KINDS)


# ---------- running the board ----------
def uses_ai(c: S.Ctx, kind: str) -> bool:
    return J.KINDS[kind][1] and not c.demo


def ai_budget_left(c: S.Ctx) -> bool:
    return J.ai_calls_today(c.s, c.today) < int(J.setting(c.s, "agent_ai_budget"))


def run_job(app, job_id: str) -> str:
    """Run one queued job. Returns its new status: done | skipped | failed | queued (waiting for the AI budget or a retry)
    | running (another worker already took it)."""
    with app.state.db.session() as s:
        c = agent_ctx(app, s)
        job = s.get(m.AgentJob, job_id)
        if not job or job.status != "queued":
            return job.status if job else "missing"
        if not J.due(job.retry_at):
            return "queued"
        if uses_ai(c, job.kind) and not ai_budget_left(c):
            job.result = "Waiting: today's AI budget is used up. It runs tomorrow."
            return "queued"
        claimed = J.claim(s, job_id)
        s.refresh(job)
        if not claimed:
            return job.status
        o = s.get(m.Opportunity, job.opportunity_id) if job.opportunity_id else None
        try:
            status, result = HANDLERS[job.kind](c, job, o)
            J.finish(job, status, result)
            if o:
                S.log(c, o.id, "a", f"{job.agent}: {result}")
        except S.ActionError as e:
            if e.code == "ai_temporary" and job.attempts < 3:
                J.retry_later(job, f"Temporary AI error, will retry: {e}")
            else:
                J.finish(job, "failed", error=str(e))
                if o:
                    S.log(c, o.id, "a", f"{job.agent} could not finish: {e}")
        except LLMError as e:
            J.finish(job, "failed", error=str(e))
        except Exception as e:  # a bug must not leave the job stuck as running
            s.rollback()
            job = s.get(m.AgentJob, job_id)
            J.finish(job, "failed", error=f"{type(e).__name__}: {e}")
        return job.status


def queued(app) -> list[str]:
    """Queued jobs that may run now, oldest first (jobs waiting out a retry delay are left out)."""
    with app.state.db.session() as s:
        rows = s.execute(select(m.AgentJob.id, m.AgentJob.retry_at).where(m.AgentJob.status == "queued").order_by(m.AgentJob.created_at)).all()
    return [jid for jid, retry_at in rows if J.due(retry_at)]


def next_job(app) -> str | None:
    """The oldest job the background worker can run now. While today's AI budget is used up, AI work waits
    and work that needs no AI goes ahead, so one waiting job never blocks the rest of the board."""
    with app.state.db.session() as s:
        c = agent_ctx(app, s)
        budget = ai_budget_left(c)
        rows = s.execute(select(m.AgentJob.id, m.AgentJob.kind, m.AgentJob.retry_at)
                         .where(m.AgentJob.status == "queued").order_by(m.AgentJob.created_at)).all()
        for jid, kind, retry_at in rows:
            if J.due(retry_at) and (budget or not uses_ai(c, kind)):
                return jid
    return None


def run_pending(app, limit: int = 50, pause: float = 0) -> list[dict]:
    """Work through the board in order, including handoffs created along the way. Returns what happened."""
    out, tried = [], set()
    with app.state.db.session() as s:
        if J.setting(s, "agents_paused"):
            return out
    while len(out) < limit:
        ids = [i for i in queued(app) if i not in tried]
        if not ids:
            break
        tried.add(ids[0])
        status = run_job(app, ids[0])
        with app.state.db.session() as s:
            j = s.get(m.AgentJob, ids[0])
            out.append({"id": j.id, "agent": j.agent, "kind": j.kind, "opportunityId": j.opportunity_id, "trigger": j.trigger,
                        "status": status, "result": j.result or j.error or ""})
            used_ai = J.KINDS[j.kind][1]
        if pause and used_ai and status in ("done", "failed"):
            time.sleep(pause)
    return out


def patrol_due(app) -> bool:
    with app.state.db.session() as s:
        c = agent_ctx(app, s)
        last = J.setting(s, "last_patrol")
        moved = (s.get(m.Setting, "day_offset").value if s.get(m.Setting, "day_offset") else 0) > 0
        return (not last or date.fromisoformat(last) < c.today) and (datetime.now().hour >= int(J.setting(s, "patrol_hour")) or moved)


def run_patrol(app) -> list[dict]:
    with app.state.db.session() as s:
        jobs = patrol(agent_ctx(app, s))
        return [{"id": j.id, "agent": j.agent, "kind": j.kind, "opportunityId": j.opportunity_id, "reason": j.reason} for j in jobs]


def sync_research(app) -> list[dict]:
    """Live mode: collect finished research runs, so briefs reach Step 4 without anyone pressing a button."""
    with app.state.db.session() as s:
        c = agent_ctx(app, s)
        return [] if c.demo else S.sync_research(c)


class Worker:
    """Background worker: collects finished research, patrols once a day and works through the board,
    slowly enough for the AI free tier."""

    def __init__(self, app, tick: float = 10, pause: float = 4, sync_every: float = 30):
        self.app, self.tick, self.pause, self.sync_every, self.stop = app, tick, pause, sync_every, threading.Event()
        self.last_sync = 0.0
        self.lock = getattr(app.state, "work_lock", None) or threading.Lock()  # shared with POST /api/demo/reset
        self.thread = threading.Thread(target=self.loop, name="cgs-agents", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def step(self):
        if time.monotonic() - self.last_sync >= self.sync_every:  # research runs on its own, so this ignores the pause switch
            self.last_sync = time.monotonic()
            sync_research(self.app)
        with self.app.state.db.session() as s:
            if J.setting(s, "agents_paused"):
                return False
        if patrol_due(self.app):
            run_patrol(self.app)
        job_id = next_job(self.app)
        return bool(job_id) and run_job(self.app, job_id) != "queued"

    def loop(self):
        while not self.stop.wait(self.tick):
            try:
                with self.lock:
                    worked = self.step()
                if worked:
                    time.sleep(self.pause)  # outside the lock, so a reset never waits for the pause
            except Exception as e:  # keep the worker alive; the error is visible in the server window
                print(f"[agents] {type(e).__name__}: {e}", flush=True)


# ---------- a person acts on an inbox item ----------
CHANGES = ("facts", "contact", "nurture", "draft")  # kinds whose apply changes the opportunity or relies on its state


def check_still_valid(c: S.Ctx, it: m.InboxItem, o: m.Opportunity | None, seen_hash: str | None) -> None:
    """Approval gate (the idea comes from the MVP in cgs-lead-workspace): a person applies exactly what they saw,
    while the opportunity is still where it was when the agent prepared it, and before it expires."""
    if it.expires_on and c.today > it.expires_on:
        raise S.ActionError(f"This was prepared on {it.created_on.isoformat()} and expired on {it.expires_on.isoformat()}. "
                            "Dismiss it; the agents prepare a new one when it is needed.", 409, "expired")
    if seen_hash is not None and it.payload_hash and seen_hash != it.payload_hash:
        raise S.ActionError("This item is not the version you saw. Reload the inbox and look again.", 409, "changed")
    if o and it.kind in CHANGES and it.basis and it.basis.get("stage") != o.stage:
        raise S.ActionError(f"The opportunity moved from {it.basis['stage']} to {o.stage} after this was prepared. Dismiss it.", 409, "stale")
    if o and it.kind == "draft":  # every fact the draft cites must still mean the same thing in the brief
        brief = {f["id"]: f["text"] for f in (o.brief.facts or [])}
        clash = [f["id"] for f in it.data.get("facts", []) if f["id"] in brief and brief[f["id"]] != f["text"]]
        if clash:
            raise S.ActionError(f"The brief changed after this draft was written ({', '.join(clash)} now say something else). "
                                "Dismiss it and ask for a new draft.", 409, "stale")


def apply_item(c: S.Ctx, item_id: str, action: str, seen_hash: str | None = None) -> dict:
    """action: apply (use what the agent prepared), done (seen, nothing to change) or dismiss (not useful).
    seen_hash: the payloadHash the person saw; when given, apply refuses anything else."""
    it = c.s.get(m.InboxItem, item_id)
    if not it:
        raise S.ActionError("Inbox item not found.", 404, "not_found")
    if it.status != "new":
        raise S.ActionError(f"This item is already {it.status}.", 409, "wrong_status")
    o = c.s.get(m.Opportunity, it.opportunity_id) if it.opportunity_id else None
    note = ""
    if action == "apply":
        check_still_valid(c, it, o, seen_hash)
        if it.kind == "facts" and o:
            n = len(o.brief.facts or [])
            o.brief.facts = (o.brief.facts or []) + [{**f, "id": f"f{n + i + 1}"} for i, f in enumerate(it.data.get("facts", []))]
            note = f"{len(it.data.get('facts', []))} new facts added to the brief"
        elif it.kind == "contact" and o:
            d = it.data
            c.s.add(m.Contact(id=S.new_id("ct"), company_id=o.company_id, name=d["name"], title=d["title"]))
            note = f"{d['name']} ({d['title']}) added as a contact at {o.company.name}; source {d['url']}"
        elif it.kind == "nurture" and o:
            S.to_nurture(c, o.id, date.fromisoformat(it.data["review_on"]), "Client deferred; suggested by the action agent")
            note = f"Moved to nurture until {it.data['review_on']}"
        elif it.kind == "draft":
            known = {f["id"] for f in (o.brief.facts or [])} if o else set()
            added = [f for f in it.data.get("facts", []) if f["id"] not in known]
            if o and added:  # the sentences the draft cites join the brief, so the citations resolve
                o.brief.facts = (o.brief.facts or []) + added
            note = (f"{it.data.get('purpose', 'outreach').capitalize()} draft accepted. Send it from your own email, then record it."
                    + (f" {len(added)} cited fact{'s' if len(added) != 1 else ''} added to the brief." if added else ""))
        else:
            note = "Done"
        it.status = "applied"
    elif action in ("done", "dismiss"):
        it.status = "done" if action == "done" else "dismissed"
        note = "Marked done" if action == "done" else "Dismissed; nothing changed"
    else:
        raise S.ActionError("Action must be apply, done or dismiss.")
    it.decided_by = c.actor
    if o:
        S.touch(c, o)
        S.log(c, o.id, "h", f"{c.actor} {'applied' if action == 'apply' else action + 'ed' if action == 'dismiss' else 'closed'} "
                             f"what {it.agent} prepared ({it.title}): {note}")
    return {"status": it.status, "note": note}


def item_view(i: m.InboxItem) -> dict:
    return {"id": i.id, "owner": i.owner, "opportunityId": i.opportunity_id, "agent": i.agent, "kind": i.kind, "title": i.title,
            "body": i.body, "data": i.data, "status": i.status, "decidedBy": i.decided_by, "on": i.created_on.isoformat(), "jobId": i.job_id,
            "payloadHash": i.payload_hash, "expiresOn": i.expires_on.isoformat() if i.expires_on else None}


def job_view(j: m.AgentJob) -> dict:
    return {"id": j.id, "agent": j.agent, "kind": j.kind, "opportunityId": j.opportunity_id, "trigger": j.trigger, "reason": j.reason,
            "requestedBy": j.requested_by, "status": j.status, "result": j.result, "error": j.error, "parentId": j.parent_id,
            "depth": j.depth, "attempts": j.attempts, "on": j.created_on.isoformat()}
