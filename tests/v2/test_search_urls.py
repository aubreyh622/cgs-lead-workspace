"""Search results never carry a Google redirect link as the source."""
from api.search import TavilySearch, real_url


def test_real_url_unwraps_or_drops_google_redirects():
    assert real_url("https://www.example.com/news/a") == "https://www.example.com/news/a"
    assert real_url("https://www.google.com/url?q=https://www.prnewswire.com/x.html&sa=U") == "https://www.prnewswire.com/x.html"
    assert real_url("https://google.com/url?url=https%3A%2F%2Fexample.org%2Fp") == "https://example.org/p"
    assert real_url("https://www.google.com/url?opi=89978449&q=CAESxwEBxCJF17&sa=U") == ""
    assert real_url("https://www.google.com/search?q=acme") == "https://www.google.com/search?q=acme"


def test_tavily_results_use_the_real_page():
    data = {"results": [
        {"title": "A", "url": "https://www.google.com/url?opi=1&q=CAESxw&sa=U", "content": "x"},
        {"title": "B", "url": "https://www.google.com/url?q=https://news.example.com/b", "content": "y", "published_date": "2026-11-03"},
        {"title": "C", "url": "https://news.example.com/c", "content": "z"},
    ]}
    s = TavilySearch(key="test-key", transport=lambda url, headers, body: data)
    got = s.search("acme")
    assert [r["url"] for r in got] == ["https://news.example.com/b", "https://news.example.com/c"]
    assert got[0]["published"] == "2026-11-03"
