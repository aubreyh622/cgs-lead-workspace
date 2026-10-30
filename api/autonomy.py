"""Autonomous agents: they find work themselves and prepare it for people.

How agents find work (each piece of work is a job on the board, api/jobs.py):
  event    something happened: a reply says "not now", a pack was returned, the rules now say Ready
  patrol   once a day (and on demand) the patrol looks at every opportunity: no reply after a week,
           a nurture review date reached, no progress for two weeks, Ready but not submitted, weekly discovery and feedback
  handoff  an agent asks another for help: the evidence agent asks the research agent to find "the CFO who approves";
           the research agent finds news on a deferred client and asks the outreach agent for a reconnect draft.
           With the manager on, the request goes to the manager's triage, which accepts or declines it
  triage   with the manager watching (shadow or on), anything that happens to an opportunity (notes, a reply, a returned
           pack, a request, a promise past its date) gets the manager's close look that day (api/manager_triage.py)

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
from api import demo_agents as A
from api import jobs as J
from api import live_agents as L
from api import manager_agent as MA
from api import manager_triage as MT
from api import lessons as LS
from api import lessons_agent as LA
from api import memory as MEM
from api import outreach_agent as OA
from api import research_agent as RA
from api import models as m
from api import services as S
from api import toolkit as TK
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
                 search_factory=getattr(app.state, "search_factory", None), mail_factory=lambda: getattr(app.state, "mail", None),
                 crm_factory=lambda: getattr(app.state, "crm", None))


def web(c: S.Ctx, agent: str = "Research agent"):
    """The web, as the agent doing this work may reach it (api/toolkit)."""
    return TK.web_for(agent, c)


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
    if c.mail_factory and c.mail_factory() is not None and s.scalar(select(m.Outreach.opportunity_id).where(m.Outreach.mail_thread_id.is_not(None)).limit(1)):
        add("mail_sync", None, "Daily check of the email threads")
    from api import crm_sync as CRM
    if CRM.writes_on(c) and s.scalar(select(m.Opportunity.id).where(m.Opportunity.crm_sync.in_(("sync_pending", "sync_failed"))).limit(1)):
        add("crm_sync", None, "Opportunities waiting to be synced to the CRM (Insightly)")
    for o in s.scalars(select(m.Opportunity)):
        if o.stage == "contacted":
            r = s.get(m.Outreach, o.id)
            if r and r.sent_on and not r.reply_on and (t - r.sent_on).days >= follow and not recent(c, "followup_draft", o.id, r.sent_on + timedelta(days=1)):
                add("followup_draft", o, f"No reply {(t - r.sent_on).days} days after the email of {S.fmt(r.sent_on)}")
        elif o.stage == "nurture" and o.next_due and o.next_due <= t and not recent(c, "research_refresh", o.id, o.next_due):
            add("research_refresh", o, f"Nurture review date reached ({S.fmt(o.next_due)}): check for news")
        elif o.stage == "active":
            snoozed = o.snoozed_until and o.snoozed_until > t
            if o.last_progress and (t - o.last_progress).days >= stall and not snoozed \
                    and not recent(c, "gap_actions", o.id, t - timedelta(days=stall)) and not stalled_triage(c, o.id, t - timedelta(days=stall)):
                job = J.ask_for_next_actions(s, t, o.id, "patrol", f"No progress recorded for {(t - o.last_progress).days} days",
                                             requested_by="Patrol", live=not c.demo, event="stalled")
                if job and job not in found:
                    found.append(job)
            if S.calculate(S.rules_input(o))["readiness"] == "Ready" and not S.open_pack(c, o) and not recent(c, "ready_reminder", o.id, t - timedelta(days=7)):
                add("ready_reminder", o, "The rules say Ready and the pack is not submitted")
    if mode in ("shadow", "on") and not c.demo:  # a promise past its date goes back to the manager (at most once a week)
        for opp_id, cm in MEM.overdue(s, t):
            o = s.get(m.Opportunity, opp_id)
            if not o or o.stage not in MA.OPEN_STAGES:
                continue
            job = J.triage(s, t, opp_id, "commitment_due", f"{cm['who']} promised to {cm['what']} by {cm['due']}; nothing recorded since",
                           trigger="patrol", requested_by="Patrol")
            if job:
                MEM.mark_nudged(s, opp_id, cm["id"], t)
                if job not in found:
                    found.append(job)
    last = J.setting(s, "last_discovery")
    if not last or (t - date.fromisoformat(last)).days >= 7:
        add("discovery_round", None, "Weekly discovery round")
    last = J.setting(s, "last_feedback_review")
    if not last or (t - date.fromisoformat(last)).days >= 7:
        add("feedback_review", None, "Weekly review of consultant feedback")
    last = J.setting(s, "last_lessons_review")
    if mode in ("shadow", "on") and not c.demo and (not last or (t - date.fromisoformat(last)).days >= 7):
        add("lessons_review", None, "Weekly lessons review by the manager agent")
    J.set_setting(s, "last_patrol", t.isoformat())
    s.add(m.Activity(opportunity_id=None, on=t, actor_type="a", text=f"Patrol: {len(found)} new piece{'s' if len(found) != 1 else ''} of work for the agents"))
    return found


def stalled_triage(c: S.Ctx, opp_id: str, since: date) -> bool:
    """The manager already looked at this stalled opportunity since that day."""
    for j in c.s.scalars(select(m.AgentJob).where(m.AgentJob.kind == "manager_triage", m.AgentJob.opportunity_id == opp_id,
                                                  m.AgentJob.created_on >= since, m.AgentJob.status.in_(("queued", "running", "done")))):
        if any(e.get("event") == "stalled" for e in (j.payload or {}).get("events", [])):
            return True
    return False


def report(c: S.Ctx, job, o, title: str, body: str) -> str:
    """An agent could not deliver what it was asked for. With the manager on, it reports back to the manager, which decides
    what the owner hears (one voice); otherwise the owner gets a reminder. Returns who was told."""
    if o is not None and o.stage in MA.OPEN_STAGES and J.manager_mode(c.s, not c.demo) == "on" \
            and J.triage(c.s, c.today, o.id, "agent_result", f"{job.agent}: {title}. {body}"[:900], requested_by=job.agent):
        return "the manager"
    J.to_inbox(c.s, c.today, job, "reminder", title, body, opp=o)
    return "the owner"


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


def do_first_draft(c, job, o):
    """After Step 4: the first email, ready for the owner to approve and send (POST /api/inbox/{id} action send)."""
    if o.stage != "identified":
        return "skipped", f"The first email is no longer due (stage {o.stage})."
    r = c.s.get(m.Outreach, o.id)
    if r and (r.body or "").strip():
        return "skipped", "A first draft already exists in Step 5 (written or generated by a person)."
    contact = {"name": o.contact.name if o.contact else None, "title": o.contact.title if o.contact else "", "company": o.company.name}
    star = o.company_id == "corvane" and o.title.startswith("Scaling AI")
    trace: list[dict] = []
    draft, _ = S.run_agent(c, job.agent, o, "outreach-demo-1.0",
                           lambda: A.demo_draft(star, contact["name"], o.company.name, o.owner or "CGS Advisors", o.brief.facts, "standard", None),
                           live=lambda p: write_draft(c, o, p, trace, purpose="first outreach email", angle=None, tone="standard"),
                           live_prompt=OA.VERSION + "+first", trace=trace)
    lib = S.knowledge.library().by_id
    synthetic = sorted({x for x in re.findall(r"\[\[([^|\]]+)\|", draft["body"]) if x in lib and lib[x].synthetic})
    warning = (f"Cites a SYNTHETIC demo document ({', '.join(synthetic)}), so it cannot be sent as it is: apply it, replace that "
               "sentence in Step 5, then send." if synthetic else "")
    J.to_inbox(c.s, c.today, job, "draft", f"First email for {contact['name'] or 'the contact'} at {o.company.name}",
               (warning + "\n\n" if warning else "") + draft["body"],
               {"subject": draft["subject"], "body": draft["body"], "purpose": "first", "facts": draft.get("new_facts", []),
                **({"warning": warning} if warning else {})}, opp=o)
    return "done", ("First email draft is in the owner's inbox: approve and send, or edit it in Step 5. Nothing was sent."
                    + (" " + warning if warning else ""))


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
    draft, _ = S.run_agent(c, job.agent, o, "outreach-followup-demo-1.0",
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
    draft, _ = S.run_agent(c, job.agent, o, "outreach-reconnect-demo-1.0",
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
        to = report(c, job, o, f"Nurture review due for {o.company.name}: no news since {S.fmt(since)}",
                    "The research agent found nothing new. Decide whether to reconnect anyway or set a new review date.")
        return "done", f"No news since {since.isoformat()}; told {to}."

    def demo():
        return [{"text": re.split(r"(?<=\.)\s", p["content"], 1)[0], "quote": re.split(r"(?<=\.)\s", p["content"], 1)[0],
                 "src": p["url"], "date": p["published"] or "undated"} for p in pages[:3]]

    trace: list[dict] = []

    def live(p):  # live mode: the research agent's refresh task (api/research_agent.py), within today's AI budget
        facts, usage, steps = RA.refresh(p, web(c), o.company.name, o.company.domain, since, known, may_call=may_call(c, p))
        trace.extend(steps)
        return [{**f, "date": f["date"] or "undated"} for f in facts], usage

    found, _ = S.run_agent(c, job.agent, o, "refresh-demo-1.0", demo, live=live, live_prompt=RA.VERSION, trace=trace,
                           subject=f"News on {o.company.name} since {since.isoformat()}")
    n = len(o.brief.facts or [])
    facts = [{"id": f"f{n + i + 1}", **f} for i, f in enumerate(found)]
    if not facts:
        to = report(c, job, o, f"Nurture review due for {o.company.name}: news found, but nothing usable",
                    "The research agent read new pages, but no quote passed the checks.")
        return "done", f"New pages, no usable facts; told {to}."
    J.to_inbox(c.s, c.today, job, "facts", f"{len(facts)} new fact{'s' if len(facts) != 1 else ''} about {o.company.name}",
               "\n".join(f"[{f['date']}] {f['text']}" for f in facts), {"facts": facts}, opp=o)
    if o.stage == "nurture":
        J.hand_off(c.s, c.today, "reconnect_draft", o.id, f"News on a deferred client: {facts[0]['text'][:120]}",
                   {"facts": facts}, requested_by=job.agent, parent=job, live=not c.demo)
    found_text = f"{len(facts)} new fact{'s' if len(facts) != 1 else ''}"
    return "done", f"{found_text}; asked the outreach agent for a reconnect draft." if o.stage == "nurture" else f"{found_text}."


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

    def live(p):  # live mode: the research agent's contact task decides where to look (api/research_agent.py)
        contacts = [{"name": x.name, "title": x.title, "confirmed_by_a_person": bool(x.confirmed_by)}
                    for x in c.s.scalars(select(m.Contact).where(m.Contact.company_id == o.company_id)) if x.name]
        res, usage, steps = RA.find_contact(p, web(c), o.company.name, o.company.domain, role, asked, contacts, may_call=may_call(c, p))
        trace.extend(steps)
        return res, usage

    res, _ = S.run_agent(c, job.agent, o, "person-demo-1.0", demo, live=live, live_prompt=RA.VERSION, trace=trace,
                         subject=f"Find the {role} at {o.company.name}")
    if not res.get("found"):
        checked = f" What the agent checked: {res['notes']}" if res.get("notes") else ""
        to = report(c, job, o, f"Could not find the {role} at {o.company.name}",
                    f"The notes say: \"{asked}\". No public page names the {role}. Ask the client contact who approves.{checked}")
        return "done", f"No page names the {role}; told {to}."
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
        J.to_inbox(c.s, c.today, job, "direction", "Direction change proposed by the direction agent", res["summary"],
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


def do_lessons_review(c, job, o):
    """The manager's weekly lessons review (api/lessons_agent.py). Lessons it proposes wait for a person's approval."""
    J.set_setting(c.s, "last_lessons_review", c.today.isoformat())
    found = LS.findings(c.s, c.today)
    if not found:
        return "done", "Nothing stands out in how people received the agents' work: no lessons this week."
    trace: list[dict] = []
    record: dict = {"proposed": []}

    def live(p):
        spec, rec = LA.lessons_agent(c, found)
        record.update(rec)
        out = R.run(spec, p, LA.task(c.today), may_call=may_call(c, p))
        trace.extend(out.trace)
        return (out.result or {"summary": out.reason}), out.usage

    result, run_id = S.run_agent(c, job.agent, None, "lessons-demo", lambda: {"summary": "The manager agent needs a real AI model."},
                                 subject="Weekly lessons", live=live, live_prompt=LA.VERSION, trace=trace)
    rows = [c.s.get(m.AgentLesson, i) for i in record["proposed"]]
    for x in rows:
        x.run_id = run_id
    if rows and J.manager_mode(c.s, not c.demo) == "on":
        J.to_inbox(c.s, c.today, job, "lessons", f"{len(rows)} lesson{'s' if len(rows) != 1 else ''} for the agents to review",
                   "\n".join(f"- [{LS.label(x)}] {x.agent}: {x.text}" for x in rows),
                   {"lessons": [x.id for x in rows]}, owner=None)
    n = len(rows)
    return "done", (f"{len(found)} finding{'s' if len(found) != 1 else ''}; {n} lesson{'s' if n != 1 else ''} proposed for a person to approve. "
                    + str(result.get("summary", "")))[:300]


def do_manager_triage(c, job, o):
    """The manager's close look at one opportunity (api/manager_triage.py). Shadow mode records; on mode carries it out.
    If it cannot finish, nothing is lost: the other agents' requests are carried out as asked, and next actions that were
    wanted go to the manager's next-actions step."""
    payload = job.payload or {}
    events, requests = payload.get("events", []), payload.get("requests", [])
    mode = J.manager_mode(c.s, not c.demo)
    if o is None or o.stage not in MA.OPEN_STAGES:
        return "skipped", f"Stage is {o.stage if o else 'unknown'}: nothing to decide."
    if mode not in ("shadow", "on"):
        note = fallback(c, job, o, requests, events, "the manager is off")
        return "skipped", f"The manager agent is off.{note}"
    trace: list[dict] = []
    record: dict = {"decisions": [], "suggestions": [], "message": "", "answered": {}}
    status = {"done": False, "reason": ""}

    def live(p):
        spec, rec = MT.triage_agent(c, o, mode, events, requests)
        record.update(rec)  # the lists are shared, so what it decided is known even if the run fails part way
        out = R.run(spec, p, MT.task(o), may_call=may_call(c, p))
        record.update(rec)  # and the message, a plain string, as it stands at the end
        trace.extend(out.trace)
        status["done"], status["reason"] = out.status == "done", out.reason
        return (out.result or {"summary": out.reason}), out.usage

    try:
        result, run_id = S.run_agent(c, job.agent, o, "triage-demo", lambda: {"summary": "The manager agent needs a real AI model."},
                                     subject=f"Triage: {o.company.name}", live=live, live_prompt=MT.VERSION, trace=trace)
    except S.ActionError as e:
        note = fallback(c, job, o, requests if mode == "on" else [], events if mode == "on" else [], f"the manager could not finish ({e})")
        return "done", f"The manager could not finish: {e}.{note}"[:300]
    for d in record["decisions"]:
        c.s.add(m.ManagerDecision(on=c.today, mode=mode, run_id=run_id, scope="triage", **d))
    sent = []
    if record["suggestions"] or record["message"]:
        if mode == "on":
            sent.append(owner_message(c, job, o, record["suggestions"], record["message"]))
        if record["suggestions"]:
            c.s.add(m.ManagerDecision(on=c.today, mode=mode, run_id=run_id, scope="triage", kind="gap_actions", opportunity_id=o.id,
                                      reason="; ".join(x["title"] for x in record["suggestions"])[:600],
                                      outcome="suggested" if mode == "on" else "recorded"))
        if record["message"]:
            c.s.add(m.ManagerDecision(on=c.today, mode=mode, run_id=run_id, scope="triage", kind="note", opportunity_id=o.id,
                                      reason=record["message"][:600], outcome="sent" if mode == "on" else "recorded"))
    note = ""
    if not status["done"] and mode == "on":  # stopped early (the budget, the step limit): carry out what it left undecided
        left = [q for q in requests if q["id"] not in record["answered"]]
        wanted = [] if record["suggestions"] else events
        note = fallback(c, job, o, left, wanted, f"the manager stopped early ({status['reason']})")
    n_sug, n_ans = len(record["suggestions"]), len(record["answered"])
    parts = [f"{mode.capitalize()} mode"] + ([f"{n_sug} next action{'s' if n_sug != 1 else ''}"] if n_sug else [])         + ([f"{n_ans} request{'s' if n_ans != 1 else ''} answered"] if n_ans else []) + (["one message to the owner"] if sent else [])
    return "done", (", ".join(parts) + f". {result.get('summary', '')}{note}")[:300]


