"""The qualification scoring criteria an admin may change (client meeting 4: an admin, once signed in,
can change the scoring criteria).

What can change: the weight of each dimension (B A N T H E, summing to 100), each dimension's minimum score (its floor,
or none), what Ready needs (confirmed coverage and confirmed Q), and the priority bands on P. The dimensions, their
anchors and the conditions stay as they are: the agents and the evidence checklist are built on them.

Each change is a new rule version (cgs-1, cgs-2, ...), stored in Setting "scoring"; earlier versions stay in
"scoring_history". Packs keep the version they were scored with. "Back to the built-in criteria" is a change too.
The rules engine (backend/rules.py) stays the only place that calculates; this module only hands it the numbers.
Every request checks the stored version (refresh), so all server instances use the same criteria."""
from __future__ import annotations

import backend.rules as R
from api import models as m
from api import people as P


def stored(s) -> dict | None:
    row = s.get(m.Setting, "scoring")
    return dict(row.value) if row and row.value else None


def refresh(s) -> None:
    """Make the rules engine use the stored criteria (built-in ones when none are stored)."""
    want = stored(s)
    if (want or R.DEFAULT)["version"] != R.version():
        R.apply(want)


def meta(s) -> dict | None:
    """Who changed the criteria last, when and why; None while the built-in ones apply."""
    cur = stored(s)
    return {k: cur.get(k) for k in ("version", "by", "on", "reason")} if cur else None


def view(c) -> dict:
    refresh(c.s)
    row = c.s.get(m.Setting, "scoring_history")
    return {"current": {"version": R.version(), "weights": dict(R.WEIGHTS), "floors": dict(R.FLOORS), "ready": dict(R.READY),
                        "bands": dict(R.BANDS), "labels": dict(R.LABELS)},
            "builtIn": R.DEFAULT, "changed": meta(c.s), "history": list(reversed(list(row.value if row else [])))[:20],
            "canChange": P.is_admin(c.s, c.settings, c.signed_in or c.actor), "admins": P.admins(c.s, c.settings)}


def _whole(value, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value) or not low <= value <= high:
        raise P.RoleError(f"{what} must be a whole number from {low} to {high}.", 400, "invalid")
    return int(value)


def check(weights: dict, floors: dict, ready: dict, bands: dict) -> dict:
    keys = list(R.DEFAULT["weights"])
    if set(weights) != set(keys):
        raise P.RoleError(f"Give a weight for each of {', '.join(keys)}.", 400, "invalid")
    w = {k: _whole(weights[k], 0, 100, f"The weight of {R.LABELS[k]}") for k in keys}
    if sum(w.values()) != 100:
        raise P.RoleError(f"The weights must add up to 100; these add up to {sum(w.values())}.", 400, "invalid")
    unknown = set(floors) - set(keys)
    if unknown:
        raise P.RoleError(f"No dimension called {sorted(unknown)[0]}.", 400, "invalid")
    f = {k: _whole(floors[k], 0, 5, f"The minimum for {R.LABELS[k]}") for k in keys if floors.get(k) not in (None, "")}
    r = {"coverage": _whole(ready.get("coverage"), 0, 100, "Confirmed coverage for Ready"),
         "q": _whole(ready.get("q"), 0, 100, "Confirmed Q for Ready")}
    b = {"High": _whole(bands.get("High"), 1, 100, "The High band"), "Medium": _whole(bands.get("Medium"), 0, 99, "The Medium band")}
    if b["Medium"] >= b["High"]:
        raise P.RoleError("The High band must start above the Medium band.", 400, "invalid")
    return {"weights": w, "floors": f, "ready": r, "bands": b}


def change(c, weights: dict | None, floors: dict | None, ready: dict | None, bands: dict | None, reason: str,
           built_in: bool = False) -> dict:
    """An admin saves new criteria (or goes back to the built-in ones). Every opportunity is scored with them from now on."""
    from api.services import log
    P.require_admin(c, "change the scoring criteria")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise P.RoleError("Say why, in a few words: the team sees it beside the criteria.", 400, "no_reason")
    refresh(c.s)
    new = dict(R.DEFAULT) if built_in else check(weights or {}, floors or {}, ready or {}, bands or {})
    before = stored(c.s)
    same = all((before or R.DEFAULT)[k] == new[k] for k in ("weights", "floors", "ready", "bands"))
    if same:
        raise P.RoleError("Nothing changed: these are the criteria in use.", 409, "unchanged")
    hist = c.s.get(m.Setting, "scoring_history")
    past = list(hist.value if hist else [])
    past.append(before or {**R.DEFAULT, "by": "Built in", "on": None, "reason": "The starting criteria"})
    n = sum(1 for x in past if str(x.get("version", "")).startswith("cgs-")) + 1
    entry = {**{k: new[k] for k in ("weights", "floors", "ready", "bands")},
             "version": f"cgs-{n}" + ("-built-in" if built_in else ""), "by": P.label(c), "on": c.today.isoformat(), "reason": reason}
    row = c.s.get(m.Setting, "scoring")
    if row is None:
        c.s.add(m.Setting(key="scoring", value=entry))
    else:
        row.value = entry
    if hist is None:
        c.s.add(m.Setting(key="scoring_history", value=past))
    else:
        hist.value = past
    c.s.flush()
    R.apply(entry)
    log(c, None, "h", f"{P.label(c)} changed the scoring criteria to {entry['version']}"
                      + (" (back to the built-in ones)" if built_in else "") + f": {reason}")
    return view(c)
