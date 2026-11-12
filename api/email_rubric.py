"""The CGS email standard (config/email_rubric.json): the criteria a first outreach email is judged on, their weights, the
bar a draft must reach, and how many times the outreach agent's reviewer may send a draft back.

One rubric, three readers: the outreach agent is told what the standard asks; its reviewer scores each draft against it
before a person sees it (api/outreach_agent.py); and the quality check's reading model scores drafts with it
(api/check_quality.py, tests/eval). Changing the standard changes all three at once.

An admin may change the criteria (add, remove, reword, reweight), the bar and the review rounds (client meeting 4: admins
change the scoring). Each change is a new version (email-1, email-2, ...) stored in Setting "email_rubric", earlier ones in
"email_rubric_history"; "back to the built-in standard" is a change too. Every review and every quality-check score records
the version it used, so numbers from different versions are never compared without saying so.
The calibration examples stay in the file: they are checked by `python -m api.check_quality calibration`.
"""
from __future__ import annotations

import copy
import json
import re

from api import models as m
from api import people as P
from api.settings import ROOT

FILE = ROOT / "config" / "email_rubric.json"
MAX_CRITERIA = 8
_KEY = re.compile(r"^[a-z][a-z0-9_]{1,23}$")


def built_in() -> dict:
    return json.loads(FILE.read_text(encoding="utf-8"))


def stored(s) -> dict | None:
    row = s.get(m.Setting, "email_rubric") if s is not None else None
    return copy.deepcopy(row.value) if row and row.value else None


def current(s=None) -> dict:
    """The standard in use: the stored one, else the built-in one. Always has scale, criteria, bar, review, length, version."""
    base = built_in()
    mine = stored(s)
    if mine:
        base.update({k: mine[k] for k in ("criteria", "bar", "review", "version") if k in mine})
        base["changed"] = {k: mine.get(k) for k in ("by", "on", "reason")}
    return base


# ---------- what the readers get ----------
def as_text(r: dict) -> str:
    """The standard as instructions for a model that writes, reviews or scores an email."""
    lines = [f"CGS email standard ({r['version']}). Score each line from 1 to 5:"]
    lines += [f"  {k} = {v}" for k, v in r["scale"].items()]
    lines.append("The lines (weight in brackets):")
    for c in r["criteria"]:
        lines.append(f"- {c['key']} ({c['label']}, {c['weight']}%): {c['question']}")
        if c.get("good"):
            lines.append(f"    Looks like a 5: {c['good']}")
        if c.get("poor"):
            lines.append(f"    Looks like a 2: {c['poor']}")
    lo, hi = r["length"]["standard"]
    lines.append(f"Length: tone 'standard' is about {lo}-{hi} words; tone 'brief' is under {r['length']['brief'][1]} words.")
    lines.append(f"A draft meets the standard when the weighted mean is at least {r['bar']['mean']:g} and no line is below {r['bar']['each']}.")
    return "\n".join(lines)


def schema(r: dict, judge: bool = False) -> dict:
    """The JSON answer a reviewer (judge=False) or the quality check's reading model (judge=True) gives."""
    five = {"type": "integer", "enum": [1, 2, 3, 4, 5]}
    props = {c["key"]: five for c in r["criteria"]}
    props["unsupported_claim"] = {"type": "boolean"}
    if judge:
        props["would_send"] = {"type": "string", "enum": ["yes", "with edits", "no"]}
        props["improve"] = {"type": "string"}
    else:
        props["fixes"] = {"type": "array", "items": {"type": "string"}}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def valid_scores(data: dict, r: dict) -> bool:
    return all(data.get(c["key"]) in (1, 2, 3, 4, 5) for c in r["criteria"])


def weighted(scores: dict, r: dict) -> float:
    total = sum(c["weight"] for c in r["criteria"]) or 1
    return round(sum(scores[c["key"]] * c["weight"] for c in r["criteria"]) / total, 2)


def meets(scores: dict, r: dict) -> bool:
    return weighted(scores, r) >= r["bar"]["mean"] and all(scores[c["key"]] >= r["bar"]["each"] for c in r["criteria"])


def summary(scores: dict, r: dict) -> str:
    return ", ".join(f"{c['label']} {scores[c['key']]}" for c in r["criteria"]) + f" (weighted {weighted(scores, r):g})"


# ---------- admin changes ----------
def view(c) -> dict:
    r = current(c.s)
    row = c.s.get(m.Setting, "email_rubric_history")
    b = built_in()
    return {"current": {k: r[k] for k in ("version", "scale", "criteria", "bar", "review", "length")},
            "builtIn": {k: b[k] for k in ("version", "criteria", "bar", "review")}, "changed": r.get("changed"),
            "history": list(reversed(list(row.value if row else [])))[:20],
            "canChange": P.is_admin(c.s, c.settings, c.signed_in or c.actor), "admins": P.admins(c.s, c.settings)}


