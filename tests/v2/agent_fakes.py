"""Fake tool-using agents for the tests: they answer in Gemini's function-calling format, the way a careful model would.

play(payload, evidence=..., draft=...) looks at which tools the agent was given and plays that agent:
  research (person lookup)  company pages -> read the leadership page -> answer
  evidence                  read the notes -> propose each item (and ask the research agent) -> finish
  outreach                  read the brief -> submit the draft (again, if it is rejected)
  manager                   read the board -> delegate and write notes -> finish
  discovery                 read the direction -> search the first signal -> nominate -> finish
  next step                 read the situation -> suggest an action for up to three gaps -> finish
  research brief            company pages -> read up to three -> one fact from each -> finish
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


def play(payload, *, evidence=None, draft=None, manager=None, discovery=None, actions=None, research=None):
    names = tool_names(payload)
    if "add_problem" in names:  # the direction agent
        return play_direction(payload)
    if "add_news" in names:  # the research agent's refresh task
        return play_refresh(payload)
    if "set_contact" in names:  # the research agent's brief task (the outreach agent has add_fact too)
        return play_research(payload, research or default_facts)
    if "suggest" in names:
        return play_actions(payload, actions or default_actions)
    if "nominate" in names:
        return play_discovery(payload, discovery or (lambda direction, found: []))
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


def play_discovery(payload, pick):
    """direction -> search_news (custom query or first signal) -> nominate what pick(direction, results) returns -> finish."""
    done = results(payload)
    if not done:
        return turn(("direction", {}))
    if len(done) == 1:
        d = done[0][1]
        return turn(("search_news", {"query": (d.get("run_these_searches_first") or [f"manufacturer {d['signals'][0].lower()}"])[0]}))
    if len(done) == 2:
        calls = [("nominate", n) for n in pick(done[0][1], done[1][1]["results"])]
        if calls:
            return turn(*calls)
    return turn(("finish", {"summary": "Searched once and nominated what matched."}))


def default_actions(situation):
    return [{"gap_key": g["gap_key"], "title": f"Ask about {g['label'].lower()}", "reason": g["label"], "question": g["question"]}
            for g in situation["open_gaps"][:3]]


def play_actions(payload, plan):
    """situation -> suggest what plan(situation) returns -> finish (suggest again first if every suggestion was rejected)."""
    done = results(payload)
    if not done:
        return turn(("situation", {}))
    if len(done) == 1:
        calls = [("suggest", a) for a in plan(done[0][1])]
        if calls:
            return turn(*calls)
    return turn(("finish", {"summary": "Suggested the next actions."}))


def default_facts(page):
    """One fact from a page: its first sentence."""
    first = page["text"].split(". ")[0].strip()
    return [{"url": page["url"], "quote": first, "fact": first, "category": "signal"}] if len(first) >= 15 else []


def play_research(payload, facts):
    """company_pages -> read up to three of them -> add facts(page) for each page read -> finish."""
    done = results(payload)
    if not done:
        return turn(("company_pages", {"topic": "recent news"}))
    if len(done) == 1:
        found = done[0][1].get("pages", [])[:3]
        if found:
            return turn(*[("read_page", {"url": p["url"]}) for p in found])
    if all(name in ("company_pages", "read_page") for name, _ in done):
        calls = [("add_fact", f) for _, page in done[1:] if "text" in page for f in facts(page)]
        if calls:
            return turn(*calls)
    return turn(("finish", {"identity_confirmed": True, "hypothesis": "A possible need for integration and operating-model planning.",
                            "relevance": "Matches CGS operating-model work.", "alternative": "The company may handle it internally.",
                            "unknowns": ["Who owns the budget?"], "confidence": "medium"}))


def play_refresh(payload):
    """company_pages(news) -> read the dated pages -> add_news with each page's first sentence and date -> finish."""
    done = results(payload)
    if not done:
        return turn(("company_pages", {"topic": "news"}))
    if len(done) == 1:
        found = sorted((p for p in done[0][1].get("pages", []) if p["published"] != "undated"), key=lambda p: p["published"], reverse=True)[:3]
        if found:
            return turn(*[("read_page", {"url": p["url"]}) for p in found])
    if all(name in ("company_pages", "read_page") for name, _ in done):
        calls = [("add_news", {"url": page["url"], "quote": page["text"].split(". ")[0].strip(), "fact": page["text"].split(". ")[0].strip(),
                               "category": "signal", "date": page["published"]})
                 for _, page in done[1:] if "text" in page and len(page["text"].split(". ")[0]) >= 15]
        if calls:
            return turn(*calls)
    return turn(("finish", {"summary": "Checked the company's recent news."}))


def play_direction(payload):
    """library_contents -> read the first two solutions -> one problem from each (its first sentence) -> finish."""
    done = results(payload)
    if not done:
        return turn(("library_contents", {}))
    if len(done) == 1:
        sols = [d["doc_id"] for d in done[0][1]["documents"] if d["type"] == "solution"][:2]
        return turn(*[("read_document", {"doc_id": d}) for d in sols])
    if all(n in ("library_contents", "read_document") for n, _ in done):
        calls = []
        for _, doc in done[1:]:
            first = doc["passages"][0]
            quote = first["text"].split(". ")[0]
            calls.append(("add_problem", {"problem": "Clients need help with: " + doc["title"].split(": ")[-1].lower() + ".",
                                           "signals": ["completes acquisition", "announces new plant"], "buyer_roles": ["COO"],
                                           "passage_id": first["passage_id"], "quote": quote}))
        return turn(*calls)
    return turn(("finish", {"summary": "Two problems from the first two solutions."}))


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