def owner_message(c, job, o, suggestions: list[dict], message: str) -> m.InboxItem:
    """One message to the owner: the manager's note and its next actions together. It replaces the unread messages from the
    manager about the same opportunity, so the owner reads one voice."""
    for old in c.s.scalars(select(m.InboxItem).where(m.InboxItem.opportunity_id == o.id, m.InboxItem.status == "new",
                                                     m.InboxItem.kind.in_(("actions", "advice")))):
        old.status, old.decided_by = "dismissed", "Replaced by a newer message from the manager"
    if suggestions:
        S.save_suggestions(c, o, suggestions, "Manager agent")
        lines = ([message, ""] if message else []) + [f"- {x['title']}" for x in suggestions]
        return J.to_inbox(c.s, c.today, job, "actions", f"Manager: {len(suggestions)} next action{'s' if len(suggestions) != 1 else ''} for {o.company.name}",
                          "\n".join(lines), {"why": job.reason, "message": message, "suggestions": suggestions}, opp=o)
    return J.to_inbox(c.s, c.today, job, "advice", f"Manager: {o.company.name}", message, {"why": message}, opp=o)


def fallback(c, job, o, requests: list[dict], events: list[dict], why: str) -> str:
    """Carry out what a triage could not decide: the requests as asked; next actions by the manager's next-actions step."""
    done = []
    for q in requests:
        if o is not None and J.carry_out_request(c.s, c.today, o.id, q):
            done.append(q["kind"])
    wanted = [e for e in events if e.get("event") in MT.WANTS_ACTIONS]
    if wanted and o is not None and o.stage in S.QUAL_STAGES:
        if J.enqueue(c.s, c.today, "gap_actions", o.id, job.trigger if job.trigger in ("event", "patrol") else "event",
                     wanted[0].get("reason", job.reason), requested_by="Manager agent"):
            done.append("gap_actions")
    if o is not None and done:
        S.log(c, o.id, "s", f"Because {why}, the board carried out without the manager: {', '.join(done)}")
    return f" Carried out without the manager: {', '.join(done)}." if done else ""


