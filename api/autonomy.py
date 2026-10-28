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

from api import jobs as J
from api import live_agents as L
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

    def add(kind, o, reason, payload=None):
        job = J.enqueue(s, t, kind, o.id if o else None, "patrol", reason, payload, requested_by="Patrol")
        if job:
            found.append(job)

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


def do_followup_draft(c, job, o):
    if o.stage != "contacted":
        return "skipped", f"No longer waiting for a reply (stage {o.stage})."
    r = c.s.get(m.Outreach, o.id)
    b = o.brief
    contact = {"name": o.contact.name if o.contact else None, "title": o.contact.title if o.contact else "", "company": o.company.name}
    angle = (f"A short follow-up to the first email sent on {r.sent_on.isoformat()} (subject: {r.subject or 'introduction'}). "
             "Do not repeat the first email. One new reason to talk, then offer a 20-minute call.")
    fact = (b.facts or [{"id": "f1", "text": o.company.name}])[0]
    draft, _ = S.run_agent(c, job.agent, o, "a1-followup-demo-1.0",
                           lambda: {"subject": f"Re: {r.subject or o.title}",
                                    "body": f"Hi {_first(contact['name'])}, I wanted to follow up on my note from {S.fmt(r.sent_on)}. "
                                            f"Since [[{fact['id']}|{fact['text'][:90]}]], would a 20-minute call in the next two weeks be useful? "
                                            f"{o.owner or 'CGS Advisors'}, CGS Advisors"},
                           live=lambda p: L.write_outreach(p, {"facts": b.facts, "hypothesis": b.hypothesis, "relevance": b.relevance},
                                                           contact, o.owner or "CGS Advisors", angle, "brief"),
                           live_prompt=L.OUTREACH_PROMPT_VERSION + "+followup")
    J.to_inbox(c.s, c.today, job, "draft", f"Follow-up draft for {contact['name'] or 'the contact'} at {o.company.name}", draft["body"],
               {"subject": draft["subject"], "body": draft["body"], "purpose": "followup"}, opp=o)
    return "done", "Follow-up draft is in the owner's inbox. Nothing was sent."


def do_reconnect_draft(c, job, o):
    facts = job.payload.get("facts") or []
    if not facts:
        return "skipped", "No new facts to build on."
    contact = {"name": o.contact.name if o.contact else None, "title": o.contact.title if o.contact else "", "company": o.company.name}
    angle = "The client asked to talk again later. Reconnect by citing the new development; keep it short and offer a short call."
    f = facts[0]
    draft, _ = S.run_agent(c, job.agent, o, "a1-reconnect-demo-1.0",
                           lambda: {"subject": f"{o.company.name}: {f['text'][:60]}",
                                    "body": f"Hi {_first(contact['name'])}, I saw that [[{f['id']}|{f['text'][:100]}]]. When we spoke you suggested "
                                            f"reconnecting around now. Would a short call be useful? {o.owner or 'CGS Advisors'}, CGS Advisors"},
                           live=lambda p: L.write_outreach(p, {"facts": facts, "hypothesis": o.brief.hypothesis, "relevance": o.brief.relevance},
                                                           contact, o.owner or "CGS Advisors", angle, "standard"),
                           live_prompt=L.OUTREACH_PROMPT_VERSION + "+reconnect")
    J.to_inbox(c.s, c.today, job, "draft", f"Reconnect draft for {contact['name'] or 'the contact'} at {o.company.name}", draft["body"],
               {"subject": draft["subject"], "body": draft["body"], "purpose": "reconnect"}, opp=o)
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
    pages = company_pages(c, o.company.name, o.company.domain, f"{role} leadership")

    def demo():
        for p in pages:
            for name, title in _NAME_TITLE.findall(p["content"]):
                if S.role_matches(title, role):
                    return {"found": True, "name": name, "title": title.strip(), "url": p["url"], "quote": f"{name}, {title}"}
        return {"found": False}

    res, _ = S.run_agent(c, job.agent, o, "person-demo-1.0", demo, live=lambda p: L.find_person(p, pages, o.company.name, role),
                         live_prompt=L.PERSON_PROMPT_VERSION)
    asked = job.payload.get("quote", "")
    if not res.get("found"):
        J.to_inbox(c.s, c.today, job, "reminder", f"Could not find the {role} at {o.company.name}",
                   f"The notes say: \"{asked}\". No public page names the {role}. Ask the client contact who approves.", opp=o)
        return "done", f"No page names the {role}."
    J.to_inbox(c.s, c.today, job, "contact", f"{role} at {o.company.name}: {res['name']} ({res['title']})",
               f"Why: the notes say \"{asked}\". Source: {res['url']}", {**res, "role": role}, opp=o)
    return "done", f"Found {res['name']} ({res['title']}); the owner confirms before it is added."


