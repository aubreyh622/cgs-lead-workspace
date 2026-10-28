"""Lessons for the agents (multi-agent step 7, Phase 3). Rules only: no AI here; the weekly review is api/lessons_agent.py.

How the system learns, with a person in the loop:
  1. findings   code reads the scorecard (api/scorecard.py) and lists what stands out, with the numbers and the reviewers'
                own words: a score reviewers keep raising, work owners keep dismissing, manager decisions the code keeps refusing
  2. proposal   once a week the manager turns findings into at most three lessons, one or two sentences addressed to one agent.
                People can propose lessons too
  3. approval   a person approves or rejects each lesson (only CGS_ADMINS when that is set). Nothing reaches an agent before
  4. prompt     approved lessons are added to that agent's prompt, under the rules, at most five per agent
  5. effect     each approved lesson shows its finding's numbers then and since it was approved; a person retires a lesson
                that no longer helps

Lessons refine how an agent works; they never loosen a rule. The prompt block says the rules come first, and code refuses
lesson text that tells an agent to ignore, override, bypass or skip anything, or that names fees or promises.
"""
from __future__ import annotations

from datetime import date, timedelta
import re

from sqlalchemy import select

from api import models as m
from api import scorecard as SC

TARGETS = ("Evidence agent", "Outreach agent", "Next-step agent", "Manager agent")  # agents whose prompts take lessons
MAX_ACTIVE = 5          # approved lessons per agent
MAX_PER_REVIEW = 3      # lessons the weekly review may propose
TEXT_MIN, TEXT_MAX = 20, 400
REFUSED_SHARE = 0.3     # manager decisions the code refused, as a share of at least MIN_SAMPLE
LOOSENS = re.compile(r"\b(ignore|ignoring|override|bypass|skip|skipping|disregard)\b", re.I)


class Invalid(ValueError):
    """A lesson change that does not pass the checks; the message says why."""


def label(lesson: m.AgentLesson) -> str:
    return f"L{lesson.id}"


# ---------- 1. findings ----------
def _evidence_reasons(s, key: str) -> list[str]:
    rows = s.scalars(select(m.Proposal).where(m.Proposal.key == key, m.Proposal.status.in_(("edited", "rejected")),
                                              m.Proposal.reason.is_not(None)).order_by(m.Proposal.decided_at.desc()).limit(5))
    out = []
    for p in rows:
        move = f"{p.value} -> {p.final_value}" if p.status == "edited" else f"{p.value} rejected"
        out.append(f"{move}: {p.reason}"[:300])
    return out


def findings(s, today: date, days: int = SC.WINDOW_DAYS) -> list[dict]:
    """What stands out in the scorecard, each with a ref, the agent a lesson would address, the numbers and the reviewers' words."""
    out = []
    for r in SC.evidence(s, today, days)["keys"]:
        if r["findings"]:
            out.append({"ref": f"evidence:{r['key']}", "agent": "Evidence agent",
                        "summary": f"{r['key']}: " + "; ".join(r["findings"]),
                        "numbers": {k: r[k] for k in ("decided", "accepted", "raised", "lowered", "rejected", "byHandNeverProposed")},
                        "reviewer_reasons": _evidence_reasons(s, r["key"])})
    for r in SC.work(s, today, days):
        if r["enough"] and (r["usefulRate"] <= SC.LOW_USEFUL or r["ignoredRate"] >= SC.HIGH_IGNORED):
            agent = r["agent"] if r["agent"] in TARGETS else "Manager agent"
            out.append({"ref": f"work:{r['work']}", "agent": agent,
                        "summary": f"{r['work']} ({r['agent']}): people used {r['used']} of {r['judged']}, dismissed {r['dismissed']}, "
                                   f"left {r['ignored']} unread until they expired",
                        "numbers": {k: r[k] for k in ("judged", "used", "dismissed", "ignored", "usefulRate", "ignoredRate")},
                        "reviewer_reasons": []})
    mg = manager_numbers(s, today, days)
    if mg["decisions"] >= SC.MIN_SAMPLE and mg["refused"] / mg["decisions"] >= REFUSED_SHARE:
        details = [d.detail for d in s.scalars(select(m.ManagerDecision).where(
            m.ManagerDecision.on >= today - timedelta(days=days), m.ManagerDecision.outcome == "refused").order_by(m.ManagerDecision.id.desc()))]
        out.append({"ref": "manager:refused", "agent": "Manager agent",
                    "summary": f"the code refused {mg['refused']} of the manager's {mg['decisions']} decisions",
                    "numbers": mg, "reviewer_reasons": list(dict.fromkeys(details))[:5]})
    return out


