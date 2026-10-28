"""Agent scorecards (multi-agent step 7, Phase 2): how people received the agents' work. Rules only: no AI here.

Everything comes from records the workspace already keeps:
  work         every inbox item an agent prepared, grouped by the job that produced it (research_refresh, followup_draft ...),
               and what a person did with it: used it (applied or marked done), dismissed it, or left it unread until it expired
  owners       the same, per owner, so the manager can see that one consultant keeps dismissing one kind of work
  evidence     the evidence agent's proposals as reviewers decided them: accepted, raised (the agent scored too low),
               lowered (too high), rejected, and values a person entered by hand that the agent never proposed
  reliability  agent runs that failed, jobs that failed or were skipped
  manager      the manager's own decisions by outcome (queued, refused, declined ...)

A rate is only judged when it rests on at least MIN_SAMPLE decisions; below that the row says so. The manager sees the
owner rows (api/manager_agent.py, api/manager_triage.py) and gets a caution, not a refusal, when it hands an owner work
they usually dismiss or ignore. People decide what to change; Phase 3 will turn findings into lessons a person approves.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select

from api import models as m

WINDOW_DAYS = 60
MIN_SAMPLE = 5
LOW_USEFUL = 0.25       # an owner found at most a quarter of this work useful
HIGH_IGNORED = 0.5      # or left at least half of it unread until it expired
OFTEN = 0.3             # reviewers raised (or lowered and rejected) at least this share of one key's proposals


def _outcome(item: m.InboxItem) -> str:
    """used | dismissed | ignored | replaced | waiting. Only used and dismissed are a person's judgement; ignored counts too
    (nobody looked in two weeks), replaced does not (a newer version took its place)."""
    by = item.decided_by or ""
    if item.status == "new":
        return "waiting"
    if item.status in ("applied", "done"):
        return "used"
    if by.startswith("Expired"):
        return "ignored"
    if by.startswith("Replaced"):
        return "replaced"
    return "dismissed"


def _work_key(item: m.InboxItem, jobs: dict[str, str]) -> str:
    """The kind of work: the job that produced the item, or, for items made outside a job, the agent's own word for it."""
    if item.job_id and item.job_id in jobs:
        return jobs[item.job_id]
    return {"Manager agent": "manager_note", "Mail rules": "client_reply"}.get(item.agent, f"{item.kind}")


def _rates(row: dict) -> dict:
    judged = row["used"] + row["dismissed"] + row["ignored"]
    row["judged"] = judged
    row["usefulRate"] = round(row["used"] / judged, 2) if judged else None
    row["ignoredRate"] = round(row["ignored"] / judged, 2) if judged else None
    row["enough"] = judged >= MIN_SAMPLE
    return row


def _items(s, since: date, owner: str | None = None):
    q = select(m.InboxItem).where(m.InboxItem.created_on >= since)
    if owner:
        q = q.where(m.InboxItem.owner == owner)
    items = list(s.scalars(q))
    ids = {i.job_id for i in items if i.job_id}
    jobs = {j.id: j.kind for j in s.scalars(select(m.AgentJob).where(m.AgentJob.id.in_(ids)))} if ids else {}
    return items, jobs


def work(s, today: date, days: int = WINDOW_DAYS, owner: str | None = None, by_owner: bool = False) -> list[dict]:
    """One row per kind of work (and per owner when by_owner): prepared, used, dismissed, ignored, replaced, waiting, rates."""
    items, jobs = _items(s, today - timedelta(days=days), owner)
    rows: dict[tuple, dict] = {}
    for it in items:
        key = (it.owner if by_owner else None, _work_key(it, jobs))
        r = rows.setdefault(key, {**({"owner": it.owner} if by_owner else {}), "work": key[1], "agent": it.agent, "prepared": 0,
                                  "used": 0, "dismissed": 0, "ignored": 0, "replaced": 0, "waiting": 0})
        r["prepared"] += 1
        r[_outcome(it)] += 1
    out = [_rates(r) for r in rows.values() if not (by_owner and r.get("owner") is None)]
    return sorted(out, key=lambda r: (r.get("owner") or "", -r["prepared"], r["work"]))


def caution(s, today: date, owner: str | None, kind: str, days: int = WINDOW_DAYS) -> str | None:
    """A sentence for the manager when this owner usually dismisses or ignores this kind of work, else None."""
    if not owner:
        return None
    row = next((r for r in work(s, today, days, owner=owner) if r["work"] == kind), None)
    if not row or not row["enough"]:
        return None
    if row["usefulRate"] <= LOW_USEFUL or row["ignoredRate"] >= HIGH_IGNORED:
        return (f"In the last {days} days {owner} used {row['used']} of {row['judged']} {kind} results "
                f"({row['dismissed']} dismissed, {row['ignored']} left unread until they expired).")
    return None


