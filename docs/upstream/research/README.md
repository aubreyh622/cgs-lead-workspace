# CGS Company Research

Local prototype for company input → public evidence → a possible CGS opportunity → human review.
The immediate milestone is **five real researched company cards**, starting with one company to
check provider access and output quality. Provider connection tests have succeeded; live
research validation is in progress.

## Start and connect

Double-click **Start CGS Identification.cmd**, then open <http://127.0.0.1:8502>.
The launcher uses this project's `.venv`, or the earlier CGS prototype's Python environment
on this computer. For a fresh computer with Python 3.12, run **Setup.cmd** first.

In the sidebar's **API connections**, enter:

1. A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).
2. A Tavily API key from [your Tavily account](https://app.tavily.com/).
3. Keep the displayed `gemini-3.5-flash-lite` model, or explicitly choose another supported ID.

Both models passed a live connection probe on 20 October 2026, but Gemini 3.6 Flash returned
repeated HTTP 503 high-demand errors during research. Flash-Lite completed live research and
is the visible default. The observed account limit was five model requests/minute. New runs
default to **four/minute** under Advanced; other account usage can still cause a pause.

Do not send keys in chat. Sidebar keys stay in the browser's Streamlit session and are not
saved in research records, exports or logs. Restarting the server or losing the session
requires entering them again. Alternatively, an optional local `.env` can hold
`GEMINI_API_KEY`, `TAVILY_API_KEY` and `GEMINI_MODEL`; `.env` is ignored by Git.

Click **Test connections**. This makes one small structured model request, one Tavily
search and one page retrieval; provider usage may apply. Separate sanitized results show
which route works. A successful test establishes access, not research quality.
Gemini 503/high-demand errors are availability failures; quota/authentication errors are
reported separately. A provider failure never loads fictional leads.

## Research one company, then five

Start with **One company**, defaulting to Ford / ford.com. Click **Research company**.
Progress updates automatically. Once finished, inspect identity, observed event, excerpts,
publication dates, the proposed challenge, alternative explanation and missing information.

Then choose **Five-company batch**. The editable five-row table starts with Ford, Lear,
Dana, BorgWarner and Magna. These are research inputs, not asserted prospects or CGS clients.
The pilot geography means manufacturers with **US operations**, not US headquarters only.
The capability is AI/digital strategy and first-mile planning. Both choices are provisional.

The worker researches one company at a time. Ordinary code retrieves the homepage and up to
three official search-result pages before asking the model to produce a structured card.
The initial query applies the brief's lookback date while retaining pages with unknown
publication dates. Search-provider dates are discovery hints, not publication-date evidence.
If sources lack a business signal or are stale, one bounded fallback search adds sources.
Up to three assessment/repair passes use the saved pages. Each company gets its own share
of search, retrieval and time allowances. UI batch caps are 40 searches, 50 page retrievals, 30 model requests,
90 conservatively reserved Tavily credits and 25 active minutes. Retries count toward caps.
Single-company caps are one fifth of those values. Model output is capped at 8,192 tokens
per request. Request caps are not a dollar-price guarantee.

An inaccessible company produces an explicit incomplete card; the remaining companies
can continue. Authentication, quota and repeated temporary provider errors pause the run.
Stop takes effect at a request boundary. Resume retains the current company, sources,
pending action and usage. A worker lease prevents concurrent execution. Hard-limit runs
stay terminal; start a new run for further work. A request already in flight can exceed
a company's time share before the next checkpoint.

## Read the results

The card leads with exact source excerpts, a possible business challenge, where CGS might help,
an alternative explanation and the next investigation. **Sources and evidence** holds exact
excerpts and dates. **Missing information** separates ordinary research gaps from evidence
errors. **Priority calculation** explains the provisional score and coverage.

A verified merger, divestiture or new product can justify investigation. Public proof that
the company needs a consultant is not required. The event must be sourced; its business
implications and external-advice need remain hypotheses. Generic AI activity is weak evidence.
Company revenue/scale and buyer seniority are sourced context, separate from priority and
never treated as a consulting-budget estimate.

One strong official page can support an initial card; further corroboration is encouraged.
Source membership, official identity and assessment references are checked in code. Equivalent
source URLs/IDs are normalized to stored evidence references. An identity observation can be
grounded from a company-name excerpt on its supplied official domain; this does not establish
legal corporate-group identity. Ambiguous numeric dates remain unknown instead of being guessed.
Unknown industry/geography keeps a card in Needs research but can retain a provisional
score. Eligibility evidence is required before handoff. A missing buyer does not block
otherwise useful company research. An undated official leadership page can support a role
checked on the research date; its unknown publication date remains explicit. Unsupported
buyer claims are omitted from the verified contact list and access remains unknown.
Human review is still needed to determine whether an excerpt logically supports a claim.

## Review, configuration and handoff

Review is available after research stops. Approve, defer, reject or request further research.
Approval requires a named reviewer, source checks, eligibility evidence and recorded brief/
rubric approval metadata. Current approval and ownership settings are explicitly **mock**,
as authorized for the prototype; they are not CGS endorsement.

Scoring weights/definitions live in `config/rubric.json`; the capability, signals and
eligibility live in `config/problem_brief.json`. Edit these files and change the version
when definitions change. Each run retains its configuration snapshots. No JSON editing
interface is needed in the main workflow.

Research enrichment saves the previous record and review history, then resets the current
decision. Exported records remain immutable. **Export reviewed results** offers CSV and
approved JSON. The JSON record also powers the card and CSV's `record_json` field. Stable
domain-based IDs and the SQLite outbox prevent duplicate handoffs across retries/runs.
No record is sent to an external application. The mock downstream consumer remains:

```powershell
python -m lead_agent.consume examples/approved_mock_leads.json --allow-mock
```

## Historical tests and next milestone

Earlier synthetic and manual excerpt samples are retained, hidden under **Advanced → Show
historical and synthetic runs**. Their labels distinguish them from autonomous research.
The 25 fictional records repeat five scenario types; they are software fixtures, not a
company-discovery pilot. `scripts/prepare_demo.py` recreates those historical demo artifacts.

Run checks with `python -m pytest -q`. See `VERIFICATION.md` for current results.
Adapters are tested with controlled HTTP responses. Live probes and five-company batches
have completed; research quality still requires source-by-source inspection. After the cards are useful,
expand to a real 20–30-company pilot and compare with the baseline. Business relevance,
CGS rubric calibration, teammate handoff agreement and cost/time improvement are pending.
The market/baseline engine remains available in code, outside the simplified first workflow.

Pinned dependencies are in `requirements.lock`; reference versions/notices are in
`ATTRIBUTION.md` and `third_party/`. Fresh-machine installation remains unverified.
