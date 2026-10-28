# Connecting a search service (for CGS)

The workspace never searches the web directly. Two agents reach the web through one **search plug** (`api/search.py`):
- the discovery agent (Step 1), which finds candidate companies;
- the research agent (Steps 2–3), which reads a company's pages.

Which search service sits behind the plug, and what it searches for, is set in `config/search.json`. You can change it without touching the agents.

## Settings (`config/search.json`)

| Setting | What it does |
|---|---|
| `provider` | `mock` = synthetic internet with invented companies (default, free, for demos and tests). `tavily` = real web through Tavily. Or the name of your own provider (see below). `WEB_MODE` in `.env` overrides this. |
| `custom_queries` | Your own search queries, for example `["manufacturer completes acquisition Ohio", "appoints Chief Digital Officer manufacturer"]`. Leave empty to let the AI write 4 news queries from the current research direction. |
| `recency_days` | Only consider results published in the last N days (default 183). |
| `news_only` | Search news only (default true). |
| `results_per_query` | Results kept per query (default 5). |

What the agents look *for* (problem, signals, buyer roles, exclusions, excluded domains) is the **research direction**. It is changed through the workspace's direction proposals and approvals, so every change is versioned and approved by a person.

## Adding your own search service

1. In `api/search.py`, write a class with this one method:

```python
class MySearch:
    name = "mysearch"

    def __init__(self, results: int = 5):
        ...  # read your API key from the environment, never from code

    def search(self, query: str, start_date=None, news: bool = False) -> list[dict]:
        # return up to `results` items:
        # {"title": str, "url": str, "content": str (a snippet or the page text), "published": "YYYY-MM-DD" or ""}
        ...
```

2. Register it: `PROVIDERS["mysearch"] = MySearch`.
3. Set `"provider": "mysearch"` in `config/search.json`, and put the key in `.env`.
4. Run `Check Research.cmd` to see what it finds and what the code checks keep. Every search round is also saved to `runtime/discovery-last-round.json`.

Raise `api.llm.LLMError` with a short message when the service fails. The workspace then shows the error and records a failed run instead of inventing results.

## What stays the same whatever the provider

- A nominated company must appear in a result the provider actually returned.
- A person's name is kept only when it appears in the source text.
- Research facts must quote a page word for word.
- Excluded domains are removed, and a person picks every candidate before research spends anything.

## Research on the real web

- With `provider: mock`, research runs a short AI reading of the company's synthetic pages (`api/mock_research.py`).
- With `provider: tavily`, research runs Aubrey's research v2 pipeline on the real web (`api/research_bridge.py`). It is Gemini-only and capped per company at 10 searches, 12 page reads, 18 model calls and 8 minutes.
- To use another provider for research on the real web, the pipeline's `LiveProviders` class (`lead_agent/providers.py`) needs the same search method. This is a follow-up task.

## The synthetic internet (for demos)

`api/seed/mock_web.json` is built by `tools/build_mock_web.py`. It holds about 75 invented pages:
- 22 US manufacturers that fit the direction, each with a website, a leadership page and dated news;
- 8 distractors that should not be nominated: a bank, a hospital, a consultancy, a software company, a retailer, a distributor, a Swedish manufacturer with no US operations, and an excluded domain;
- 10 simulated client conversations with different outcomes: ready to buy, need without budget, authority unclear, defer, decline and courtesy-only.

To add a company or a conversation, add a `co(...)` entry in the script and run `python tools/build_mock_web.py`.
`Check Workflow.cmd` runs Steps 1–8 for a random simulated company, or for one you name.