def for_owner(s, today: date, owner: str | None, days: int = WINDOW_DAYS) -> list[dict]:
    """What the manager sees about one owner: only the kinds of work with enough decisions to judge."""
    if not owner:
        return []
    return [{"work": r["work"], "used": r["used"], "dismissed": r["dismissed"], "ignored": r["ignored"], "useful_rate": r["usefulRate"]}
            for r in work(s, today, days, owner=owner) if r["enough"]]


def _number(v):
    if isinstance(v, bool):
        return int(v)
    return v if isinstance(v, (int, float)) else None


def evidence(s, today: date, days: int = WINDOW_DAYS) -> dict:
    """How reviewers decided the evidence agent's proposals, per key, and what they entered by hand that it never proposed.
    The window follows the date of the note a proposal came from, and the date a value was entered by hand."""
    since = today - timedelta(days=days)
    notes = {n.id: n.on for n in s.scalars(select(m.Note).where(m.Note.on >= since))}
    rows: dict[str, dict] = defaultdict(lambda: {"decided": 0, "accepted": 0, "raised": 0, "lowered": 0, "editedOther": 0,
                                                 "rejected": 0, "byHandNeverProposed": 0})
    proposed: set[tuple[str, str]] = set()
    for p in s.scalars(select(m.Proposal)):
        proposed.add((p.opportunity_id, p.key))
        if p.status == "pending" or (p.note_id and p.note_id not in notes) or (not p.note_id):
            continue
        r = rows[p.key]
        r["decided"] += 1
        if p.status == "accepted":
            r["accepted"] += 1
        elif p.status == "rejected":
            r["rejected"] += 1
        else:  # edited: which way did the person move it?
            a, b = _number(p.value), _number(p.final_value)
            r["raised" if a is not None and b is not None and b > a else "lowered" if a is not None and b is not None and b < a else "editedOther"] += 1
    agent_opps = {o for o, _ in proposed}
    for e in s.scalars(select(m.Evidence).where(m.Evidence.how == "manual review", m.Evidence.on >= since,
                                                m.Evidence.kind.in_(("score", "cond")))):
        if e.opportunity_id in agent_opps and (e.opportunity_id, e.key) not in proposed:
            rows[e.key]["byHandNeverProposed"] += 1
    out = []
    for key, r in sorted(rows.items()):
        d = r["decided"]
        findings = []
        if d >= MIN_SAMPLE and r["raised"] / d >= OFTEN:
            findings.append("reviewers often raised it: the agent scores this lower than people do")
        if d >= MIN_SAMPLE and (r["lowered"] + r["rejected"]) / d >= OFTEN:
            findings.append("reviewers often lowered or rejected it: the agent claims more than people accept")
        if r["byHandNeverProposed"] >= MIN_SAMPLE:
            findings.append("people often entered it by hand where the agent proposed nothing")
        out.append({"key": key, **r, "enough": d >= MIN_SAMPLE, "findings": findings})
    totals = {k: sum(r[k] for r in rows.values()) for k in ("decided", "accepted", "raised", "lowered", "editedOther", "rejected", "byHandNeverProposed")}
    return {"keys": out, "totals": totals}


def reliability(s, today: date, days: int = WINDOW_DAYS) -> dict:
    since = today - timedelta(days=days)
    runs: dict[str, dict] = defaultdict(lambda: {"runs": 0, "failed": 0, "aiCalls": 0})
    for r in s.scalars(select(m.AgentRun).where(m.AgentRun.on >= since)):
        x = runs[r.agent]
        x["runs"] += 1
        x["failed"] += r.status == "failed"
        x["aiCalls"] += (r.calls or 0) if not (r.model or "").startswith("demo") else 0
    jobs: dict[str, dict] = defaultdict(lambda: {"done": 0, "failed": 0, "skipped": 0, "queued": 0, "running": 0})
    for j in s.scalars(select(m.AgentJob).where(m.AgentJob.created_on >= since)):
        jobs[j.kind][j.status] = jobs[j.kind].get(j.status, 0) + 1
    return {"runs": [{"agent": a, **v} for a, v in sorted(runs.items())], "jobs": [{"kind": k, **v} for k, v in sorted(jobs.items())]}


def manager(s, today: date, days: int = WINDOW_DAYS) -> list[dict]:
    since = today - timedelta(days=days)
    rows: dict[tuple, int] = defaultdict(int)
    for d in s.scalars(select(m.ManagerDecision).where(m.ManagerDecision.on >= since)):
        rows[(d.scope or "review", d.mode, d.outcome)] += 1
    return [{"scope": k[0], "mode": k[1], "outcome": k[2], "count": v} for k, v in sorted(rows.items())]


def build(s, today: date, days: int = WINDOW_DAYS, owner: str | None = None) -> dict:
    """The whole scorecard (GET /api/manager/scorecard)."""
    return {"today": today.isoformat(), "days": days, "since": (today - timedelta(days=days)).isoformat(), "minSample": MIN_SAMPLE,
            "work": work(s, today, days, owner=owner), "owners": work(s, today, days, owner=owner, by_owner=True),
            "evidence": evidence(s, today, days), "reliability": reliability(s, today, days), "manager": manager(s, today, days)}
