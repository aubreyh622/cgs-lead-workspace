"""Automatic searches: what the discovery agent looks for on its own, how often and how many companies each run.

The main search uses the research direction alone; it replaces the fixed weekly round (B2, 5 Nov 2026). People add
others with their own request ("New Chief Digital Officers"); each runs with its creator's direction. The daily check
(api/autonomy.patrol) queues one discovery job when any search is due, and that job runs each due search as its own
round. A run finds at most per_run companies, which land in Companies, New candidates, for a person to pick or skip.
Searches run at the daily check, not at a set hour.
"""
from __future__ import annotations

from datetime import date, timedelta
import uuid

from sqlalchemy import select

from api import jobs as J
from api import models as m

FREQ = {"day": 1, "3days": 3, "week": 7, "2weeks": 14, "4weeks": 28}
FREQ_LABEL = {"day": "Every day", "3days": "Every 3 days", "week": "Every week", "2weeks": "Every 2 weeks", "4weeks": "Every 4 weeks"}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
MAX_PER_RUN = 10      # the discovery agent's nomination limit (api/discovery_agent.MAX_CANDIDATES)
KEEP_ROUNDS = 12
MAIN_NAME = "What we look for (main search)"


class Invalid(ValueError):
    """A search that does not pass the checks; the message says why."""


def ensure_main(s, today: date) -> m.DiscoverySearch:
    x = s.get(m.DiscoverySearch, "main")
    if x is None:
        last = J.setting(s, "last_discovery")
        x = m.DiscoverySearch(id="main", name=MAIN_NAME, query="", freq="week", weekday=None, per_run=MAX_PER_RUN, on=True,
                              main=True, created_by="", created_on=today, rounds=[],
                              last_run_on=date.fromisoformat(last) if last else None)
        s.add(x)
        s.flush()
    return x


def last_run(s, x: m.DiscoverySearch) -> date | None:
    """The main search also counts the older weekly round's date (setting last_discovery), which checks still set."""
    if not x.main:
        return x.last_run_on
    old = J.setting(s, "last_discovery")
    old = date.fromisoformat(old) if old else None
    return max([d for d in (x.last_run_on, old) if d], default=None)


def next_run(s, x: m.DiscoverySearch, today: date) -> date | None:
    """None when switched off. Never run: today, or the next chosen weekday. Otherwise the last run plus the interval,
    moved forward to the chosen weekday."""
    if not x.on:
        return None
    last = last_run(s, x)
    base = today if last is None else last + timedelta(days=FREQ[x.freq])
    if x.weekday is not None and x.freq not in ("day", "3days"):
        base += timedelta(days=(x.weekday - base.weekday()) % 7)
    return base


def due(s, today: date) -> list[m.DiscoverySearch]:
    ensure_main(s, today)
    rows = s.scalars(select(m.DiscoverySearch).order_by(m.DiscoverySearch.main.desc(), m.DiscoverySearch.created_on)).all()
    return [x for x in rows if (n := next_run(s, x, today)) is not None and n <= today]


def check(name: str, query: str, freq: str, weekday: int | None, per_run: int, main: bool) -> tuple[str, str]:
    name, query = " ".join((name or "").split()), " ".join((query or "").split())
    if not main:
        if not 3 <= len(name) <= 120:
            raise Invalid("Give the search a name (3-120 characters).")
        if not 10 <= len(query) <= 600:
            raise Invalid("Say what to look for, as you would to a researcher (10-600 characters).")
    if freq not in FREQ:
        raise Invalid(f"How often is one of {', '.join(FREQ)}.")
    if weekday is not None and (freq in ("day", "3days") or not 0 <= weekday <= 4):
        raise Invalid("A weekday (Monday to Friday) applies only to weekly or less frequent searches.")
    if not 1 <= per_run <= MAX_PER_RUN:
        raise Invalid(f"Companies per run is 1-{MAX_PER_RUN}.")
    return name, query


def create(s, today: date, by: str, name: str, query: str, freq: str, weekday: int | None, per_run: int) -> m.DiscoverySearch:
    name, query = check(name, query, freq, weekday, per_run, False)
    x = m.DiscoverySearch(id=f"ds-{uuid.uuid4().hex[:8]}", name=name, query=query, freq=freq, weekday=weekday, per_run=per_run,
                          on=True, main=False, created_by=by, created_on=today, rounds=[])
    s.add(x)
    s.flush()
    return x


def update(s, x: m.DiscoverySearch, fields: dict) -> m.DiscoverySearch:
    """fields: any of name, query, freq, weekday, per_run, on. The main search keeps its name and has no query."""
    new = {k: getattr(x, k) for k in ("name", "query", "freq", "weekday", "per_run", "on")}
    new.update({k: v for k, v in fields.items() if k in new and not (x.main and k in ("name", "query"))})
    name, query = check(new["name"], new["query"], new["freq"], new["weekday"], new["per_run"], x.main)
    x.name, x.query, x.freq, x.weekday, x.per_run, x.on = name, query, new["freq"], new["weekday"], new["per_run"], bool(new["on"])
    return x


def run(c, x: m.DiscoverySearch, budget=None) -> str:
    """One round for this search. Returns a sentence for the job's result. A failure is kept on the search and re-raised."""
    from api import services as S
    try:
        res = S.recommend(c, x.query or "Scheduled round by the discovery agent", budget=budget, count=x.per_run,
                          person=x.created_by or None)
    except Exception as e:
        x.last_error = str(e)[:500]
        raise
    x.last_run_on, x.last_error = c.today, None
    x.rounds = (list(x.rounds or []) + [{"round": res["round"], "on": c.today.isoformat(), "found": res["candidates"]}])[-KEEP_ROUNDS:]
    if x.main:
        J.set_setting(c.s, "last_discovery", c.today.isoformat())
    if not res["candidates"]:
        return f"{x.name}, round {res['round']}: no new candidates."
    return f"{x.name}, round {res['round']}: {res['candidates']} candidate{'s' if res['candidates'] != 1 else ''}."


def view(s, x: m.DiscoverySearch, today: date) -> dict:
    rounds = list(x.rounds or [])
    nums = [r["round"] for r in rounds]
    cands = s.scalars(select(m.Candidate).where(m.Candidate.round.in_(nums))).all() if nums else []
    last = rounds[-1] if rounds else None
    nxt, lr = next_run(s, x, today), last_run(s, x)
    return {"id": x.id, "name": x.name, "query": x.query, "main": x.main, "freq": x.freq, "freqLabel": FREQ_LABEL[x.freq],
            "weekday": x.weekday, "weekdayLabel": WEEKDAYS[x.weekday] if x.weekday is not None else None, "perRun": x.per_run,
            "on": x.on, "createdBy": x.created_by, "createdOn": x.created_on.isoformat(),
            "lastRunOn": lr.isoformat() if lr else None, "nextRunOn": nxt.isoformat() if nxt else None,
            "lastRound": last, "found": len(cands), "picked": sum(cd.status == "sent" for cd in cands),
            "skipped": sum(cd.status == "skipped" for cd in cands), "lastError": x.last_error}
