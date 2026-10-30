"""What earlier discovery rounds taught: which searches and signals led to candidates people picked and accepted.

No AI and no new table: everything is counted from the candidates (Step 1: picked or skipped, with the reason) and the
opportunities they became (Step 4: accepted, rejected or still waiting). The discovery agent sees the result in its
direction tool as hints ("what_worked_before"), never as rules; a person still picks every candidate.

Outcome of one candidate:
  new       nobody has looked at it yet (not counted)
  picked    picked at Step 1; its opportunity is still being researched or reviewed
  skipped   a person skipped it at Step 1 (skip_reason says why)
  accepted  picked, and the opportunity was accepted at Step 4 (any stage after review except rejected)
  rejected  picked, and the opportunity was rejected at Step 4
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from api import models as m

RECENT = 300          # candidates counted (newest first)
MIN_DECIDED = 3       # fewer decided candidates than this: no hints yet
ACCEPTED = {"identified", "contacted", "active", "approval", "qualification", "nurture", "closed"}
WAITING = {"researching", "review"}


def _key(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def outcome(cand: m.Candidate, opp: m.Opportunity | None) -> str:
    if cand.status == "skipped":
        return "skipped"
    if cand.status != "sent":
        return "new"
    if opp is None or opp.stage in WAITING:
        return "picked"
    return "rejected" if opp.stage == "rejected" else "accepted"


def outcomes(s: Session, limit: int = RECENT) -> list[dict]:
    cands = list(s.scalars(select(m.Candidate).order_by(m.Candidate.round.desc()).limit(limit)))
    opps = {o.candidate_id: o for o in s.scalars(select(m.Opportunity).where(m.Opportunity.candidate_id.is_not(None)))}
    return [{"id": c.id, "round": c.round, "company": c.company, "query": c.source_query or "", "signal": c.signal or "",
             "problem_id": c.problem_id or "", "skip_reason": c.skip_reason or "", "outcome": outcome(c, opps.get(c.id))}
            for c in cands]


def _tally(rows: list[dict], field: str) -> list[dict]:
    by: dict[str, Counter] = defaultdict(Counter)
    label: dict[str, str] = {}
    for r in rows:
        k = _key(r[field])
        if not k:
            continue
        label.setdefault(k, r[field].strip())
        by[k]["nominated"] += 1
        by[k][r["outcome"]] += 1
    out = [{field: label[k], "nominated": n["nominated"], "picked": n["accepted"] + n["rejected"] + n["picked"],
            "accepted": n["accepted"], "rejected_at_step_4": n["rejected"], "skipped": n["skipped"]} for k, n in by.items()]
    return sorted(out, key=lambda x: (-x["accepted"], x["skipped"] - x["picked"], -x["nominated"]))


def summary(s: Session, limit: int = RECENT) -> dict:
    """GET /api/discovery/memory and the discovery agent's what_worked_before."""
    rows = [r for r in outcomes(s, limit) if r["outcome"] != "new"]
    decided = [r for r in rows if r["outcome"] in ("accepted", "rejected", "skipped")]
    queries, signals = _tally(rows, "query"), _tally(rows, "signal")
    reasons = Counter(_key(r["skip_reason"]) for r in rows if r["outcome"] == "skipped" and r["skip_reason"])
    return {
        "candidates_counted": len(rows), "decided": len(decided),
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "queries_that_led_to_accepted_leads": [q for q in queries if q["accepted"]][:6],
        "queries_that_led_to_nothing": [q for q in queries if not q["picked"] and q["skipped"] >= 2][:6],
        "signals": signals[:10],
        "common_skip_reasons": [{"reason": k, "times": n} for k, n in reasons.most_common(5)],
    }


def for_agent(s: Session) -> dict | None:
    """The hints the discovery agent sees, or None while too few candidates have been decided."""
    sm = summary(s)
    if sm["decided"] < MIN_DECIDED:
        return None
    return {"note": "Counted from earlier rounds: what people picked at Step 1 and accepted at Step 4. Hints, not rules.",
            **{k: sm[k] for k in ("decided", "queries_that_led_to_accepted_leads", "queries_that_led_to_nothing",
                                  "signals", "common_skip_reasons")}}