def do_crm_sync(c, job, o):
    from api import crm_sync as CRM
    res = CRM.sync(c)
    if res.get("off"):
        return "skipped", "Writing to the CRM is off."
    text = f"Synced {res['synced']} opportunit{'y' if res['synced'] == 1 else 'ies'} to the CRM."
    if res["failed"]:
        text += f" {res['failed']} failed: " + "; ".join(res["errors"][:3])
    return "done", text


def do_mail_sync(c, job, o):
    res = S.sync_mail(c)
    if c.crm_dirty:  # an email sent from Gmail moved a lead to Contacted: tell the CRM
        from api import crm_sync as CRM
        CRM.sync(c, sorted(c.crm_dirty))
        c.crm_dirty.clear()
    if res.get("off"):
        return "skipped", "Email is off."
    n = res["replies"]
    text = f"Checked {res['checked']} email thread(s): {res['sent']} sent from Gmail recorded, {n} new repl{'y' if n == 1 else 'ies'} in the inbox."
    if res["errors"]:
        text += " Could not read: " + "; ".join(res["errors"][:3])
    return "done", text


HANDLERS = {"first_draft": do_first_draft, "followup_draft": do_followup_draft, "reconnect_draft": do_reconnect_draft, "research_refresh": do_research_refresh,
            "person_lookup": do_person_lookup, "gap_actions": do_gap_actions, "defer_nurture": do_defer_nurture,
            "ready_reminder": do_ready_reminder, "discovery_round": do_discovery_round, "feedback_review": do_feedback_review,
            "manager_review": do_manager_review, "manager_triage": do_manager_triage, "lessons_review": do_lessons_review, "mail_sync": do_mail_sync,
            "crm_sync": do_crm_sync}
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


