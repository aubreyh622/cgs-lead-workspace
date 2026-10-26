"""Demo agents: deterministic text rules and prepared fixtures, ported from the prototype.

They stand in for the real AI agents until backend steps 5-6 connect live providers. They run only
in APP_MODE=demo. In live mode every call raises AgentUnavailable, so the caller records a failed
run and shows the error. Live mode never falls back to these results.
"""
from __future__ import annotations

from collections import Counter
import copy
from datetime import date, timedelta
import json
import re
import time

from api.seed import SEED

_DATA = json.loads(SEED.read_text(encoding="utf-8"))
STAR = _DATA["star"]
DEMO_MODEL = "demo-text-rules"


class AgentUnavailable(Exception):
    """Raised when an agent is asked to run but no provider is connected."""


def require_demo(app_mode: str, agent: str) -> None:
    if app_mode != "demo":
        raise AgentUnavailable(f"{agent}: the live AI provider is not connected yet. Nothing was generated, and no demo result was substituted.")


# ---------- Steps 2-3: research ----------
def light_brief(facts: list[list[str]], hypothesis: str, flags=None, rec=None, priority=None, suggest_owner="", due: date | None = None,
                relevance=None, alternative=None) -> dict:
    p = {"tier": 3, "tierWhy": "Mid-size manufacturer; scope not yet known", "authority": "Director", "authorityWhy": "Influences budget",
         "relationship": "None", "relationshipWhy": "No known connection", "fit": "Moderate", "fitWhy": "Matches 2 of 4 signals", "confidence": "Medium"}
    p.update(priority or {})
    return {"facts": [{"id": f"f{i + 1}", "text": t[0], "src": t[1], "date": t[2]} for i, t in enumerate(facts)],
            "hypothesis": hypothesis, "relevance": relevance or "Relevant to CGS strategy and transformation planning.",
            "alternative": alternative or "An internal team may already own this work.", "unknowns": ["Budget owner", "Timing"],
            "flags": flags or [], "priority": p, "recommendation": rec or {"decision": "accept", "reason": "Signals match the research direction."},
            "suggested": {"owner": suggest_owner, "next": "Send tailored outreach", "due": (due or date.today()).isoformat()},
            "usage": {"searches": 5, "pages": 9, "calls": 4}}


def candidate_brief(c, owner: str, today: date) -> dict:
    """Brief for a picked Step 1 candidate."""
    if c.demo_star:
        r = copy.deepcopy(STAR["research"])
        return {"facts": r["facts"], "hypothesis": r["hypothesis"], "relevance": r["relevance"], "alternative": r["alternative"],
                "unknowns": r["unknowns"], "flags": r["flags"], "priority": r["priority"], "recommendation": r["rec"],
                "suggested": r["suggest"], "usage": r["usage"]}
    flags = []
    for f in c.flags or []:
        if f == "Contact to confirm":
            flags.append({"type": "No contact found", "text": f"No verifiable {c.title} found. Suggested route: the company leadership page or an introduction."})
        elif f == "Possible duplicate":
            flags.append({"type": "Possible duplicate", "text": f"{c.person} may already be a CGS contact. Check before accepting."})
        else:
            flags.append({"type": "Stale signal", "text": f"The main signal is from {c.signal_date}, older than the 6-month window."})
    if any(f["type"] == "Possible duplicate" for f in flags):
        rec = {"decision": "reject", "reason": "Likely an existing relationship. Confirm with the owner before any outreach."}
    elif flags:
        rec = {"decision": "return", "reason": "Resolve the flag before accepting."}
    else:
        rec = {"decision": "accept", "reason": f"{c.signal} matches the research direction and the contact owns the area."}
    return light_brief([[c.why, "Public source", c.signal_date]], f"{c.signal} may create planning work CGS can support.",
                       flags, rec, suggest_owner=owner, due=today + timedelta(days=4))


def manual_brief(company: str, note: str, source: str, has_name: bool, owner: str, today: date) -> dict:
    flags = [] if has_name else [{"type": "No contact found", "text": "No name was given. The agent suggests a role to target."}]
    rec = {"decision": "accept", "reason": "Added by a consultant with context."} if has_name else {"decision": "return", "reason": "Find a named contact before accepting."}
    fact = f"Consultant note: {note}" if note else f"{company} was added from a {source.lower()}."
    return light_brief([[fact, source, f"{today.day} {today:%b %Y}"]],
                       "To be confirmed by the research agent.", flags, rec, suggest_owner=owner, due=today + timedelta(days=4))


def star_sibling_brief(today: date) -> dict:
    return light_brief(
        [["Corvane completed its acquisition of Arden Controls on 18 September 2026, adding four plants.", "Corvane press release", "18 Sep 2026"],
         ["Corvane is hiring an integration program manager for the four Arden plants, reporting to the COO.", "Job posting", "15 Oct 2026"]],
        "Bringing Arden’s plants onto Corvane’s processes may need an operations integration plan, separate from the AI program.",
        [{"type": "No contact found", "text": "No verifiable COO found in public sources. Jordan Lee (Fellow) knows the COO from 2023 work."}],
        {"decision": "return", "reason": "A different team and problem from the AI program at the same company. Confirm the COO before accepting."},
        {"tier": 3, "tierWhy": "Operations integration for four plants; scope not yet known", "authority": "C-level (to confirm)",
         "authorityWhy": "The role would report to the COO", "relationship": "Second degree", "relationshipWhy": "Jordan Lee (Fellow) worked with the COO in 2023"},
        relevance="Matches CGS post-merger operating-model work.", alternative="The integration program manager may own this without outside help.", due=today)


