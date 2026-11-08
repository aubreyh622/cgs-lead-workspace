"""Evidence agent, tool-using version (live mode): reads conversation notes and proposes qualification evidence.

It reads the new conversation notes and, when useful, the earlier ones; looks at what is already confirmed and which
gaps the rules engine still shows; may look up the CGS library for the service-fit condition; and records each proposal
with a tool. Every proposal is checked by code the moment it is made (the same checks as before: valid key and value,
quote word for word in the notes, no inference marking a condition met, the library only for fit), and a rejected one
goes back to the agent with the reason. When the notes say someone named only by role must decide, the agent asks the
research agent directly; the request is queued for that agent once the notes are saved.
The rules engine, not the model, calculates Q, R and P. People accept or reject every proposal.
"""
from __future__ import annotations

from sqlalchemy import select

from api import agent_runtime as R
from api import toolkit
from api import live_agents as L
from api import models as m
from backend.rules import CONDITION_QUESTIONS, LABELS, QUESTIONS, calculate

VERSION = "evidence-agent-1.1"
# Every dimension and condition gets one line in the agent's closing account, so a reviewer can tell "read the notes and
# found nothing" from "never looked". "conflict" is confirmed by CGS internally, so the agent does not judge it.
CHECKED = list(L.WEIGHTS) + [k for k in L.CONDITIONS if k != "conflict"]
STATUSES = ("proposed", "already_confirmed", "not_in_notes", "unclear")
DEFAULT_REASONS = {"proposed": "Proposed for review (below).", "already_confirmed": "Already confirmed; nothing new in these notes.",
                   "not_in_notes": "These notes do not mention it.", "unclear": "The notes touch on it but do not settle it."}
REASONS = {"invalid": "The kind, key, value or evidence type is not valid under the rubric.",
           "quote_not_found": "The quote is not in those notes word for word. Copy the sentence exactly as written.",
           "inference_condition": "An inference can never mark a condition as met. Use a client statement or leave it out.",
           "duplicate": "You already proposed this key. One proposal per key.",
           "over_limit": f"The limit of {L.MAX_PROPOSALS} proposals is reached. Call finish.",
           "library_misuse": "Only the fit condition may quote the CGS library, as approved_research, from a passage you looked up."}

SYSTEM = L.EVIDENCE_SYSTEM + """

How to work with your tools (instead of returning one JSON answer):
- Start with notes and assessment: see the new notes, what is already confirmed, and which gaps the rules still show.
  Earlier notes are context; a quote may come from them only when it still applies (give that note's id).
- Record each proposal with propose. It is checked at once; if it is rejected, read the reason and fix it or leave it out.
- For the fit condition, look up the CGS library with search_library first.
- When the notes say that someone named only by role must approve or decide, call ask_research with the role and the sentence.
- When you have gone through all six dimensions and the conditions, call finish with a one-sentence summary and "checked":
  exactly one entry for each of B, A, N, T, H, E, problem, fit, external, next and commercial (not conflict), with a status
  and one factual sentence:
    proposed = you recorded a proposal for it with propose;
    already_confirmed = it is confirmed already (see assessment) and these notes add nothing new;
    not_in_notes = the notes do not mention it (say what is missing, for example "No one says who approves the spend.");
    unclear = the notes touch on it but do not settle it (say what is unsettled).
  A "not_in_notes" or "unclear" line is not a score: never turn it into a proposal to fill a gap. The checklist is checked
  by code; if it is refused, read the reason and call finish again."""


def _confirmed(o: m.Opportunity, key: str) -> bool:
    return (o.scores or {}).get(key) is not None if key in L.WEIGHTS else (o.conditions or {}).get(key) is not None


def _label(key: str) -> str:
    return LABELS[key] if key in LABELS else L.CONDITIONS[key]


