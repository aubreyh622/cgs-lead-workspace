"""Outreach agent, tool-using version (live mode): first emails, follow-ups and reconnect messages.

It reads the brief and what was already sent or said, may look for news newer than the brief, may pick one relevant CGS
service or case, and then submits a draft. Code checks the draft before anyone sees it: every fact it relies on is cited,
each cited phrase stays close to its source, no prices, percentages, guarantees or promises, and the length fits the tone.
A new development can be cited only after the agent has read the page and added the sentence word for word as a fact.
A rejected draft goes back to the agent with the reason. Nothing is ever sent: a person edits, sends and records it.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from api import agent_runtime as R
from api import knowledge
from api import live_agents as L
from api import models as m
from api import toolkit
from api.agent_tools import Pages
from api.demo_agents import quote_in_text
from api.llm import LLMError, Usage

VERSION = "outreach-agent-1.1"  # 1.1: brief lists other CGS work with the same company or person
AGENT = "Outreach agent"
MAX_NEW_FACTS = 3
SUPPORT = 0.75  # share of a cited phrase's words that must appear in its source (and every number must)

SYSTEM = L.OUTREACH_SYSTEM + """

How to work with your tools:
- Start with brief (who writes to whom, the purpose, the facts you may cite) and history (what was already sent or said; never repeat it).
- You may look for something newer than the brief with company_pages or search_web. To cite a new development, read the page
  with read_page and add the sentence with add_fact (word for word). It gets a fact id you can cite.
- You may look up one relevant CGS service or case with search_library and cite it by its passage id.
- If you have mail_thread, read it for a follow-up: answer what the client said and do not repeat what CGS already wrote.
- brief may list other_cgs_opportunities: other CGS work with the same company or the same person. Do not repeat what a
  colleague already wrote to this person and do not write as if CGS were new to them; never mention the other work by
  name unless the brief's facts do.