def manager_numbers(s, today: date, days: int) -> dict:
    ds = list(s.scalars(select(m.ManagerDecision).where(m.ManagerDecision.on >= today - timedelta(days=days))))
    return {"decisions": len(ds), "refused": sum(d.outcome == "refused" for d in ds)}


def numbers(s, today: date, ref: str, days: int) -> dict | None:
    """The current numbers behind a ref, over the last `days` days (None for a person's lesson or an unknown ref)."""
    kind, _, key = ref.partition(":")
    if kind == "evidence":
        r = next((x for x in SC.evidence(s, today, days)["keys"] if x["key"] == key), None)
        return {k: r[k] for k in ("decided", "accepted", "raised", "lowered", "rejected", "byHandNeverProposed")} if r else \
            {"decided": 0, "accepted": 0, "raised": 0, "lowered": 0, "rejected": 0, "byHandNeverProposed": 0}
    if kind == "work":
        r = next((x for x in SC.work(s, today, days) if x["work"] == key), None)
        return {k: r[k] for k in ("judged", "used", "dismissed", "ignored", "usefulRate", "ignoredRate")} if r else {"judged": 0}
    if ref == "manager:refused":
        return manager_numbers(s, today, days)
    return None


# ---------- 2. proposals ----------
def check_text(text: str) -> str:
    from api.next_step_agent import promises
    text = " ".join((text or "").split())
    if not TEXT_MIN <= len(text) <= TEXT_MAX:
        raise Invalid(f"A lesson is {TEXT_MIN}-{TEXT_MAX} characters: one or two sentences to the agent, saying what to do in which situation.")
    if LOOSENS.search(text):
        raise Invalid("A lesson may not tell an agent to ignore, override, bypass or skip anything. Lessons refine the rules; they never loosen them.")
    if promises(text):
        raise Invalid("A lesson may not mention fees, percentages, discounts or guarantees.")
    return text


def _similar_open(s, agent: str, text: str) -> m.AgentLesson | None:
    from api.next_step_agent import similar
    for x in s.scalars(select(m.AgentLesson).where(m.AgentLesson.agent == agent, m.AgentLesson.status.in_(("proposed", "approved")))):
        if similar(text, x.text):
            return x
    return None


def propose(s, today: date, agent: str, text: str, ref: str, why: str, by: str, basis: dict | None = None,
            run_id: str | None = None) -> m.AgentLesson:
    if agent not in TARGETS:
        raise Invalid(f"Lessons are for {', '.join(TARGETS)}.")
    text = check_text(text)
    why = " ".join((why or "").split())
    if len(why) < 10:
        raise Invalid("Say why, in a sentence: what was seen that the lesson answers.")
    dup = _similar_open(s, agent, text)
    if dup:
        raise Invalid(f"{label(dup)} already says this ({dup.status}).")
    lesson = m.AgentLesson(agent=agent, text=text, ref=ref[:120], basis=basis or {}, why=why[:600], status="proposed",
                           proposed_by=by, proposed_on=today, run_id=run_id)
    s.add(lesson)
    s.flush()
    return lesson