def do_gap_actions(c, job, o):
    if o.stage not in S.QUAL_STAGES:
        return "skipped", f"Stage is {o.stage}."
    n = S.suggest_actions(c, o.id)
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
    res = S.recommend(c, "Weekly round by the discovery agent")
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


HANDLERS = {"followup_draft": do_followup_draft, "reconnect_draft": do_reconnect_draft, "research_refresh": do_research_refresh,
            "person_lookup": do_person_lookup, "gap_actions": do_gap_actions, "defer_nurture": do_defer_nurture,
            "ready_reminder": do_ready_reminder, "discovery_round": do_discovery_round, "feedback_review": do_feedback_review}
assert set(HANDLERS) == set(J.KINDS)


# ---------- running the board ----------
def run_job(app, job_id: str) -> str:
    """Run one queued job. Returns its new status: done | skipped | failed | queued (waiting for the AI budget or a retry)."""
    with app.state.db.session() as s:
        c = agent_ctx(app, s)
        job = s.get(m.AgentJob, job_id)
        if not job or job.status != "queued":
            return job.status if job else "missing"
        uses_ai = J.KINDS[job.kind][1] and not c.demo
        if uses_ai and J.ai_calls_today(s, c.today) >= int(J.setting(s, "agent_ai_budget")):
            job.result = "Waiting: today's AI budget is used up. It runs tomorrow."
            return "queued"
        job.status, job.attempts = "running", job.attempts + 1
        s.commit()
        o = s.get(m.Opportunity, job.opportunity_id) if job.opportunity_id else None
        try:
            status, result = HANDLERS[job.kind](c, job, o)
            J.finish(job, status, result)
            if o:
                S.log(c, o.id, "a", f"{job.agent}: {result}")
        except S.ActionError as e:
            if e.code == "ai_temporary" and job.attempts < 3:
                job.status, job.result = "queued", f"Temporary AI error, will retry: {e}"
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
    with app.state.db.session() as s:
        return list(s.scalars(select(m.AgentJob.id).where(m.AgentJob.status == "queued").order_by(m.AgentJob.created_at)))


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


class Worker:
    """Background worker: patrols once a day and works through the board, slowly enough for the AI free tier."""

    def __init__(self, app, tick: float = 10, pause: float = 4):
        self.app, self.tick, self.pause, self.stop = app, tick, pause, threading.Event()
        self.thread = threading.Thread(target=self.loop, name="cgs-agents", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def loop(self):
        while not self.stop.wait(self.tick):
            try:
                with self.app.state.db.session() as s:
                    if J.setting(s, "agents_paused"):
                        continue
                if patrol_due(self.app):
                    run_patrol(self.app)
                ids = queued(self.app)
                if ids:
                    run_job(self.app, ids[0])
                    time.sleep(self.pause)
            except Exception as e:  # keep the worker alive; the error is visible in the server window
                print(f"[agents] {type(e).__name__}: {e}", flush=True)


# ---------- a person acts on an inbox item ----------
def apply_item(c: S.Ctx, item_id: str, action: str) -> dict:
    """action: apply (use what the agent prepared), done (seen, nothing to change) or dismiss (not useful)."""
    it = c.s.get(m.InboxItem, item_id)
    if not it:
        raise S.ActionError("Inbox item not found.", 404, "not_found")
    if it.status != "new":
        raise S.ActionError(f"This item is already {it.status}.", 409, "wrong_status")
    o = c.s.get(m.Opportunity, it.opportunity_id) if it.opportunity_id else None
    note = ""
    if action == "apply":
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
            note = f"{it.data.get('purpose', 'outreach').capitalize()} draft accepted. Send it from your own email, then record it."
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
            "body": i.body, "data": i.data, "status": i.status, "decidedBy": i.decided_by, "on": i.created_on.isoformat(), "jobId": i.job_id}


def job_view(j: m.AgentJob) -> dict:
    return {"id": j.id, "agent": j.agent, "kind": j.kind, "opportunityId": j.opportunity_id, "trigger": j.trigger, "reason": j.reason,
            "requestedBy": j.requested_by, "status": j.status, "result": j.result, "error": j.error, "parentId": j.parent_id,
            "depth": j.depth, "attempts": j.attempts, "on": j.created_on.isoformat()}
