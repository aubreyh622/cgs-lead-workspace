"""Fake tool-using agents for the tests: they answer in Gemini's function-calling format, the way a careful model would.

play(payload, evidence=..., draft=...) looks at which tools the agent was given and plays that agent:
  research (person lookup)  company pages -> read the leadership page -> answer
  evidence                  read the notes -> propose each item (and ask the research agent) -> finish
  outreach                  read the brief -> submit the draft (again, if it is rejected)
"""
import json
import re


def turn(*calls, tokens=5):
    """One model turn that asks for these tools: turn(("notes", {}), ("propose", {...}))."""
    return {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": n, "args": a}} for n, a in calls]}}],
            "usageMetadata": {"promptTokenCount": tokens, "candidatesTokenCount": 1}}


def tool_names(payload) -> set[str]:
    return {d["name"] for d in payload["tools"][0]["functionDeclarations"]}


def results(payload) -> list[tuple[str, dict]]:
    """What the tools returned so far, in order: [(tool name, result)]."""
    out = []
    for content in payload["contents"]:
        for part in content["parts"]:
            if "functionResponse" in part:
                r = part["functionResponse"]
                out.append((r["name"], json.loads(r["response"]["result"])))
    return out


def play(payload, *, evidence=None, draft=None, manager=None):
    names = tool_names(payload)
    if "delegate" in names:
        return play_manager(payload, manager or (lambda board: []))
    if "propose" in names:
        return play_evidence(payload, evidence or (lambda text: {"proposals": [], "people_to_find": []}))
    if "submit_draft" in names:
        return play_outreach(payload, draft or default_draft)
    return play_person(payload)


def play_evidence(payload, evidence):
    done = results(payload)
    if not done:
        return turn(("notes", {}))
    if len(done) == 1:
        text = done[0][1]["new_note"]["text"]
        ans = evidence(text)
        proposals = ans["proposals"] if isinstance(ans, dict) else ans
        people = ans.get("people_to_find", []) if isinstance(ans, dict) else []
        calls = [("search_library", {"query": text})] if any((p.get("source") or "notes") != "notes" for p in proposals) else []
        calls += [("propose", {"kind": p["kind"], "key": p["key"], "value": str(p["value"]), "quote": p["quote"], "why": p.get("why", ""),
                               "evidence_type": p.get("evidence_type", "client_statement"), "source": p.get("source") or "notes"})
                  for p in proposals]
        calls += [("ask_research", {"role": x["role"], "quote": x["quote"]}) for x in people]
        if calls:
            return turn(*calls)
    return turn(("finish", {"summary": "Went through the notes."}))


def play_manager(payload, plan):
    """board -> the planned delegations and notes (plan(board) returns [(tool, args)]) -> finish."""
    done = results(payload)
    if not done:
        return turn(("board", {}))
    if len(done) == 1:
        calls = plan(done[0][1])
        if calls:
            return turn(*calls)
    return turn(("finish", {"summary": "Reviewed the board."}))


def default_draft(brief):
    f = brief["facts"][-1]
    return {"subject": "Following up", "body": f"Hi, [[{f['id']}|{' '.join(f['text'].split()[:5])}]]. A short call? Alex Morgan, CGS Advisors"}


def play_outreach(payload, draft):
    done = results(payload)
    if not done:
        return turn(("brief", {}))
    brief = done[0][1]
    return turn(("submit_draft", draft(brief)))  # submitted again if the check rejected it


def play_person(payload):
    from api.services import role_matches
    task = payload["contents"][0]["parts"][0]["text"]
    role = re.search(r"Find the (.+?) at ", task).group(1)
    done = results(payload)
    if not done:
        return turn(("company_pages", {"topic": "leadership team"}))
    name, result = done[-1]
    nothing = {"found": False, "name": "", "title": "", "url": "", "quote": ""}
    if name == "company_pages":
        page = next((x for x in result["pages"] if "leadership" in x["url"]), None)
        return turn(("read_page", {"url": page["url"]})) if page else turn(("finish", {**nothing, "notes": "No leadership page."}))
    if name == "read_page":
        for person, title in re.findall(r"([A-Z][a-z]+(?: [A-Z][a-z]+)+), ([^,.]+)", result["text"]):
            if role_matches(title, role):
                return turn(("finish", {"found": True, "name": person, "title": title, "url": result["url"],
                                        "quote": f"{person}, {title}", "notes": "Named on the leadership page."}))
    return turn(("finish", {**nothing, "notes": "The leadership page does not name anyone in that role."}))
