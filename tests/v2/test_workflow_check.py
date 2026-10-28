"""The full workflow check (Check Workflow.cmd) acts like a careful consultant in every branch: approve, keep working, nurture, close.
A fake AI stands in for Gemini, so these tests run offline and cost nothing."""
import json
from datetime import date

import pytest

import api.check_workflow as W
from api.llm import make_provider
from api.search import MockSearch
from agent_fakes import play, tool_names


def story_draft(brief):
    f = brief["facts"][0]
    return {"subject": "Your integration", "body": f"Hi, I read that [[{f['id']}|{' '.join(f['text'].split()[:4])}]]. Open to a short call? Alex Morgan, CGS Advisors"}


def quote(text: str, start: str) -> str:
    """A sentence from the notes that starts with these words, copied exactly (the code checks reject anything else)."""
    i = text.index(start)
    end = min(j for j in (text.find(".", i), text.find("\n", i), len(text)) if j != -1)
    return text[i:end]


def prop(kind, key, value, q):
    return {"kind": kind, "key": key, "value": str(value).lower(), "quote": q, "why": "", "evidence_type": "client_statement", "source": "notes"}


class StoryAI:
    """Answers each agent by the shape of the answer it asks for."""

    def __init__(self, label, proposals):
        self.label, self.proposals, self.agents = label, proposals, []

    def __call__(self, url, headers, payload, **kw):
        if "tools" in payload:  # the tool-using agents (evidence, outreach, person lookup)
            names = tool_names(payload)
            self.agents.append("actions" if "suggest" in names else "discovery" if "nominate" in names else "evidence" if "propose" in names
                               else "outreach" if "submit_draft" in names else "person")
            return play(payload, evidence=lambda notes: {"proposals": self.proposals(notes), "people_to_find": []}, draft=story_draft)
        props = set(payload["generationConfig"]["responseJsonSchema"]["properties"])
        user = payload["contents"][0]["parts"][0]["text"]
        if "queries" in props:
            self.agents.append("queries"); ans = {"queries": ["manufacturer integration planning"]}
        elif "candidates" in props:
            self.agents.append("discovery"); ans = {"candidates": []}
        elif "identity_confirmed" in props:
            self.agents.append("research")
            pages = json.loads(user)["pages"]
            home = next(p for p in pages if p["url"].endswith(".example/"))
            ans = {"identity_confirmed": True, "facts": [{"page_index": home["index"], "quote": " ".join(home["text"].split()[:8]),
                                                          "fact": "What the company does.", "category": "other"}],
                   "person_name": "", "person_role": "", "person_page_index": -1, "hypothesis": "Integration planning", "relevance": "Planning design",
                   "alternative": "Internal team", "unknowns": [], "confidence": "medium"}
        elif "subject" in props:
            self.agents.append("outreach")
            f = json.loads(user)["facts"][0]
            ans = {"subject": "Your integration", "body": f"Hi, I read that [[{f['id']}|{' '.join(f['text'].split()[:4])}]]. Open to a short call? Alex Morgan, CGS Advisors"}
        elif "label" in props:
            self.agents.append("label"); ans = {"label": self.label, "why": "test"}
        elif "proposals" in props:
            self.agents.append("evidence"); ans = {"proposals": self.proposals(json.loads(user)["notes"])}
        elif "actions" in props:
            self.agents.append("actions")
            g = json.loads(user)["open_gaps"][0]
            ans = {"actions": [{"gap_key": g["key"], "title": "Ask who approves the budget", "reason": "Open gap", "question": "Who signs off?"}]}
        else:
            self.agents.append("feedback")
            ans = {"summary": "No change proposed.", "propose": False, "field": "problem", "after_list": [], "after_text": "", "title": "",
                   "rationale": "", "feedback_ids": []}
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(ans)}]}}], "usageMetadata": {"promptTokenCount": 5}}


def ardent(n):
    return [prop("score", "B", 4, quote(n, "The integration budget for 2027")), prop("score", "A", 4, quote(n, "Maria and Kevin jointly decide")),
            prop("score", "N", 4, quote(n, "On outside help")), prop("score", "T", 3, quote(n, "They want the common planning")),
            prop("score", "H", 4, quote(n, "Ardent worked with CGS")), prop("score", "E", 3, quote(n, "Kevin sent the plant capacity data")),
            prop("cond", "problem", True, quote(n, "Maria said the seven foundries")), prop("cond", "external", True, quote(n, "On outside help")),
            prop("cond", "next", True, quote(n, "Agreed next step")), prop("cond", "commercial", True, quote(n, "The integration budget for 2027")),
            {**prop("cond", "fit", True, ""), "source": "lib"}]