# ---------- 3. approval ----------
def _get(s, lesson_id: int) -> m.AgentLesson:
    lesson = s.get(m.AgentLesson, lesson_id)
    if not lesson:
        raise Invalid(f"Lesson L{lesson_id} not found.")
    return lesson


def decide(s, today: date, lesson_id: int, decision: str, reason: str, by: str) -> m.AgentLesson:
    lesson = _get(s, lesson_id)
    if lesson.status != "proposed":
        raise Invalid(f"{label(lesson)} is already {lesson.status}.")
    if decision not in ("approve", "reject"):
        raise Invalid("decision is approve or reject.")
    reason = " ".join((reason or "").split())
    if decision == "reject" and len(reason) < 5:
        raise Invalid("Say why it is rejected; the manager reads it before it proposes again.")
    if decision == "approve":
        active = s.scalars(select(m.AgentLesson).where(m.AgentLesson.agent == lesson.agent, m.AgentLesson.status == "approved")).all()
        if len(active) >= MAX_ACTIVE:
            raise Invalid(f"{lesson.agent} already has {MAX_ACTIVE} approved lessons ({', '.join(label(x) for x in active)}). Retire one first.")
    lesson.status = "approved" if decision == "approve" else "rejected"
    lesson.decided_by, lesson.decided_on, lesson.decision_reason = by, today, reason[:600]
    return lesson


def retire(s, today: date, lesson_id: int, reason: str, by: str) -> m.AgentLesson:
    lesson = _get(s, lesson_id)
    if lesson.status != "approved":
        raise Invalid(f"Only an approved lesson can be retired; {label(lesson)} is {lesson.status}.")
    reason = " ".join((reason or "").split())
    if len(reason) < 5:
        raise Invalid("Say why it is retired (it no longer helps, or the rule it answered changed).")
    lesson.status, lesson.retired_by, lesson.retired_on, lesson.retired_reason = "retired", by, today, reason[:600]
    return lesson


# ---------- 4. prompt ----------
def block(s, agent: str, today: date | None = None) -> str:
    """The approved lessons for an agent, as a section for the end of its prompt ("" when there are none).
    Each use is counted, so a person can see which lessons are in play."""
    rows = list(s.scalars(select(m.AgentLesson).where(m.AgentLesson.agent == agent, m.AgentLesson.status == "approved")
                          .order_by(m.AgentLesson.id)))
    if not rows:
        return ""
    for x in rows:
        x.uses = (x.uses or 0) + 1
        x.last_used_on = today or x.last_used_on
    lines = "\n".join(f"- [{label(x)}] {x.text}" for x in rows)
    return ("\n\nLessons approved by CGS reviewers from how people received your work. They refine how you work in the "
            "situations they name; every rule above still comes first.\n" + lines)


# ---------- 5. view and effect ----------
def view(s, lesson: m.AgentLesson, today: date) -> dict:
    out = {"id": lesson.id, "label": label(lesson), "agent": lesson.agent, "text": lesson.text, "ref": lesson.ref,
           "basis": lesson.basis, "why": lesson.why, "status": lesson.status, "proposedBy": lesson.proposed_by,
           "proposedOn": lesson.proposed_on.isoformat(), "decidedBy": lesson.decided_by,
           "decidedOn": lesson.decided_on.isoformat() if lesson.decided_on else None, "decisionReason": lesson.decision_reason,
           "retiredBy": lesson.retired_by, "retiredOn": lesson.retired_on.isoformat() if lesson.retired_on else None,
           "retiredReason": lesson.retired_reason, "uses": lesson.uses or 0,
           "lastUsedOn": lesson.last_used_on.isoformat() if lesson.last_used_on else None}
    if lesson.status in ("approved", "retired") and lesson.decided_on:
        until = lesson.retired_on or today
        days = (until - lesson.decided_on).days
        out["sinceApproved"] = {"days": days, "numbers": numbers(s, until, lesson.ref, days) if days > 0 else None}
    return out