- Keep each cited phrase close to the source's own words.
- Submit with submit_draft. It is checked; if it is rejected, fix what the message says and submit again."""


def _fact_ids(facts) -> set[str]:
    return {f["id"] for f in facts}


def _next_id(facts) -> str:
    nums = [int(f["id"][1:]) for f in facts if re.fullmatch(r"f\d+", str(f.get("id", "")))]
    return f"f{max(nums, default=0) + 1}"


def unsupported_citation(body: str, sources: dict[str, str]) -> str | None:
    """The first cited phrase whose words are mostly not in its source, or None."""
    for sid, phrase in L._CITE.findall(body):
        words = set(knowledge.tokens(phrase))
        source = set(knowledge.tokens(sources.get(sid, "")))
        numbers = set(re.findall(r"\d+", phrase)) - set(re.findall(r"\d+", sources.get(sid, "")))
        if numbers or (words and len(words & source) / len(words) < SUPPORT):
            return f"[[{sid}|{phrase}]]"
    return None


def outreach_agent(c, o: m.Opportunity, *, purpose: str, angle: str | None, tone: str, sender: str,
                   extra_facts: list[dict] | None = None) -> tuple[R.AgentSpec, dict]:
    """Returns the agent and its record: {"new_facts": [...]} (facts it added from pages it read)."""
    b = o.brief
    facts = list(b.facts or [])
    facts += [f for f in (extra_facts or []) if f["id"] not in _fact_ids(facts)]
    pages = Pages()
    shown_library: dict[str, dict] = {}
    record = {"new_facts": []}
    contact = {"name": o.contact.name if o.contact else None, "title": o.contact.title if o.contact else "", "company": o.company.name}

    def brief(_args):
        from api import related as REL
        others = REL.for_agent(c.s, o, c.today)
        return {"purpose": purpose, "angle": angle, "tone": tone, "sender": sender, "contact": contact,
                "facts": [{"id": f["id"], "text": f["text"], "date": f.get("date"), "source": f.get("src")} for f in facts],
                "hypothesis": b.hypothesis, "cgs_relevance": b.relevance, **({"other_cgs_opportunities": others} if others else {})}

    def history(_args):
        r = c.s.get(m.Outreach, o.id)
        notes = list(c.s.scalars(select(m.Note).where(m.Note.opportunity_id == o.id).order_by(m.Note.on.desc()).limit(3)))[::-1]
        return {"stage": o.stage, "reply_label": o.reply_label,
                "sent": None if not r or not r.sent_on else {"on": r.sent_on.isoformat(), "via": r.sent_via, "subject": r.subject,
                                                             "body": (r.body or "")[:800]},
                "reply": None if not r or not r.reply_text else {"on": r.reply_on.isoformat() if r.reply_on else None, "text": r.reply_text[:800]},
                "recent_notes": [{"on": n.on.isoformat(), "text": n.text[:600]} for n in notes]}

    def add_fact(args):
        url, quote = str(args.get("url", "")).strip(), str(args.get("quote", "")).strip()
        if len(record["new_facts"]) >= MAX_NEW_FACTS:
            raise R.ToolError(f"At most {MAX_NEW_FACTS} new facts per draft.")
        if url not in pages.read:
            raise R.ToolError("Read the page with read_page before you add a fact from it.")
        if len(quote) < 20 or not quote_in_text(quote, pages.text(url)):
            raise R.ToolError("The quote must be a sentence from that page, copied word for word.")
        page = pages.found[url]
        fact = {"id": _next_id(facts), "text": quote, "quote": quote, "src": url, "date": page.get("published") or "undated"}
        facts.append(fact)
        record["new_facts"].append(fact)
        return {"id": fact["id"], "note": f"Cite it as [[{fact['id']}|a phrase from the sentence]]."}

    lib = toolkit.library_for(AGENT, c) if toolkit.allowed(AGENT, "library", c.s) else None

    def search_library(args):
        found = lib.search(str(args.get("query", "")), k=3, types=("case", "service", "document"))
        for p in found:
            shown_library[p["id"]] = p
        return {"passages": [{"id": p["id"], "title": p["title"], "type": p["type"], "text": p["text"],
                              **({"synthetic": True} if p["synthetic"] else {})} for p in found]}

    def submit_draft(args):
        subject, body = str(args.get("subject", "")).strip(), str(args.get("body", "")).strip()
        sources = {f["id"]: f["text"] + " " + str(f.get("quote", "")) for f in facts} | {k: p["text"] for k, p in shown_library.items()}
        try:
            L.check_draft(subject, body, set(sources), " ".join(sources.values()))
        except LLMError as e:
            raise R.ToolError(f"{e} Fix it and submit again.") from None
        weak = unsupported_citation(body, sources)
        if weak:
            raise R.ToolError(f"The cited phrase {weak} does not match its source. Use words close to the source, or cite another one.")
        return {"subject": subject, "body": body}

    s = {"type": "string"}
    tools = [
        R.Tool("brief", "The purpose, tone, sender, contact, the facts you may cite (with ids), the hypothesis and CGS relevance.",
               R.params({}), brief),
        R.Tool("history", "What was already sent, the client's reply and recent conversation notes.", R.params({}), history),
        *toolkit.grant(AGENT, toolkit.Run(c, company=o.company.name, domain=o.company.domain, opp=o, company_id=o.company_id, pages=pages),
                       ["company_pages", "search_web", "read_page", "mail_thread"]),
        *([R.Tool("add_fact", "Add one sentence from a page you read as a new citable fact (word for word).",
                  R.params({"url": s, "quote": s}), add_fact)] if toolkit.allowed(AGENT, "web", c.s) else []),
        *([R.Tool("search_library", "Search the CGS library for a relevant service or case you may cite by passage id.",
                  R.params({"query": s}), search_library)] if lib else []),
    ]
    from api.lessons import block
    spec = R.AgentSpec(name="Outreach agent", version=VERSION, system=SYSTEM + block(c.s, "Outreach agent", c.today), tools=tools,
                       finish=R.Tool("submit_draft", "Submit the draft (plain text, citation markers included). It is checked.",
                                     R.params({"subject": s, "body": s}), submit_draft),
                       max_steps=8, max_tool_calls=12)
    return spec, record


def write(c, o: m.Opportunity, provider, *, purpose: str, angle: str | None, tone: str, sender: str,
          extra_facts: list[dict] | None = None, may_call=None) -> tuple[dict, Usage, list[dict]]:
    """Run the agent. Returns ({subject, body, new_facts}, usage, trace). Raises LLMError when no draft passes the checks."""
    spec, record = outreach_agent(c, o, purpose=purpose, angle=angle, tone=tone, sender=sender, extra_facts=extra_facts)
    task = f"Write the {purpose} for {o.company.name}. Start with brief and history, then submit_draft."
    out = R.run(spec, provider, task, **({"may_call": may_call} if may_call else {}))
    if out.status != "done":
        last = next((t.get("error") for t in reversed(out.trace) if t["type"] == "finish" and t.get("error")), None)
        raise LLMError(f"The outreach agent did not produce a draft that passes the checks ({out.reason}"
                       + (f" Last problem: {last}" if last else "") + ")", "invalid_output")
    return {**out.result, "new_facts": record["new_facts"]}, out.usage, out.trace