def orchard(n):
    return [prop("score", "N", 3, quote(n, "Daniel said")), prop("score", "A", 1, quote(n, "He is interested in outside help")),
            prop("cond", "problem", True, quote(n, "Daniel said")), prop("cond", "next", True, quote(n, "Agreed next step"))]


def westford(n):
    q = quote(n, "We selected an integration partner")
    return [{**prop("blocker", "NO_EXTERNAL_SUPPORT", "NO_EXTERNAL_SUPPORT", q)}, prop("cond", "external", False, q)]


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "PAUSE", 0)
    monkeypatch.setattr(W, "ROOT", tmp_path)

    def go(company, label, proposals):
        ai = StoryAI(label, proposals)
        code = W.main([company], prepare=lambda app: setattr(app.state, "provider_factory",
                                                              lambda: make_provider("gemini", "m", key="k", transport=ai)))
        return code, "\n".join(W.LINES), ai
    return go


def fit_from_library(n):
    """Ardent's fit proposal needs a real library passage id, as in the workspace."""
    from api.knowledge import library
    pid = library().search(n, k=1)[0]
    return [p if p["key"] != "fit" else {**p, "source": pid["id"], "quote": pid["text"].split(". ")[0], "evidence_type": "approved_research"}
            for p in ardent(n)]


def test_engaged_and_ready_is_approved_without_an_exception(run):
    code, out, ai = run("ardent", "meet", fit_from_library)
    assert code == 0, out
    assert "Consultant runs the internal conflict check" in out and "with an exception" not in out
    assert "Director of Operations approves" in out and "Final stage: Qualification (approved)" in out and "Matches the story." in out
    assert "actions" not in ai.agents  # nothing left to suggest when the rules say Ready


def test_engaged_but_not_ready_stays_active_with_a_task(run):
    code, out, ai = run("orchard", "need", orchard)
    assert code == 0, out
    assert "Consultant turns the first one into a task" in out and "Final stage: Active" in out and "Matches the story." in out
    assert "Submit" not in out and "actions" in ai.agents


def test_decline_is_closed_at_step_8_with_the_right_reason(run):
    code, out, _ = run("westford", "decline", westford)
    assert code == 0, out
    assert "Director of Operations closes it: Went with another advisor" in out and "Final stage: Closed" in out and "Matches the story." in out
    assert "approves" not in out and "revenue estimate" not in out


def test_defer_moves_to_nurture_on_the_month_the_client_named(run):
    code, out, _ = run("kessler", "defer", lambda n: [])
    assert code == 0, out
    assert "Move to nurture, review on 01 Apr" in out and "Final stage: Nurture" in out and "Matches the story." in out


def test_a_different_ai_reading_is_reported_not_hidden(run):
    code, out, _ = run("ardent", "meet", lambda n: [prop("score", "N", 3, quote(n, "Maria said the seven foundries"))])
    assert code == 0 and "Final stage: Active" in out and "Differs from the story" in out


def test_consultant_helpers():
    assert W.consultant_plan("meet", None) == "qualify" and W.consultant_plan("decline", None) == "close"
    assert W.consultant_plan("need", "FUNDING_UNAVAILABLE") == "close" and W.consultant_plan("meet", "PROJECT_DEFERRED") == "nurture"
    assert W.consultant_plan("courtesy", None) == "nurture" and W.consultant_plan("defer", None) == "nurture"
    assert W.close_reason("FUNDING_UNAVAILABLE", "") == "No budget this year"
    assert W.close_reason("NO_EXTERNAL_SUPPORT", "We are not considering consultants.") == "Client declined"
    today = date(2026, 10, 26)
    sims = MockSearch().simulations
    assert W.review_date(sims["kesslermotion.example"]["notes"], today) == date(2027, 4, 1)
    assert W.review_date(sims["larkfieldpaper.example"]["notes"], today) == date(2027, 3, 1)
    assert W.review_date("We may talk later.", today) == date(2027, 1, 24)
    assert all(s.get("story") for s in sims.values())
