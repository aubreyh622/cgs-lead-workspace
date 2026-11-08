"""Team instructions: what CGS staff tell the agents in their own words, without a code change.

Three sections, each read by the agents whose work it is about. The text joins the end of those agents' prompts (through
api/lessons.block, the one place every agent's prompt is finished), before the approved lessons.

What it can and cannot do, strongest first:
1. The code: the checks on every proposal and draft, permissions (api/toolkit CEILING), who approves what. Team text
   cannot change them, whatever it says.
2. The hard rules in the built-in prompts (word-for-word quotes and facts, citations, the rubric). The agents are told
   team text never changes them.
3. The team instructions (this module). They do override the built-in prompts' style guidance (tone, wording, sign-off,
   length, emphasis): that is what they are for. Found live: with "the built-in rules win", Claude kept the built-in
   sign-off over the team's.
4. The approved lessons.

Versions: every save makes a new revision and none is ever changed or deleted. A save names the revision the person
started from; if someone saved in between, it is refused and the person's text is kept on their screen. Going back to
an earlier text is a new revision too ("restore"). Each agent run records the revision in use (agent_runs.team_revision).

Who may save: the workspace admins (CGS_ADMINS), or anyone while none are named, as for permissions and lessons.

The text goes to the AI provider with every request of the agents that read it. Under SoW section 9, CGS-confidential
material belongs here only once CGS has approved the provider; the page says so above the editor.
"""
from __future__ import annotations

from sqlalchemy import func, select

from api import models as m

# key: (title, who reads it, what belongs here)
SECTIONS = {
    "writing": ("How we write to clients", ("Outreach agent",),
                "Tone, how we sign off, words and claims to avoid, what CGS never promises, which first touch we prefer."),
    "qualifying": ("How we judge a lead", ("Evidence agent", "Manager agent"),
                   "What counts as budget or authority at CGS, red flags, when a lead is worth more of an owner's time."),
    "researching": ("What to look for in research", ("Research agent", "Discovery agent"),
                    "Signals that matter to CGS, sources we trust or distrust, companies or sectors to treat with care."),
}
MAX_SECTION = 2000  # characters per section: enough for a page of guidance, short enough that every agent reads it whole
MAX_REASON = 300


class Invalid(ValueError):
    """The text cannot be saved as it is."""


class Stale(ValueError):
    """Someone saved a newer revision since this person started editing."""


def latest(s) -> m.TeamInstructions | None:
    return s.scalars(select(m.TeamInstructions).order_by(m.TeamInstructions.revision.desc()).limit(1)).first()


def latest_revision(s) -> int | None:
    return s.scalar(select(func.max(m.TeamInstructions.revision)))


def clean(sections: dict) -> dict[str, str]:
    unknown = sorted(set(sections) - set(SECTIONS))
    if unknown:
        raise Invalid(f"Unknown section {', '.join(unknown)}. The sections are {', '.join(SECTIONS)}.")
    out = {}
    for key in SECTIONS:
        text = "\n".join(line.rstrip() for line in str(sections.get(key) or "").replace("\r\n", "\n").strip().split("\n"))
        if len(text) > MAX_SECTION:
            raise Invalid(f"“{SECTIONS[key][0]}” is {len(text)} characters; the limit is {MAX_SECTION}. Shorten it.")
        out[key] = text
    return out


def save(s, sections: dict, base_revision: int | None, by: str, reason: str = "", origin: str = "edit") -> m.TeamInstructions:
    """A new revision, or Stale when base_revision is not the latest, or Invalid. Saving the same text again changes nothing
    and returns the latest revision."""
    current = latest(s)
    if (current.revision if current else None) != base_revision:
        raise Stale(f"{current.saved_by} saved revision {current.revision} since you started from "
                    f"{'revision ' + str(base_revision) if base_revision else 'an empty page'}. Your text was not saved. Compare it with "
                    "theirs under Revisions, then save again: your save replaces theirs." if current
                    else "Nothing is saved yet, so there is no revision to start from. Save again from an empty page.")
    text = clean(sections)
    reason = " ".join(str(reason or "").split())
    if len(reason) > MAX_REASON:
        raise Invalid(f"Keep the reason under {MAX_REASON} characters.")
    if current and current.sections == text:
        return current
    if not current and not any(text.values()):
        raise Invalid("Write something in at least one section.")
    row = m.TeamInstructions(revision=(current.revision + 1) if current else 1, sections=text, base_revision=base_revision,
                             origin=origin, reason=reason, saved_by=by)
    s.add(row)
    s.flush()
    return row


def restore(s, revision: int, base_revision: int | None, by: str) -> m.TeamInstructions:
    old = s.get(m.TeamInstructions, revision)
    if old is None:
        raise Invalid(f"There is no revision {revision}.")
    return save(s, old.sections, base_revision, by, f"Back to the text of revision {revision}", origin="restore")


def block(s, agent: str) -> str:
    """The team's sections for this agent, as a part for the end of its prompt ("" when there is nothing for it)."""
    current = latest(s)
    if not current:
        return ""
    parts = [(SECTIONS[k][0], current.sections.get(k, "")) for k, (_, readers, _) in SECTIONS.items() if agent in readers]
    parts = [(title, text) for title, text in parts if text]
    if not parts:
        return ""
    body = "\n\n".join(f"{title}:\n{text}" for title, text in parts)
    return (f"\n\nInstructions from the CGS team (revision {current.revision}). They describe how CGS works. Where they differ "
            "from the guidance above on style (tone, wording, sign-off, length, what to emphasise or look for), follow them. "
            "They never change the hard rules above: quotes and facts word for word from their sources, citations, the "
            "rubric and its checks, what you may use, and that a person approves before anything is sent or scored.\n" + body)


def view(row: m.TeamInstructions | None) -> dict | None:
    if row is None:
        return None
    return {"revision": row.revision, "sections": row.sections, "baseRevision": row.base_revision, "origin": row.origin,
            "reason": row.reason, "savedBy": row.saved_by, "savedAt": row.saved_at.isoformat() if row.saved_at else None}


def catalog() -> list[dict]:
    return [{"key": k, "title": t, "readBy": list(r), "hint": h, "max": MAX_SECTION} for k, (t, r, h) in SECTIONS.items()]