def next_job_for_lane(app, busy_opps: set, busy_kinds: set) -> tuple[str, str | None, str] | None:
    """Like next_job, for one of several lanes: skips an opportunity that already has a job running (here or anywhere,
    so two agents never work on the same opportunity at once) and a company-wide job already running (two discovery
    rounds at once). Returns (job id, opportunity id, kind)."""
    with app.state.db.session() as s:
        c = agent_ctx(app, s)
        budget = ai_budget_left(c)
        running = s.execute(select(m.AgentJob.opportunity_id, m.AgentJob.kind).where(m.AgentJob.status == "running")).all()
        opps = set(busy_opps) | {o for o, _ in running if o}
        kinds = set(busy_kinds) | {k for o, k in running if not o}
        rows = s.execute(select(m.AgentJob.id, m.AgentJob.kind, m.AgentJob.retry_at, m.AgentJob.opportunity_id)
                         .where(m.AgentJob.status == "queued").order_by(m.AgentJob.created_at)).all()
        for jid, kind, retry_at, opp in rows:
            if (opp in opps) if opp else (kind in kinds):
                continue
            if J.due(retry_at) and (budget or not uses_ai(c, kind)):
                return jid, opp, kind
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
    """Background worker: collects finished research, patrols once a day and works through the board in parallel lanes
    (settings.agent_lanes; 1 = one job at a time). While the outreach agent writes a draft for one opportunity, the discovery
    agent can search and another opportunity's job can run. Rules for the lanes: never two jobs for the same opportunity at
    once, never two of the same company-wide job (a discovery round, the daily review); lane 0 alone does the patrol and
    collects research. Each lane pauses between jobs, for the AI provider's rate limit. The daily AI budget is checked before
    every AI request, so parallel lanes can pass it by at most a request or two."""

    def __init__(self, app, tick: float = 10, pause: float = 4, sync_every: float = 30, lanes: int | None = None):
        self.app, self.tick, self.pause, self.sync_every, self.stop = app, tick, pause, sync_every, threading.Event()
        self.last_sync = 0.0
        self.lock = getattr(app.state, "work_lock", None) or J.WorkGate()  # shared with POST /api/demo/reset
        self.lanes = lanes or getattr(app.state.settings, "agent_lanes", 1) or 1
        self.pick = threading.Lock()  # one lane at a time chooses its next job
        self.busy_opps: set = set()
        self.busy_kinds: set = set()
        self.threads = [threading.Thread(target=self.loop, args=(i,), name=f"cgs-agents-{i}", daemon=True) for i in range(self.lanes)]
        self.thread = self.threads[0]

    def start(self):
        for t in self.threads:
            t.start()
        return self

    def step(self, lane: int = 0):
        if lane == 0 and time.monotonic() - self.last_sync >= self.sync_every:  # research runs on its own: no pause switch
            self.last_sync = time.monotonic()
            sync_research(self.app)
        with self.app.state.db.session() as s:
            if J.setting(s, "agents_paused"):
                return False
        if lane == 0 and patrol_due(self.app):
            run_patrol(self.app)
        with self.pick:
            nxt = next_job_for_lane(self.app, self.busy_opps, self.busy_kinds)
            if not nxt:
                return False
            job_id, opp, kind = nxt
            if opp:
                self.busy_opps.add(opp)
            else:
                self.busy_kinds.add(kind)
        try:
            return run_job(self.app, job_id) != "queued"
        finally:
            with self.pick:
                if opp:
                    self.busy_opps.discard(opp)
                else:
                    self.busy_kinds.discard(kind)

    def loop(self, lane: int = 0):
        while not self.stop.wait(self.tick):
            try:
                with self.lock:
                    worked = self.step(lane)
                if worked:
                    time.sleep(self.pause)  # outside the lock, so a reset never waits for the pause
            except Exception as e:  # keep the worker alive; the error is visible in the server window
                print(f"[agents lane {lane}] {type(e).__name__}: {e}", flush=True)