SECOND_PASS_NAMES = {"ostrander": ("Rachel Duval", "Head of Transformation (since Sep 2026)"), "corvane-ops": ("Owen Price", None)}


def second_pass(opp_id: str, question: str, today: date) -> dict:
    """What the research agent adds when a Step 4 reviewer returns an opportunity with a question."""
    name, title = SECOND_PASS_NAMES.get(opp_id, ("Jordan Ellis", None))
    return {"fact": {"text": f"Follow-up research: {question}", "src": "Research agent, second pass", "date": today.isoformat()},
            "contact_name": name, "contact_title": title,
            "recommendation": {"decision": "accept", "reason": "The second pass answered the reviewer’s question."}}


# ---------- Step 6: reply labels and evidence ----------
def label_reply(text: str) -> str:
    if re.search(r"not (interested|considering)|no thanks|decline|not a priority", text, re.I):
        return "decline"
    if re.search(r"later|next year|not now|revisit|after (the|our)", text, re.I):
        return "defer"
    if re.search(r"talk|call|meet|available|schedule", text, re.I):
        return "meet"
    if re.search(r"problem|challenge|struggl|need", text, re.I):
        return "need"
    return "courtesy"


_QUOTE_NORMAL = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t.translate(_QUOTE_NORMAL)).strip()


def quote_in_text(quote: str, text: str) -> bool:
    """A client quote is valid only if every sentence of it appears word for word in the source text."""
    src = _norm(text)
    parts = [p for p in re.split(r"(?<=[.!?])\s+", _norm(quote)) if p]
    return bool(parts) and all(p.rstrip(".") in src for p in parts)


def extract_evidence(text: str, star: bool) -> list[dict]:
    """Proposals from a note: score, condition or blocker, each with a quote and an evidence type."""
    if star and re.search(r"Arden|partner", text, re.I):
        return [{k: p[k] for k in ("kind", "key", "value", "quote", "why", "type")} for p in copy.deepcopy(STAR["proposals"])]
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", text) if len(x) > 20]
    found = []

    def pick(pattern, kind, key, value, why):
        q = next((x for x in sentences if re.search(pattern, x, re.I)), None)
        if q:
            found.append({"kind": kind, "key": key, "value": value, "quote": q.strip(), "why": why, "type": "client_statement"})

    pick(r"problem|struggl|challenge|manual|delay|no plan", "cond", "problem", True, "The client describes a problem. Check it is specific.")
    pick(r"problem|struggl|challenge|manual|delay|no plan", "score", "N", 2, "A problem is named; impact and fit still need validation.")
    pick(r"outside|partner|external|consult|help", "cond", "external", True, "The client mentions outside help.")
    pick(r"budget|fund", "score", "B", 2, "A possible funding source is mentioned. The approval route is unknown.")
    pick(r"next step|workshop|meet again|follow[- ]up on", "cond", "next", True, "A next action is mentioned. Check it has an owner and date.")
    pick(r"not (considering|interested)|no outside|internally only|declin", "blocker", "NO_EXTERNAL_SUPPORT", "NO_EXTERNAL_SUPPORT", "Possible blocker. Only a person can confirm it.")
    return found


def star_need() -> dict:
    return copy.deepcopy(STAR["problem"])


# ---------- Step 7: next actions ----------
ACTION_TITLES = {"B": "Confirm the funding path", "A": "Bring the decision-maker into the conversation", "N": "Validate the problem and its business impact",
                 "T": "Confirm the timing and the decision date", "H": "Check the relationship history", "E": "Agree a useful next exchange",
                 "revenue": "Draft an initial CGS fee estimate", "condition:problem": "Confirm the specific problem",
                 "condition:fit": "Check CGS service fit internally", "condition:external": "Ask about openness to outside support",
                 "condition:next": "Agree a next step with a date", "condition:commercial": "Confirm funding and decision access",
                 "condition:conflict": "Resolve conflicting statements"}


def next_actions(gaps: list[dict], existing_titles: set[str]) -> list[dict]:
    out = []
    for g in gaps:
        title = ACTION_TITLES.get(g["key"], f"Close the gap: {g['label']}")
        if title not in existing_titles:
            out.append({"title": title, "reason": g["label"], "question": g["question"], "gap_key": g["key"]})
        if len(out) == 3:
            break
    return out


# ---------- learning loop ----------
def analyze_feedback(records: list, buyer_roles: list[str]) -> dict:
    """Propose a direction change only when at least 3 eligible rejections share a reason."""
    reasons = Counter(f.reason for f in records if f.outcome != "Accepted")
    top = reasons.most_common(1)
    if top and top[0][1] >= 3 and "authority" in top[0][0].lower():
        return {"summary": f"{top[0][1]} records share “{top[0][0]}”.",
                "proposal": {"field": "buyer_roles", "title": "Drop Manager-level roles from the buyer list",
                             "rationale": f"{top[0][1]} rejections cite “{top[0][0]}”.",
                             "after": [r for r in buyer_roles if "manager" not in r.lower()]}}
    return {"summary": f"No reason repeats in at least 3 of {len(records)} records. No change proposed.", "proposal": None}


def run_id() -> str:
    return "run-" + format(time.time_ns(), "x")[-10:]
