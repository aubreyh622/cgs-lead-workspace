"""Google Search through Gemini (api/search.py GoogleSearch): results are the pages Google returned (grounding chunks),
redirect links are followed to the page's own address, previews and dates come from what the model said each page
supports, and pages are read straight from the site. Offline: the Gemini answer and the redirects are fakes."""
from datetime import date

import httpx
import pytest

import api.search as S
from api.llm import LLMError
from lead_agent.providers import ProviderError

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"


def answer(chunks, supports):
    return {"candidates": [{"content": {"parts": [{"text": "..."}]},
                            "groundingMetadata": {"groundingChunks": chunks, "groundingSupports": supports}}]}


GROUNDED = answer(
    [{"web": {"uri": REDIRECT, "title": "industrywire.example"}},
     {"web": {"uri": "https://www.northwind.example/news/cdo", "title": "northwind.example"}},
     {"web": {"uri": REDIRECT, "title": "industrywire.example"}}],  # the same page twice
    [{"segment": {"text": "2026-10-06: Northwind named a Chief Digital Officer."}, "groundingChunkIndices": [0, 2]},
     {"segment": {"text": "undated: Northwind runs three plants in Ohio."}, "groundingChunkIndices": [1]},
     {"segment": {"text": "Northwind's CDO will lead the ERP programme."}, "groundingChunkIndices": [0]}])


def google(monkeypatch, data=GROUNDED, calls=None, **kw):
    monkeypatch.setenv("GEMINI_BACKEND", "ai_studio")

    def transport(url, headers, body):
        if calls is not None:
            calls.append((url, headers, body))
        if isinstance(data, Exception):
            raise data
        return data
    return S.GoogleSearch(key="k-secret", transport=transport, model="gemini-test",
                          resolver=lambda u: "https://www.industrywire.example/2026/10/northwind-cdo", **kw)


def test_results_are_the_pages_google_returned_at_their_own_address(monkeypatch):
    calls = []
    out = google(monkeypatch, calls=calls).search("Northwind Chief Digital Officer", start_date=date(2026, 5, 1), news=True)
    assert [r["url"] for r in out] == ["https://www.industrywire.example/2026/10/northwind-cdo", "https://www.northwind.example/news/cdo"]
    assert out[0]["published"] == "2026-10-06" and "Chief Digital Officer" in out[0]["content"] and "ERP" in out[0]["content"]
    assert out[1]["published"] == "" and out[1]["title"] == "northwind.example"
    url, headers, body = calls[0]
    assert url.endswith("/models/gemini-test:generateContent") and headers == {"x-goog-api-key": "k-secret"}
    assert body["tools"] == [{"google_search": {}}]
    prompt = body["contents"][0]["parts"][0]["text"]
    assert "Northwind Chief Digital Officer" in prompt and "news" in prompt and "2026-05-01" in prompt


def test_unresolvable_redirects_are_dropped_and_results_capped(monkeypatch):
    g = google(monkeypatch, results=1)
    g.resolver = lambda u: ""
    assert [r["url"] for r in g.search("x y z")] == ["https://www.northwind.example/news/cdo"]
    g2 = google(monkeypatch, results=1)
    assert len(g2.search("x y z")) == 1


def test_no_grounding_means_no_results(monkeypatch):
    assert google(monkeypatch, data={"candidates": [{"content": {"parts": [{"text": "I could not search."}]}}]}).search("abc") == []


def test_errors_say_what_failed_without_the_key(monkeypatch):
    g = google(monkeypatch, data=ProviderError("HTTP 429: quota for key k-secret", "quota", 429))
    with pytest.raises(LLMError) as e:
        g.search("abc")
    assert e.value.kind == "quota" and "k-secret" not in str(e.value)


def test_a_key_is_needed_except_on_google_cloud(monkeypatch):
    monkeypatch.setenv("GEMINI_BACKEND", "ai_studio")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with pytest.raises(LLMError) as e:
        S.GoogleSearch()
    assert e.value.kind == "auth" and "GEMINI_API_KEY" in str(e.value)
    monkeypatch.setenv("GEMINI_BACKEND", "agent_platform")
    assert S.GoogleSearch().key == ""
    monkeypatch.setenv("GEMINI_BACKEND", "ai_studio")
    monkeypatch.setenv("GEMINI_API_KEY", "abc")
    monkeypatch.setenv("TOOLS_TRANSPORT", "inprocess")
    assert isinstance(S.make_search("google", local=True), S.GoogleSearch)


def test_pages_are_read_from_the_site(monkeypatch):
    html = ("<html><head><title>Leadership | Northwind</title></head><body><nav><p>Menu</p></nav>"
            "<h1>Leadership</h1><p>Priya Desai, Chief Digital Officer</p><footer><p>Copyright</p></footer></body></html>")
    seen = {}

    def get(url, **kw):
        seen["ua"] = kw["headers"]["User-Agent"]
        return httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"}, request=httpx.Request("GET", url))
    monkeypatch.setattr(S.httpx, "get", get)
    page = S.fetch_page("https://www.northwind.example/leadership")
    assert page["title"] == "Leadership | Northwind" and page["content"] == "Leadership\nPriya Desai, Chief Digital Officer"
    assert "CGS-Workspace" in seen["ua"]
    monkeypatch.setattr(S.httpx, "get", lambda url, **kw: httpx.Response(403, request=httpx.Request("GET", url)))
    assert S.fetch_page("https://blocked.example/") is None
    assert google(monkeypatch, reader=lambda u: {"url": u, "content": "x"}).read("https://a.example/")["content"] == "x"


def test_redirects_are_followed_once_per_link(monkeypatch):
    asked = []
    g = google(monkeypatch)
    g.resolver = lambda u: asked.append(u) or "https://www.industrywire.example/a"
    g.search("one")
    g.search("two")
    assert asked == [REDIRECT]