# ---------- a person acts on an inbox item ----------
CHANGES = ("facts", "contact", "nurture", "draft", "reply")  # kinds whose apply changes the opportunity or relies on its state


def check_still_valid(c: S.Ctx, it: m.InboxItem, o: m.Opportunity | None, seen_hash: str | None) -> None:
    """Approval gate (the idea comes from the MVP in cgs-lead-workspace): a person applies exactly what they saw,
    while the opportunity is still where it was when the agent prepared it, and before it expires."""
    if it.expires_on and c.today > it.expires_on:
        raise S.ActionError(f"This was prepared on {it.created_on.isoformat()} and expired on {it.expires_on.isoformat()}. "
                            "Dismiss it; the agents prepare a new one when it is needed.", 409, "expired")
    if seen_hash is not None and it.payload_hash and seen_hash != it.payload_hash:
        raise S.ActionError("This item is not the version you saw. Reload the inbox and look again.", 409, "changed")
    if o and it.kind in CHANGES and it.kind != "reply" and it.basis and it.basis.get("stage") != o.stage:
        raise S.ActionError(f"The opportunity moved from {it.basis['stage']} to {o.stage} after this was prepared. Dismiss it.", 409, "stale")
    if o and it.kind == "draft":  # every fact the draft cites must still mean the same thing in the brief
        brief = {f["id"]: f["text"] for f in (o.brief.facts or [])}
        clash = [f["id"] for f in it.data.get("facts", []) if f["id"] in brief and brief[f["id"]] != f["text"]]
        if clash:
            raise S.ActionError(f"The brief changed after this draft was written ({', '.join(clash)} now say something else). "
                                "Dismiss it and ask for a new draft.", 409, "stale")