def check_checked(raw, o: m.Opportunity, proposed: set[str]) -> list[dict]:
    """The agent's closing checklist must cover every key in CHECKED exactly once and agree with what it proposed.
    Raises ToolError with what to fix; returns [{key, status, reason}]."""
    if not isinstance(raw, list):
        raise R.ToolError("checked must be a list with one entry per dimension and condition.")
    out: dict[str, dict] = {}
    for e in raw:
        e = e if isinstance(e, dict) else {}
        key, status, reason = str(e.get("key", "")).strip(), str(e.get("status", "")).strip(), " ".join(str(e.get("reason", "")).split())
        if key not in CHECKED:
            raise R.ToolError(f"Unknown key {key!r} in checked. Use exactly these: {', '.join(CHECKED)}.")
        if key in out:
            raise R.ToolError(f"{key} appears twice in checked. Give each key once.")
        if status not in STATUSES:
            raise R.ToolError(f"Status for {key} must be one of {', '.join(STATUSES)}.")
        if status == "proposed" and key not in proposed:
            raise R.ToolError(f"You marked {key} proposed, but no proposal for {key} was accepted. Propose it, or mark it not_in_notes or unclear.")
        if status != "proposed" and key in proposed:
            raise R.ToolError(f"You proposed {key}; mark it proposed.")
        if status == "already_confirmed" and not _confirmed(o, key):
            raise R.ToolError(f"{key} is not confirmed yet (see assessment). Mark it not_in_notes or unclear.")
        if not reason or len(reason) > 300:
            raise R.ToolError(f"Give {key} one short factual sentence (at most 300 characters).")
        out[key] = {"key": key, "status": status, "reason": reason}
    missing = [k for k in CHECKED if k not in out]
    if missing:
        raise R.ToolError(f"checked is missing {', '.join(missing)}. Give every dimension and condition once.")
    return [out[k] for k in CHECKED]


def coverage(o: m.Opportunity, saved: set[str], given: list[dict] | None = None) -> list[dict]:
    """The checklist kept with the note and shown to the reviewer. saved = keys with a proposal that reached the review
    list; given = the agent's own checklist (live mode), or None (demo mode, or the agent did not finish): then code fills
    each line from what it can see. Lines left open carry the question to ask next (backend/rules.py)."""
    by = {e["key"]: e for e in (given or [])}
    out = []
    for k in CHECKED:
        e = by.get(k)
        if k in saved:
            status = "proposed"
        elif e and e["status"] == "proposed":  # proposed, but a later check dropped it before review
            status, e = "unclear", {"reason": "A value was proposed, but its quote did not pass the checks."}
        elif e:
            status = e["status"]
        else:
            status = "already_confirmed" if _confirmed(o, k) else "not_in_notes"
        row = {"key": k, "label": _label(k), "status": status, "reason": (e or {}).get("reason") or DEFAULT_REASONS[status]}
        if status in ("not_in_notes", "unclear"):
            row["ask"] = QUESTIONS.get(k) or CONDITION_QUESTIONS[k]
        out.append(row)
    return out


