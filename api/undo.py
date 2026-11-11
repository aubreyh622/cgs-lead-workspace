"""Undo: a person can take back a decision for a short time, before anything leaves the workspace.

- An approved email waits UNDO seconds in outbound_emails, then goes out (Clock below). Undo cancels it; Send now
  sends it at once. It holds exactly what the person approved.
- Accepting or rejecting at Step 4 holds its outside effects for the same window: no CRM write and no agent draft
  until the window ends (the CRM sync skips held opportunities; the first-draft job waits). Undo puts the opportunity
  back in Step 4 as it was.
- A skipped Step 1 candidate can be put back any time, unless the direction already changed because of it.

An undone decision stays in the records: the Step 4 decision gets an "Undone" entry, the feedback is marked withdrawn
(the learning loop ignores it), and an exclusion it proposed is withdrawn. A person's request asks for the window
(undo_seconds or delay); without it the action happens at once, as before.

The Clock runs in the server process (python -m api.main), apart from the agent worker, so a paused agent team never
holds a person's email. An email whose time passed more than STALE ago (the server was stopped) is not sent: the
person approves it again. An email the server stopped in the middle of sending is marked unknown and never sent again
on its own (recover_sending): the person checks Gmail's Sent folder first, so a client never gets it twice."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging
import threading

from sqlalchemy import select, update

from api import models as m

log_ = logging.getLogger("cgs.undo")

MAX_SECONDS = 30
STALE = timedelta(minutes=10)
UNKNOWN = ("The workspace stopped while this email was being sent, so it may or may not have gone out. "
           "Check the Sent folder in Gmail before you approve it again.")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(t: datetime | None) -> datetime | None:
    """SQLite returns stored times without a time zone; they are UTC."""
    return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t


def window(seconds) -> int:
    try:
        n = int(seconds or 0)
    except (TypeError, ValueError):
        n = 0
    return max(0, min(MAX_SECONDS, n))


def held(o: m.Opportunity, now: datetime | None = None) -> bool:
    """True while a Step 4 decision on this opportunity can still be undone: its outside effects wait."""
    t = aware(o.undo_until)
    return t is not None and t > (now or utcnow())


def seconds_left(t: datetime | None) -> int:
    t = aware(t)
    return max(0, int((t - utcnow()).total_seconds() + 0.999)) if t else 0


def view(o: m.Opportunity) -> dict | None:
    """For the page: the decision that can still be undone, and for how long."""
    if not held(o) or not o.undo:
        return None
    return {"decision": o.undo.get("decision"), "by": o.undo.get("by"), "until": aware(o.undo_until).isoformat(),
            "seconds": seconds_left(o.undo_until)}


# ---------- feedback and the proposals it started ----------
def withdraw_feedback(c, fb: m.Feedback | None) -> None:
    """Undo the lesson a decision taught: the feedback is kept but withdrawn, and an exclusion it proposed is withdrawn
    while it is still pending. Refuses when the direction already changed because of it."""
    from api.services import ActionError
    if fb is None or fb.withdrawn_by:
        return
    props = [p for p in c.s.scalars(select(m.DirectionProposal)) if fb.id in (p.feedback_ids or [])]
    done = [p for p in props if p.status == "approved"]
    if done:
        raise ActionError("The research direction already changed because of this decision, so it cannot be undone here. "
                          "Change the direction on the Sources page instead.", 409, "direction_changed")
    for p in props:
        if p.status == "pending":
            p.status = "withdrawn"
    fb.withdrawn_by, fb.withdrawn_on = c.actor, c.today


# ---------- Step 1: put a skipped candidate back ----------
def unskip(c, cand_id: str) -> None:
    from api.services import ActionError
    cand = c.s.get(m.Candidate, cand_id)
    if not cand:
        raise ActionError("Candidate not found.", 404, "not_found")
    if cand.status != "skipped":
        raise ActionError(f"This candidate is {cand.status}, not skipped.", 409, "wrong_status")
    fb = c.s.scalars(select(m.Feedback).where(m.Feedback.subject == cand.company, m.Feedback.step == 1, m.Feedback.outcome == "Skipped",
                                              m.Feedback.withdrawn_by.is_(None)).order_by(m.Feedback.on.desc())).first()
    withdraw_feedback(c, fb)
    cand.status, cand.skip_reason, cand.skip_scope = "new", None, None


# ---------- Step 4: hold a decision, undo it, release it ----------
def before_step4(o: m.Opportunity) -> dict:
    return {"stage": o.stage, "owner": o.owner, "rating": o.rating, "next_action": o.next_action,
            "next_due": o.next_due.isoformat() if o.next_due else None,
            "frozen_on": o.brief.frozen_on.isoformat() if o.brief and o.brief.frozen_on else None, "crm_sync": o.crm_sync}


def hold_step4(c, o: m.Opportunity, decision: str, before: dict, seconds: int, feedback_id: str, job: m.AgentJob | None = None) -> None:
    until = utcnow() + timedelta(seconds=seconds)
    o.undo_until = until
    o.undo = {"kind": "step4", "decision": decision, "by": c.actor, "before": before, "feedback": feedback_id,
              "job": job.id if job else None, "revision": o.revision + 1}  # step4() adds 1 after this
    if job is not None:
        job.retry_at = until  # the outreach agent starts once the window has passed


def claim(c, opp_id: str) -> bool:
    """End the undo window once, whoever gets here first (the person's Undo or the Clock)."""
    res = c.s.execute(update(m.Opportunity).where(m.Opportunity.id == opp_id, m.Opportunity.undo_until.is_not(None))
                      .values(undo_until=None))
    return res.rowcount == 1


def undo_step4(c, opp_id: str) -> dict:
    from api import services as S
    o = c.s.get(m.Opportunity, opp_id)
    if not o:
        raise S.ActionError("Opportunity not found.", 404, "not_found")
    u = o.undo or {}
    if u.get("kind") != "step4" or not held(o):
        raise S.ActionError("This decision can no longer be undone: its time has passed. Change it from the opportunity's page.",
                            409, "too_late")
    if o.revision != u.get("revision"):
        raise S.ActionError("Something else changed on this opportunity after the decision, so it cannot be undone.", 409, "changed")
    if not claim(c, o.id):
        raise S.ActionError("This decision can no longer be undone: its time has passed.", 409, "too_late")
    b = u["before"]
    o.stage, o.owner, o.rating, o.next_action = b["stage"], b["owner"], b["rating"], b["next_action"]
    o.next_due = date.fromisoformat(b["next_due"]) if b.get("next_due") else None
    if o.brief:
        o.brief.frozen_on = date.fromisoformat(b["frozen_on"]) if b.get("frozen_on") else None
    if not o.crm_link:  # nothing reached the CRM: the hold kept it back
        o.crm_sync = b.get("crm_sync") or "local_saved"
    c.crm_dirty.discard(o.id)
    if u.get("job"):
        job = c.s.get(m.AgentJob, u["job"])
        if job is not None and job.status == "queued":
            job.status, job.result, job.finished_at = "skipped", f"Not needed: {c.actor} undid the decision.", utcnow()
    withdraw_feedback(c, c.s.get(m.Feedback, u.get("feedback")) if u.get("feedback") else None)
    c.s.add(m.Decision(opportunity_id=o.id, step=4, decision="Undone", by=c.actor, note=f"Undid: {u['decision']}", on=c.today))
    S.log(c, o.id, "h", f"{c.actor} undid the decision ({u['decision'].lower()}). The opportunity is back in Step 4; "
                        "nothing had gone to the CRM or the agents.")
    o.undo = None
    o.revision += 1
    return {"stage": o.stage}


def release_due(app) -> list[str]:
    """End the undo windows that have passed: the CRM write and the agents' work go ahead. Returns the opportunity ids."""
    from api.autonomy import agent_ctx
    from api import crm_sync as CRM
    now = utcnow()
    with app.state.db.session() as s:
        c = agent_ctx(app, s)
        ids = [o.id for o in s.scalars(select(m.Opportunity).where(m.Opportunity.undo_until.is_not(None))) if not held(o, now)]
        done = []
        for oid in ids:
            if claim(c, oid):
                o = s.get(m.Opportunity, oid)
                job = s.get(m.AgentJob, o.undo["job"]) if o.undo and o.undo.get("job") else None
                if job is not None and job.status == "queued":
                    job.retry_at = None  # the outreach agent may start now
                o.undo = None
                done.append(oid)
        s.commit()
        today = c.today
        sync = [oid for oid in done if s.get(m.Opportunity, oid).crm_sync == "sync_pending"]
    if sync:
        CRM.after_request(app, set(sync), today)
    return done


# ---------- Step 5: an approved email waits, then goes out ----------
def waiting_email(s, opp_id: str) -> m.OutboundEmail | None:
    return s.scalars(select(m.OutboundEmail).where(m.OutboundEmail.opportunity_id == opp_id,
                                                     m.OutboundEmail.status.in_(("approval", "queued", "sending")))).first()


def email_view(s, opp_id: str) -> dict | None:
    """The approved email still waiting, or the latest one that failed or was not sent, for the page."""
    row = waiting_email(s, opp_id) or s.scalars(select(m.OutboundEmail).where(m.OutboundEmail.opportunity_id == opp_id)
                                                  .order_by(m.OutboundEmail.created_at.desc())).first()
    if row is None or row.status in ("sent", "undone") or row.resolved_at:
        return None
    return {"id": row.id, "status": row.status, "purpose": row.purpose, "to": row.to, "subject": row.subject, "by": row.by,
            "itemId": row.item_id, "sendAt": aware(row.send_at).isoformat(), "seconds": seconds_left(row.send_at), "error": row.error,
            "approver": row.approver, "approvedBy": row.approved_by,
            **({"body": row.body} if row.status == "approval" else {})}


def mark_checked(c, out_id: str) -> dict:
    """A person looked at an email that failed, was not sent or may have gone out (unknown): it leaves their to-do list
    and the opportunity page. Nothing is sent and the draft is unchanged; they approve it again if it should still go."""
    from api import services as S
    row = _row(c, out_id)
    if row.status not in ("failed", "expired", "unknown", "returned"):
        raise S.ActionError(f"That email is {row.status}; only one that was not sent, or may not have been, can be marked checked.", 409, "wrong_status")
    if not row.resolved_at:
        row.resolved_by, row.resolved_at = c.actor, utcnow()
        S.log(c, row.opportunity_id, "h", f"{c.actor} checked the {row.purpose} email to {row.to} that was {row.status}.")
    return {"status": row.status, "checkedBy": row.resolved_by}


def recover_sending(app) -> int:
    """After a restart: an email left as sending was going out when the workspace stopped. Gmail may have sent it, so it
    is not sent again: it becomes unknown (the opportunity's next email is no longer held back by it) and the page and
    the updates feed ask the person to check Gmail's Sent folder. Only the server process calls this, before its clock
    starts (api/main.py). Returns how many emails were marked."""
    from api.autonomy import agent_ctx
    from api.services import log
    with app.state.db.session() as s:
        rows = list(s.scalars(select(m.OutboundEmail).where(m.OutboundEmail.status == "sending")))
        if not rows:
            return 0
        c = agent_ctx(app, s)
        for row in rows:
            row.status, row.error, row.ended_at = "unknown", UNKNOWN, utcnow()
            log(c, row.opportunity_id, "s", f"The workspace stopped while the {row.purpose} email to {row.to} approved by {row.by} "
                                            "was being sent. It may or may not have gone out: check the Sent folder in Gmail before "
                                            "approving it again.")
    return len(rows)


def queue_email(c, o: m.Opportunity, p: dict, it: m.InboxItem | None, seconds: int) -> dict:
    from api import services as S
    row = m.OutboundEmail(id=S.new_id("out"), opportunity_id=o.id, item_id=it.id if it is not None else None, purpose=p["purpose"],
                          to=p["to"], subject=p["subject"], body=p["body"], seen_hash=p["seenHash"], by=c.actor,
                          send_at=utcnow() + timedelta(seconds=seconds))
    c.s.add(row)
    S.log(c, o.id, "h", f"{c.actor} approved the {p['purpose']} email to {p['to']}. It goes out in {seconds} seconds unless undone.")
    return {"mode": "queued", "to": p["to"], "id": row.id, "sendAt": row.send_at.isoformat(), "seconds": seconds}


def ask_approval(c, o: m.Opportunity, p: dict, it: m.InboxItem | None, approver: str) -> dict:
    """The writer approved the email, and their emails need a second approval (api/people.py): it waits for the
    approver, holding exactly what the writer approved. Nothing is sent until the approver approves it."""
    from api import people as P
    from api import services as S
    row = m.OutboundEmail(id=S.new_id("out"), opportunity_id=o.id, item_id=it.id if it is not None else None, purpose=p["purpose"],
                          to=p["to"], subject=p["subject"], body=p["body"], seen_hash=p["seenHash"], by=c.actor,
                          send_at=utcnow(), status="approval", approver=approver)
    c.s.add(row)
    S.log(c, o.id, "h", f"{P.label(c)} approved the {p['purpose']} email to {p['to']}. It waits for {approver}'s approval "
                        "before it goes out.")
    return {"mode": "approval", "to": p["to"], "id": row.id, "approver": approver}


def second_decision(c, out_id: str, approve: bool, note: str, delay: int = 0) -> dict:
    """The second approver decides. Approve: the email goes out as its writer, from the writer's mailbox, after the undo
    window (delay). Return: nothing is sent; the writer sees the note, changes the draft and approves it again."""
    from api import people as P
    from api import services as S
    row = _row(c, out_id)
    if row.status != "approval":
        raise S.ActionError(f"That email is {row.status}; it is not waiting for a second approval.", 409, "wrong_status")
    if c.actor != row.approver:
        raise S.ActionError(f"Only {row.approver} (or an admin acting as {row.approver}) can approve or return this email.", 403, "not_approver")
    if not approve:
        note = (note or "").strip()
        if len(note) < 3:
            raise S.ActionError("Say what to change, so the writer knows.", 400, "no_note")
        if not _take(c, out_id, "returned", ("approval",)):
            raise S.ActionError("That email changed in the meantime. Look again.", 409, "wrong_status")
        c.s.refresh(row)
        row.error, row.ended_at = f"Returned by {P.label(c)}: {note}", utcnow()
        S.log(c, row.opportunity_id, "h", f"{P.label(c)} returned the {row.purpose} email to {row.to} that {row.by} approved. "
                                          f"Nothing was sent. {note}")
        return {"status": "returned", "id": row.id}
    o = c.s.get(m.Opportunity, row.opportunity_id)
    S.sender_box(c, o, row.purpose, row.by)  # it must still be able to go out as its writer
    seconds = window(delay)
    if not _take(c, out_id, "queued", ("approval",)):
        raise S.ActionError("That email changed in the meantime. Look again.", 409, "wrong_status")
    c.s.refresh(row)
    row.approved_by, row.approved_at, row.send_at = P.label(c), utcnow(), utcnow() + timedelta(seconds=seconds)
    S.log(c, row.opportunity_id, "h", f"{P.label(c)} gave the second approval to the {row.purpose} email to {row.to} from {row.by}."
                                      + (f" It goes out in {seconds} seconds unless undone." if seconds else ""))
    if not seconds:
        return send_now(c, out_id)
    return {"mode": "queued", "status": "queued", "to": row.to, "id": row.id, "sendAt": row.send_at.isoformat(), "seconds": seconds}


def _take(c, out_id: str, to: str, frm: tuple = ("queued",)) -> bool:
    res = c.s.execute(update(m.OutboundEmail).where(m.OutboundEmail.id == out_id, m.OutboundEmail.status.in_(frm)).values(status=to))
    return res.rowcount == 1


def _row(c, out_id: str) -> m.OutboundEmail:
    from api.services import ActionError
    row = c.s.get(m.OutboundEmail, out_id)
    if row is None:
        raise ActionError("That email was not found.", 404, "not_found")
    return row


def undo_email(c, out_id: str) -> dict:
    from api import services as S
    row = _row(c, out_id)
    was = row.status
    if not _take(c, out_id, "undone", ("queued", "approval")):  # the writer may also withdraw it while it waits for approval
        c.s.refresh(row)
        raise S.ActionError("Too late: the email already went out." if row.status in ("sending", "sent")
                            else f"That email was {row.status}.", 409, "too_late")
    c.s.refresh(row)
    row.ended_at = utcnow()
    S.log(c, row.opportunity_id, "h", f"{c.actor} {'withdrew' if was == 'approval' else 'undid'} the {row.purpose} email to {row.to}"
                                      + (f" before {row.approver} approved it" if was == "approval" else "") + ". Nothing was sent; the draft is unchanged.")
    return {"status": "undone"}


def send_now(c, out_id: str) -> dict:
    """The person does not want to wait for the window: send it in this request."""
    from api import services as S
    row = _row(c, out_id)
    if not _take(c, out_id, "sending"):
        c.s.refresh(row)
        raise S.ActionError(f"That email is already {row.status}.", 409, "wrong_status")
    c.s.commit()  # others see it is being sent
    return deliver(c, c.s.get(m.OutboundEmail, out_id))


def deliver(c, row: m.OutboundEmail) -> dict:
    """Send one approved email (its status is already sending). Checks again that it may still go out: the first email
    while the opportunity waits for it, an inbox draft while it is still valid. A failure is recorded, never retried."""
    from api import services as S
    o = c.s.get(m.Opportunity, row.opportunity_id)
    it = c.s.get(m.InboxItem, row.item_id) if row.item_id else None
    try:
        if row.purpose == "first" and o.stage != "identified":
            raise S.ActionError(f"The opportunity moved to {o.stage} before the email went out.", 409, "wrong_stage")
        if it is not None:
            if it.status not in ("new", "applied"):
                raise S.ActionError(f"The draft was {it.status} before the email went out.", 409, "wrong_status")
            if it.status == "new":
                from api import autonomy as AU
                AU.check_still_valid(c, it, o, it.payload_hash)
        res = S.deliver_email(c, o, {"purpose": row.purpose, "to": row.to, "subject": row.subject, "body": row.body}, it, "send",
                              S.sender_box(c, o, row.purpose, row.by),
                              how=f" by {row.by}" + (f", second approval by {row.approved_by}" if row.approved_by else ""))
    except S.ActionError as e:
        c.s.rollback()
        row = c.s.get(m.OutboundEmail, row.id)
        row.status, row.error, row.ended_at = "failed", str(e), utcnow()
        S.log(c, row.opportunity_id, "s", f"The {row.purpose} email to {row.to} approved by {row.by} was not sent: {e}")
        c.s.commit()
        raise
    row.status, row.sent_at = "sent", utcnow()
    row.ended_at = row.sent_at
    return {**res, "id": row.id}


def send_due(app) -> int:
    """Send the approved emails whose window has passed. Returns how many went out."""
    from api.autonomy import agent_ctx
    from api import crm_sync as CRM
    from api.services import ActionError
    now, sent = utcnow(), 0
    with app.state.db.session() as s:
        due = [r.id for r in s.scalars(select(m.OutboundEmail).where(m.OutboundEmail.status == "queued")) if aware(r.send_at) <= now]
    for out_id in due:
        with app.state.db.session() as s:
            c = agent_ctx(app, s)
            row = s.get(m.OutboundEmail, out_id)
            c.actor = row.by
            if now - aware(row.send_at) > STALE:
                if _take(c, out_id, "expired"):
                    row = s.get(m.OutboundEmail, out_id)
                    row.error, row.ended_at = "The workspace was stopped before it went out.", utcnow()
                    from api.services import log
                    log(c, row.opportunity_id, "s", f"The {row.purpose} email to {row.to} approved by {row.by} was not sent: the "
                                                    "workspace was stopped before it went out. Approve it again to send it.")
                    s.commit()
                continue
            if not _take(c, out_id, "sending"):
                continue  # undone or sent now in the meantime
            s.commit()
            try:
                deliver(c, s.get(m.OutboundEmail, out_id))
                s.commit()
                sent += 1
            except ActionError:
                continue
            dirty, today = set(c.crm_dirty), c.today
        if dirty:
            CRM.after_request(app, dirty, today)
    return sent


class Clock:
    """Ends undo windows once a second: sends approved emails and releases held Step 4 decisions."""

    def __init__(self, app, tick: float = 1.0):
        self.app, self.tick, self.stop = app, tick, threading.Event()
        self.thread = threading.Thread(target=self.loop, name="cgs-undo-clock", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def loop(self):
        while not self.stop.wait(self.tick):
            try:
                send_due(self.app)
                release_due(self.app)
            except Exception:  # keep the clock alive; the error is visible in the server window
                log_.exception("undo clock")
