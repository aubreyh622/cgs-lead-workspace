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
from backend.rules import calculate

VERSION = "evidence-agent-1.0"
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
- When you have gone through all six dimensions and six conditions, call finish with a one-sentence summary."""


def evidence_agent(c, o: m.Opportunity, note: m.Note, asks: list[dict]) -> tuple[R.AgentSpec, dict]:
    """Returns the agent and its record: {"kept": [...proposals], "dropped": {reason: count}}. asks is filled with research requests."""
    from api.services import role_known
    earlier = [n for n in c.s.scalars(select(m.Note).where(m.Note.opportunity_id == o.id, m.Note.id != note.id).order_by(m.Note.on.desc()))][:5]
    notes_by_id = {note.id: note.text, **{n.id: n.text for n in earlier}}
    shown_library: dict[str, dict] = {}
    record = {"kept": [], "dropped": {}}

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
                       finish=R.Tool("finish", "You are done. One-sentence summary of what you proposed and what is still unknown.",
                                     R.params({"summary": s}), finish),
                       max_steps=10, max_tool_calls=L.MAX_PROPOSALS + 10)
    return spec, record


def task(o: m.Opportunity) -> str:
    return (f"New conversation notes were added for {o.company.name}: {o.title}. Read them, check what is already confirmed, "
            "and propose the qualification evidence the notes clearly support.")
