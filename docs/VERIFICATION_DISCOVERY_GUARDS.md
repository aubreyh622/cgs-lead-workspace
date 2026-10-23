# Discovery execution guards and live rerun

Date: 2026-10-22, America/Toronto.

The user authorized execution constraints and one small live rerun with the existing model.
The constraints passed offline checks. The live rerun retrieved pages but found **0 of 2**
evidence-validated company cards. Its final status is **incomplete**, not client acceptance.
No second live round or further implementation was started.

## Changes

Only the integrated workspace is changed. The sibling research project and teammate repository
were not edited. The model, credentials, qualification logic and human approval rules were retained.

- Problem searches automatically retrieve up to two result pages while extraction and credit
  budgets remain. Cached pages and search results are reused. Seed searches keep their existing
  three-page behavior and official-domain preference.
- All nonseed proposals pass the existing `make_lead()` validation before `save_research()`.
  Unsupported quotes, source references, identity or core assessments cause rejection before
  any lead or research-revision write. Rejection feedback remains in the run state.
- A rejected replacement cannot overwrite an existing valid card. Repeating an unchanged card
  for one company cannot clear another company's pending rejection.
- Discovery targets count cards without validation issues; old invalid cards do not satisfy a
  target. This means provenance-valid research, not commercial qualification or human approval.
- Premature finish with an unresolved proposal, or retrieved pages and no saved card, receives
  at most two repair opportunities before an explicit incomplete result. Existing global limits
  and provider error handling still apply. An empty initial finish remains incomplete immediately.
- Stop/resume retains pending actions, cached sources, spent budgets and repair counts.
- Seed research still permits explicit `Needs research` placeholders when sources are unavailable.
- Save events describe actual persistence outcomes and pending human review, rather than repeating
  the model's claim that a lead is qualified.

## Offline verification

- Full regression: **242 passed, 2 deselected in 29.16 seconds**. The deselected checks are the
  historical Streamlit UI tests, not the integrated workflow.
- Final guard file: **10 passed in 3.19 seconds**. It includes two tests added after full-suite
  collection. Total distinct Python tests passed: **244**.
- Qualification JavaScript rules: **13 passed**.
- The earlier focused inherited checks also passed: **57 passed, 1 deselected**.

The ten guard cases cover retrieval order, attempted saving before retrieval, fabricated quotes,
invalid replacement, unavailable pages and snippet-only evidence, cache reuse, small web budgets,
legacy invalid cards, cross-company pending feedback, and stop/resume with bounded repairs.
These are controlled-provider checks; they make no real API requests.

Full-suite command:

```powershell
python -B -m pytest tests/p0 tests/p1 tests/p2 tests/p3 tests/p4 tests/p5 tests/research tests/qualification -q --tb=short -p no:cacheprovider --basetemp <fresh-scratch-directory> -k 'not app_loads_and_reviews_synthetic_cards and not simplified_app_requires_keys_before_creating_run'
```

Final focused command: `python -B -m pytest tests/research/test_discovery_execution_guards.py -q`.
JavaScript command: `node --test test/rules.test.cjs`.

## Interruption and recovery

Before the Codex restart, the worker edits and eight initial guard tests were saved. The first
attempt to launch the added tests encountered Windows system resource error **1450**, before
returning test results. The repair's live rerun had not started. No research/test process was
found running after restart. The earlier 57-test result was retained in the conversation output.

After restart, the eight tests passed, two additional regression cases were added, the checks
above passed, and the single authorized live rerun started at **22:50:33 EDT**. It finished
normally at approximately **22:51:37 EDT**. Its incomplete outcome is a research shortfall,
not an interrupted process or provider exception.

## Same-model live comparison

Both runs use `gemini-3.5-flash-lite`, the same brief, empty seeds, problem strategy and target 2.
Both use limits of 6 searches, 8 extractions, 14 model calls, 14 reserved Tavily credits,
360 active seconds and 4 model requests per minute. Actual search results are not deterministic.

| Measure | Before repair | After repair |
| --- | ---: | ---: |
| Total elapsed seconds | 95.125 | 64.188 |
| Research elapsed seconds | 91.438 | 61.125 |
| Research Gemini requests | 7 | 5 |
| Research Tavily searches | 4 | 2 |
| Research Tavily extractions | 0 | 3 |
| Stored source pages | 0 | 3 |
| Saved company cards | 1, invalid | 0 |
| Cards without validation issues | 0 | 0 |
| Provider HTTP errors | 0 | 0 |
| Final status | incomplete | incomplete, 0/2 |

The after-repair trial made **13 HTTP requests** including preflight:

- Gemini: **6** total, including one preflight; all HTTP 200. Median request time **1.047 seconds**,
  maximum **1.25 seconds**, sum **6.391 seconds**.
- Tavily: **7** total, including one search and one extraction preflight; all HTTP 200. Research
  used two searches and three extractions. Total request time **6.079 seconds**.
- Gemini reported **100,484 prompt tokens**, **314 candidate tokens**, **100,798 total tokens**;
  **56,791 cached content tokens** are included in provider usage, not additional prompt tokens.
- Tavily reported **4 credits** across successful responses, including preflight. The worker
  conservatively reserved 5 credits for research. Reservation and reported consumption differ.
- HTTP 429, HTTP 503, timeouts and provider exceptions: **none observed**. Two finish deferrals
  were recorded. There were no live evidence rejection errors because no card was submitted.
- Total HTTP time was **12.470 seconds**. The remaining **51.718 seconds** includes configured
  rate pacing and local work; it must not be described as API latency.

No paid upgrade or alternate model was used. Account billing tier and invoice cost were not
inspected; actual dollar cost is unknown.

## Interpretation and limits

The model issued two broad searches and retrieved overview pages from XDUCE, TEKsystems and PwC.
One repeated page was reused without another extraction. It did not select a manufacturer for
official-company research or submit a `record_lead` proposal. After two repair deferrals, its
third finish request produced an honest 0/2 incomplete result with unused research budget.

Therefore the live run demonstrates page retrieval, caching and bounded finish handling.
It does **not** demonstrate live rejection or successful saving of a candidate. Those branches
passed the offline checks. Candidate selection, query specificity and research follow-through
remain unresolved. The shorter run and absence of invalid saved cards do not prove better lead
discovery, provider stability over time, or useful CGS opportunities.

The remaining development proposal is to make signal search, candidate extraction and official
company verification explicit stages, so the model has a concrete candidate queue to investigate.
This proposal was not implemented or tested in another live round.

Deterministic quote/source/date/reference checks do not prove that every claim follows from its
excerpt. Semantic accuracy, company fit, current contacts and commercial value still require
human review. No human approval, outreach, qualification handoff or feedback-based direction
change occurred. The orchestrator was not exercised without genuine consultant feedback.

Discovery remains a research-core trial. The integrated HTTP/UI still accepts known-company
research tasks. Main workspace row counts were unchanged; the trial used its own SQLite database.
Application source/configuration hashes stayed unchanged during the live run.

## Evidence

Ignored local artifacts, relative to the workspace:

- Baseline: `runtime/live-tests/2026-10-22-221931-46b6f0/`.
- Rerun: `runtime/live-tests/2026-10-22-225033-af8e50/`.
- The rerun contains `report.md`, `report.json`, `http-calls.jsonl`, `run-snapshot.json`,
  `research.sqlite3`, `company-cards.json`, `company-cards.csv`, `comparison.json`,
  `offline-verification.json`, the frozen plan and preflight results.

Credentials were loaded privately from `.env`; request headers and bodies were not logged.
