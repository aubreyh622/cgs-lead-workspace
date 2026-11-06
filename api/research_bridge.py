"""Steps 2-3 research agent (live): a bridge to Aubrey's research v2 pipeline.

Aubrey's code does the real work: find the official site, read pages, extract exact quotes, review the evidence,
and write a brief that a person reviews. It runs in the background with its own budgets
(10 searches, 12 page reads, 18 model calls, 22 Tavily credits, 8 minutes per company) and its own database
(runtime/research.sqlite3). This bridge starts a run for one company and turns the finished company card into
the v2 brief format. It runs on Gemini + Tavily only (Aubrey's provider), whatever AI_PROVIDER says.
"""
from __future__ import annotations

from pathlib import Path
import uuid

from api.llm import DEFAULT_MODELS, LLMError


class ResearchBridge:
    label, prompt = "gemini+tavily", "research-v2 (Aubrey)"

    def __init__(self, root: Path, model: str, service=None):
        if service is None:
            from backend.research_service import ResearchJobStore, ResearchService
            service = ResearchService(root, ResearchJobStore(root / "runtime" / "research.sqlite3"), model or DEFAULT_MODELS["gemini"])
        self.service = service

    def start(self, company: str, domain: str, consultant: str) -> str:
        from backend.research_service import ResearchServiceError
        if not domain:
            raise LLMError("The research agent needs the company website (domain). Add it, then retry.", "config")
        body = {"request_id": "v2-" + uuid.uuid4().hex[:12], "consultant_id": consultant.lower().replace(" ", "-") or "user",
                "mode": "company", "research_version": "v2", "company": {"company": company, "domain": domain}}
        try:
            status, _ = self.service.start(body)
        except ResearchServiceError as e:
            raise LLMError(str(e), "config") from None
        except ValueError as e:
            raise LLMError(f"Research could not start: {e}", "config") from None
        return status["run_id"]

    def status(self, run_id: str) -> dict:
        s = self.service.status(run_id)
        return {"status": s["status"], "stage": s.get("stage"), "usage": s.get("counters", {}), "error": s.get("error"),
                "events": [e if isinstance(e, str) else e.get("message", "") for e in s.get("events", [])][-5:]}

    def result(self, run_id: str) -> dict | None:
        cards = self.service.results(run_id)["company_cards"]
        return cards[0] if cards else None

    def stop(self, run_id: str) -> None:
        self.service.stop(run_id)

    def resume(self, run_id: str) -> None:
        from backend.research_service import ResearchServiceError
        try:
            self.service.resume(run_id)
        except ResearchServiceError as e:
            raise LLMError(str(e), "config") from None


AGENT_DECISIONS = {"accept": "accept", "send_back": "return", "not_now": "reject"}


def recommendation(verdict: dict | None, ready: bool, flags: list, card: dict) -> dict:
    """The research agent's own recommendation (basis "agent") when it gave one; the code checks can only overrule an
    accept, never turn a send-back into an accept. Without one, the checks decide (basis "checks"), which is not a
    judgement of whether the company is worth pursuing."""
    checks = ({"decision": "accept", "reason": "The company is named in its dated, quoted sources and a contact was found. "
                                               "Whether it is worth pursuing (fit, timing, value) is your call."} if ready and not flags
              else {"decision": "return", "reason": "Resolve the flags below before accepting."} if ready
              else {"decision": "return", "reason": "The research agent could not complete a reviewable brief: "
                    + "; ".join(card.get("research_gaps", [])[:2])})
    if not verdict or verdict.get("decision") not in AGENT_DECISIONS:
        return {**checks, "basis": "checks"}
    decision = AGENT_DECISIONS[verdict["decision"]]
    if decision == "accept" and checks["decision"] != "accept":
        return {"decision": "return", "basis": "agent", "reason": verdict.get("reason", ""), "overruled": "accept", "checks": checks["reason"]}
    return {"decision": decision, "basis": "agent", "reason": verdict.get("reason", "")}


def card_to_brief(card: dict, owner: str, due: str, usage: dict) -> tuple[dict, dict | None]:
    """Turn Aubrey's company card into the v2 brief. Returns (brief, verified person or None)."""
    r = card["research"]
    sources = {s["source_id"]: s["url"] for s in card.get("sources", [])}
    facts = []
    for o in r.get("evidence", []):
        if o["category"] in ("signal", "problem", "timing", "identity", "industry", "account", "person", "other"):
            facts.append({"id": f"f{len(facts) + 1}", "text": o["observed_fact"], "quote": o["excerpt"],
                          "src": sources.get(o["source_id"], o["source_id"]), "date": o.get("publication_date") or "undated"})
        if len(facts) == 8:
            break
    flags = []
    if r.get("identity") != "confirmed":
        flags.append({"type": "Identity unconfirmed", "text": f"The company's identity is {r.get('identity')}. Check the official site."})
    if r.get("eligibility") == "ineligible":
        flags.append({"type": "Outside the direction", "text": r.get("eligibility_reason", "")})
    for c in r.get("conflicts", [])[:2]:
        flags.append({"type": "Conflicting evidence", "text": c})
    people = r.get("people") or []
    person = {"name": people[0]["name"], "title": people[0]["role"]} if people else None
    contacts = [{"name": p["name"], "title": p["role"], "url": p.get("url", ""), "why": p.get("why", "")} for p in people[:3]]
    if not person:
        flags.append({"type": "No contact found", "text": r.get("suggested_next_investigation", "No verified contact in public sources.")})
    for issue in (card.get("validation_issues") or [])[:2]:
        flags.append({"type": "Research gap", "text": issue})
    ready = card.get("review", {}).get("decision") == "Ready for review"
    comps = card.get("priority", {}).get("components", {})
    band = card.get("priority", {}).get("band", "Needs research")
    brief = {
        "facts": facts, "hypothesis": r["problem_hypothesis"], "relevance": r["cgs_relevance"], "alternative": r["alternative_explanation"],
        "unknowns": r.get("unanswered_questions", [])[:4], "flags": flags,
        # No revenue tier: public research does not show what the work is worth (it is estimated at Qualification).
        # The relationship comes from the CRM and workspace checks at Step 4, not from the brief.
        "priority": {"tier": None, "tierWhy": "Revenue to CGS is not estimated from public research.",
                     "authority": (comps.get("buyer_access") or {}).get("assessment") or None,
                     "authorityWhy": (comps.get("buyer_access") or {}).get("reason", ""),
                     "relationship": None, "relationshipWhy": "",
                     "fit": band, "fitWhy": (comps.get("capability_fit") or {}).get("reason", ""), "confidence": r.get("confidence", "low").capitalize()},
        "recommendation": recommendation(r.get("verdict"), ready, flags, card),
        "contacts": contacts,
        "suggested": {"owner": owner, "next": "Send tailored outreach", "due": due},
        "usage": {"searches": usage.get("searches", 0), "pages": usage.get("extractions", 0), "calls": usage.get("model_calls", 0)},
    }
    return brief, person