def approve_and_send(c: S.Ctx, it: m.InboxItem, o: m.Opportunity | None, seen_hash: str | None, to: str | None) -> dict:
    """A person approves an email draft and it goes out at once through the mail connector (Gmail).
    The approval covers exactly what they saw: the draft (seen_hash = its payloadHash) and the address (to)."""
    if it.kind != "draft" or o is None:
        raise S.ActionError("Only an email draft can be approved and sent.", 400, "not_a_draft")
    if not seen_hash:
        raise S.ActionError("Approve and send needs the payloadHash you saw.", 400, "no_hash")
    if not (to or "").strip():
        raise S.ActionError("Say which address to send to.", 400, "no_address")
    check_still_valid(c, it, o, seen_hash)
    p = S.email_preview(c, o.id, to, it.id)
    sent = S.email_out(c, o.id, p["to"], "send", p["seenHash"], it.id)  # also applies the item
    return {"status": "applied", "note": f"Approved by {c.actor} and sent to {p['to']} through {S.mailbox(c).name}.", "sent": sent}


def apply_item(c: S.Ctx, item_id: str, action: str, seen_hash: str | None = None, to: str | None = None) -> dict:
    """action: apply (use what the agent prepared), send (an email draft: approve it and send it now to `to`),
    done (seen, nothing to change) or dismiss (not useful).
    seen_hash: the payloadHash the person saw; when given, apply refuses anything else (send requires it)."""
    it = c.s.get(m.InboxItem, item_id)
    if not it:
        raise S.ActionError("Inbox item not found.", 404, "not_found")
    if it.status != "new":
        raise S.ActionError(f"This item is already {it.status}.", 409, "wrong_status")
    o = c.s.get(m.Opportunity, it.opportunity_id) if it.opportunity_id else None
    if action == "send":
        return approve_and_send(c, it, o, seen_hash, to)
    note = ""
    if action == "apply":
        check_still_valid(c, it, o, seen_hash)
        if it.kind == "facts" and o:
            n = len(o.brief.facts or [])
            o.brief.facts = (o.brief.facts or []) + [{**f, "id": f"f{n + i + 1}"} for i, f in enumerate(it.data.get("facts", []))]
            note = f"{len(it.data.get('facts', []))} new facts added to the brief"
        elif it.kind == "contact" and o:
            d = it.data
            c.s.add(m.Contact(id=S.new_id("ct"), company_id=o.company_id, name=d["name"], title=d["title"], confirmed_by=c.actor))
            note = f"{d['name']} ({d['title']}) added as a contact at {o.company.name}; source {d['url']}"
        elif it.kind == "nurture" and o:
            S.to_nurture(c, o.id, date.fromisoformat(it.data["review_on"]), "Client deferred; suggested by the action agent")
            note = f"Moved to nurture until {it.data['review_on']}"
        elif it.kind == "reply" and o and o.stage in S.QUAL_STAGES:  # a client email during qualification is evidence
            on = min(date.fromisoformat(it.data["on"]), c.today)
            try:
                res = S.add_note(c, o.id, "Email reply", on, it.data["text"], analyze=True)
                note = f"Email added to the notes; the evidence agent proposed {res['proposals']} item{'s' if res['proposals'] != 1 else ''}"
            except S.ActionError as e:  # the email is saved; only the analysis failed
                note = f"Email added to the notes; the analysis did not run ({e}). Run it again from Step 6"
        elif it.kind == "reply" and o:
            res = S.record_reply(c, o.id, it.data["text"], min(date.fromisoformat(it.data["on"]), c.today))
            note = f"Email reply recorded; stage set to Active; reply label {res['replyLabel']}"
        elif it.kind == "draft":
            known = {f["id"] for f in (o.brief.facts or [])} if o else set()
            added = [f for f in it.data.get("facts", []) if f["id"] not in known]
            if o and added:  # the sentences the draft cites join the brief, so the citations resolve
                o.brief.facts = (o.brief.facts or []) + added
            if o and it.data.get("purpose") == "first":  # the first email becomes the Step 5 draft, where it can be edited and sent
                r = S.outreach_row(c, o)
                if r.status in ("none", "draft"):
                    r.subject, r.body, r.status = it.data.get("subject", ""), it.data.get("body", ""), "draft"
                note = "First email saved as the Step 5 draft: edit it there and send it, or send it from your own email and record it."
            else:
                note = f"{it.data.get('purpose', 'outreach').capitalize()} draft accepted. Send it from your own email, then record it."
            note += f" {len(added)} cited fact{'s' if len(added) != 1 else ''} added to the brief." if added else ""
        else:
            note = "Done"
        it.status = "applied"
    elif action in ("done", "dismiss"):
        it.status = "done" if action == "done" else "dismissed"
        note = "Marked done" if action == "done" else "Dismissed; nothing changed"
    else:
        raise S.ActionError("Action must be apply, send, done or dismiss.")
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