def _text(value, low: int, high: int, what: str, required: bool = True) -> str:
    v = " ".join(str(value or "").split())
    if (required or v) and not low <= len(v) <= high:
        raise P.RoleError(f"{what} must be {low} to {high} characters.", 400, "invalid")
    return v


def check(criteria, bar, review) -> dict:
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= MAX_CRITERIA:
        raise P.RoleError(f"Give 1 to {MAX_CRITERIA} criteria.", 400, "invalid")
    out, keys = [], set()
    for i, c in enumerate(criteria, 1):
        c = c if isinstance(c, dict) else {}
        label = _text(c.get("label"), 2, 40, f"The name of criterion {i}")
        key = str(c.get("key") or "").strip().lower() or re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:24]
        if not _KEY.match(key) or key in keys or key in ("unsupported_claim", "would_send", "improve", "fixes"):
            raise P.RoleError(f"Criterion {i} needs a short unique key (letters, digits, _): {key!r} cannot be used.", 400, "invalid")
        keys.add(key)
        w = c.get("weight")
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w != int(w) or not 0 <= w <= 100:
            raise P.RoleError(f"The weight of {label} must be a whole number from 0 to 100.", 400, "invalid")
        out.append({"key": key, "label": label, "weight": int(w), "question": _text(c.get("question"), 20, 400, f"The question for {label}"),
                    "good": _text(c.get("good"), 0, 300, f"'Looks like a 5' for {label}", False),
                    "poor": _text(c.get("poor"), 0, 300, f"'Looks like a 2' for {label}", False)})
    if sum(x["weight"] for x in out) != 100:
        raise P.RoleError(f"The weights must add up to 100; these add up to {sum(x['weight'] for x in out)}.", 400, "invalid")
    bar = bar if isinstance(bar, dict) else {}
    mean, each = bar.get("mean"), bar.get("each")
    if isinstance(mean, bool) or not isinstance(mean, (int, float)) or not 1 <= mean <= 5:
        raise P.RoleError("The bar's weighted mean must be a number from 1 to 5.", 400, "invalid")
    if isinstance(each, bool) or not isinstance(each, int) or not 1 <= each <= 5:
        raise P.RoleError("The lowest score each line needs must be a whole number from 1 to 5.", 400, "invalid")
    rounds = (review if isinstance(review, dict) else {}).get("rounds")
    if isinstance(rounds, bool) or not isinstance(rounds, int) or not 0 <= rounds <= 2:
        raise P.RoleError("Review rounds must be 0, 1 or 2.", 400, "invalid")
    return {"criteria": out, "bar": {"mean": round(float(mean), 2), "each": each}, "review": {"rounds": rounds}}


def change(c, criteria=None, bar=None, review=None, reason: str = "", built: bool = False) -> dict:
    """An admin saves a new standard (or goes back to the built-in one). Drafts are reviewed and scored with it from now on."""
    from api.services import log
    P.require_admin(c, "change the email standard")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise P.RoleError("Say why, in a few words: the team sees it beside the standard.", 400, "no_reason")
    b = built_in()
    new = {k: b[k] for k in ("criteria", "bar", "review")} if built else check(criteria, bar, review)
    if built:
        new["bar"] = {"mean": b["bar"]["mean"], "each": b["bar"]["each"]}
        new["review"] = {"rounds": b["review"]["rounds"]}
    before = current(c.s)
    if all(_same(before[k], new[k]) for k in ("criteria", "bar", "review")):
        raise P.RoleError("Nothing changed: this is the standard in use.", 409, "unchanged")
    hist = c.s.get(m.Setting, "email_rubric_history")
    past = list(hist.value if hist else [])
    past.append({k: before[k] for k in ("criteria", "bar", "review", "version")}
                | (before.get("changed") or {"by": "Built in", "on": None, "reason": "The starting standard"}))
    n = sum(1 for x in past if str(x.get("version", "")).startswith("email-")) + 1
    entry = {**new, "version": f"email-{n}" + ("-built-in" if built else ""), "by": P.label(c), "on": c.today.isoformat(), "reason": reason}
    row = c.s.get(m.Setting, "email_rubric")
    if row is None:
        c.s.add(m.Setting(key="email_rubric", value=entry))
    else:
        row.value = entry
    if hist is None:
        c.s.add(m.Setting(key="email_rubric_history", value=past))
    else:
        hist.value = past
    c.s.flush()
    log(c, None, "h", f"{P.label(c)} changed the email standard to {entry['version']}"
                      + (" (back to the built-in one)" if built else "") + f": {reason}")
    return view(c)


def _same(a, b) -> bool:
    def norm(x):
        if isinstance(x, list):
            return [norm(i) for i in x]
        if isinstance(x, dict):
            return {k: norm(v) for k, v in x.items() if k not in ("explain",)}
        return x
    return norm(a) == norm(b)