def evidence_agent(c, o: m.Opportunity, note: m.Note, asks: list[dict]) -> tuple[R.AgentSpec, dict]:
    """Returns the agent and its record: {"kept": [...proposals], "dropped": {reason: count}, "checked": [...] or None}.
    asks is filled with research requests."""
    from api.services import role_known
    earlier = [n for n in c.s.scalars(select(m.Note).where(m.Note.opportunity_id == o.id, m.Note.id != note.id).order_by(m.Note.on.desc()))][:5]
    notes_by_id = {note.id: note.text, **{n.id: n.text for n in earlier}}
    shown_library: dict[str, dict] = {}
    record = {"kept": [], "dropped": {}, "checked": None}

    def notes(_args):
        return {"new_note": {"id": note.id, "type": note.type, "on": note.on.isoformat(), "text": note.text},
                "earlier_notes": [{"id": n.id, "type": n.type, "on": n.on.isoformat(), "text": n.text} for n in earlier]}

    def assessment(_args):
        r = calculate({"scores": dict(o.scores), "conditions": dict(o.conditions), "blocker": o.blocker, "revenue": o.revenue})
        pending = c.s.scalars(select(m.Proposal).where(m.Proposal.opportunity_id == o.id, m.Proposal.status == "pending"))
        return {"company": o.company.name, "opportunity": o.title, "team": o.team,
                "confirmed": {"scores": o.scores, "conditions": o.conditions, "blocker": o.blocker},
                "waiting_for_review": [{"kind": p.kind, "key": p.key, "value": p.value} for p in pending],
                "readiness": r["readiness"], "open_gaps": [{"key": g["key"], "label": g["label"], "question": g["question"]} for g in r["gaps"]]}

    lib = toolkit.library_for("Evidence agent", c) if toolkit.allowed("Evidence agent", "library", c.s) else None

    def search_library(args):
        found = lib.search(str(args.get("query", "")), k=3)
        for p in found:
            shown_library[p["id"]] = p
        return {"passages": [{"id": p["id"], "title": p["title"], "type": p["type"], "text": p["text"],
                              **({"synthetic": True} if p["synthetic"] else {})} for p in found]}

    def propose(args):
        note_id = str(args.get("note_id") or note.id)
        if note_id not in notes_by_id:
            raise R.ToolError(f"Unknown note id {note_id!r}. Use {sorted(notes_by_id)}.")
        if len(record["kept"]) >= L.MAX_PROPOSALS:
            raise R.ToolError(REASONS["over_limit"])
        raw = {k: args.get(k) for k in ("kind", "key", "value", "quote", "why", "evidence_type", "source")}
        kept, dropped = L.check_proposals([raw], notes_by_id[note_id], shown_library)
        if kept and any((p["kind"], p["key"]) == (kept[0]["kind"], kept[0]["key"]) for p in record["kept"]):
            kept, dropped = [], {"duplicate": 1}
        if not kept:
            reason = next(iter(dropped), "invalid")
            record["dropped"][reason] = record["dropped"].get(reason, 0) + 1
            raise R.ToolError(REASONS.get(reason, "The proposal did not pass the checks."))
        record["kept"].append({**kept[0], "note_id": note_id})
        return {"accepted": True, "proposals_so_far": len(record["kept"])}

    def ask_research(args):
        from api.services import job_title
        role, quote = job_title(str(args.get("role", ""))), str(args.get("quote", "")).strip()  # "CEO (approves spend)" -> "CEO"
        if len(asks) >= L.MAX_PEOPLE_TO_FIND:
            raise R.ToolError(f"At most {L.MAX_PEOPLE_TO_FIND} people can be looked up from one set of notes.")
        ok = L.check_people_to_find([{"role": role, "quote": quote}], "\n".join(notes_by_id.values()))
        if not ok:
            raise R.ToolError("Give a short role and the sentence from the notes, word for word, that says this person must decide.")
        if any(a["role"].lower() == role.lower() for a in asks):
            return {"queued": True, "note": "Already asked."}
        if role_known(c, o.company_id, role):
            return {"queued": False, "note": f"The workspace already has a named {role} at this company."}
        asks.append(ok[0])
        return {"queued": True, "note": "The research agent will look for them; what it finds goes to the owner's inbox."}

    def finish(args):
        record["checked"] = check_checked(args.get("checked"), o, {p["key"] for p in record["kept"] if p["kind"] in ("score", "cond")})
        return {"summary": str(args.get("summary", "")).strip()[:500]}

    s = {"type": "string"}
    tools = [
        R.Tool("notes", "The new conversation notes and up to five earlier notes for this opportunity.", R.params({}), notes),
        R.Tool("assessment", "What is already confirmed, what waits for review, and the gaps the rules engine still shows.", R.params({}), assessment),
        *([R.Tool("search_library", "Search the CGS library (services, cases, documents) for the service-fit condition.",
                  R.params({"query": s}), search_library)] if lib else []),
        R.Tool("propose", "Record one proposal for a person to review. Checked at once; a rejection says why.",
               R.params({"kind": {"type": "string", "enum": ["score", "cond", "blocker"]},
                         "key": {"type": "string", "enum": list(L.WEIGHTS) + list(L.CONDITIONS) + list(L.REASONS)},
                         "value": {"type": "string", "description": "score: 0-5 (H may be 2.5); cond: true or false; blocker: the same code as key"},
                         "quote": {"type": "string", "description": "Copied word for word from the notes (or the library passage for fit)"},
                         "why": s, "evidence_type": {"type": "string", "enum": list(L.EVIDENCE_TYPES)},
                         "source": {"type": "string", "description": "notes, or the id of a CGS library passage (fit only)"},
                         "note_id": {"type": "string", "description": "Which note the quote is from. Empty = the new note."}},
                        ["kind", "key", "value", "quote", "why", "evidence_type", "source"]), propose),
        R.Tool("ask_research", "Ask the research agent to find a person the notes name only by role, who must approve or decide.",
               R.params({"role": {"type": "string", "description": "The job title only, for example CEO or CFO. Why they matter goes in the quote."},
                         "quote": {"type": "string", "description": "The sentence from the notes, word for word"}}), ask_research),
    ]
    from api.lessons import block
    spec = R.AgentSpec(name="Evidence agent", version=VERSION, system=SYSTEM + block(c.s, "Evidence agent", c.today), tools=tools,
                       finish=R.Tool("finish", "You are done. One-sentence summary of what you proposed and what is still unknown, "
                                               "and one checked line for every dimension and condition.",
                                     R.params({"summary": s, "checked": {"type": "array", "items": R.params({
                                         "key": {"type": "string", "enum": CHECKED},
                                         "status": {"type": "string", "enum": list(STATUSES)},
                                         "reason": {"type": "string", "description": "One factual sentence: what the notes say, or what is missing"}})}}),
                                     finish),
                       max_steps=12, max_tool_calls=L.MAX_PROPOSALS + 10)
    return spec, record


def task(o: m.Opportunity) -> str:
    return (f"New conversation notes were added for {o.company.name}: {o.title}. Read them, check what is already confirmed, "
            "and propose the qualification evidence the notes clearly support.")
