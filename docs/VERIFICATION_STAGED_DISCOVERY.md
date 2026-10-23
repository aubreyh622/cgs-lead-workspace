# Staged discovery verification

Date: 2026-10-22, America/Toronto.

The staged research core and persisted candidate queue are implemented. One authorized
same-model snapshot continuation finished normally, investigating Bosch and PepsiCo and
saving one preliminary card. Final run status: **incomplete, 1/2**. The saved card is
**Needs research**, not handoff-ready. No second real trial was started.

## Implementation

- `lead_agent/discovery.py`: versioned entry point, frozen discovery inputs, grounded
  nominations, persisted queue, official-site resolution, company retrieval, assessment,
  strict validation/save, bounded repair and terminal dispositions.
- `lead_agent/worker.py`: explicit workflow dispatch and optional stage-owned retrieval.
  Existing saved runs without the workflow version retain their original behavior.
- `lead_agent/research_prompt.py`: shared prepared-company instructions extracted from
  the existing seed path without changing their content.
- `lead_agent/providers.py`: small stage-specific structured requests using the unchanged
  configured model. Existing company assessment still uses its compact action envelope.
- `tests/research/test_staged_discovery.py`: 19 controlled-provider cases.

The integrated UI/API remains known-company seed research. No discovery screen, orchestration
policy, outreach, qualification handoff, direction change or CRM write was added. The sibling
project, teammate repository and qualification rules were not edited. See STAGED_DISCOVERY.md
for the maintained stage contract and entry point.

## Offline verification

Final complete suite: **263 passed, 2 deselected in 32.74 seconds**. The two deselected checks
are historical Streamlit UI tests. Earlier checks before the live trial were 262 passed and
2 deselected in 34.53 seconds; the final suite includes the post-trial malformed-output case.

The new staged tests cover exact nomination provenance, duplicate names and publishers,
official-site boundaries, company-local source binding, missing pages, successful save with
human review pending, bounded evidence repair, wrong-company outputs, pending-output Stop/resume,
budget retention, immutable snapshots, cache reuse at exhausted budgets, optional-search skipping,
short-name boundaries, fresh signal searches and malformed adapter output.

Full command:

```powershell
python -B -m pytest tests/p0 tests/p1 tests/p2 tests/p3 tests/p4 tests/p5 tests/research tests/qualification -q --tb=short -p no:cacheprovider --basetemp <fresh-scratch-directory> -k 'not app_loads_and_reviews_synthetic_cards and not simplified_app_requires_keys_before_creating_run'
```

No real model or search requests occur in these offline tests.

## Live protocol

- Model: **gemini-3.5-flash-lite**, unchanged; no account upgrade or model fallback.
- Discovery inputs: the previous 22:50 trial's frozen **2 queries, 10 results, 3 retrieved pages**.
- The model extracted companies without a human-provided company list.
- Brief, rubric, research date, target and limits were copied from the baseline snapshot.
- Target: 2 evidence-valid cards. Limits: searches 6, extractions 8, model calls 14,
  reserved Tavily credits 14, active seconds 360, model requests per minute 4.
- The baseline was not rerun. New official-company searches and retrievals were live.
- One Gemini probe and two Tavily probes are reported separately from research.
- Started **23:12:51 EDT**; finished **23:14:29 EDT**. No unexpected process failure occurred.

## Calls, time and results

| Measure | Previous free action loop | Staged continuation |
| --- | ---: | ---: |
| New research seconds | 61.125 | 95.844 |
| Total seconds including probes/local setup | 64.188 | 97.656 |
| New research model requests | 5 | 7 |
| New research searches | 2 | 5 |
| New research page extractions | 3 | 5 |
| Explicit nominated companies | No queue | 2 |
| Companies investigated through official-site stages | 0 | 2 |
| Cards without deterministic validation issues | 0 | 1 |
| Human-approved / handoff-ready cards | 0 | 0 |
| Final status | incomplete, 0/2 | incomplete, 1/2 |

The staged time is for **additional work after cached discovery acquisition**. The earlier
run paid for that acquisition. These are not equal-cost end-to-end timings, and no speed
improvement is claimed. Different downstream web responses and nondeterministic model
generation also prevent a clean causal or statistical A/B claim.

Including preflight, the new trial made **20 HTTP requests**:

- Gemini: **8** requests, all HTTP 200; **7** research requests plus one probe.
  Total HTTP time **21.171 seconds**, maximum **5.578 seconds**.
- Tavily: **12** requests, all HTTP 200; research used **5 searches and 5 extractions**,
  plus one search and one extraction probe. Total HTTP time **11.500 seconds**.
- Gemini reported **89,461 prompt tokens**, **3,173 candidate tokens**, **92,634 total tokens**;
  **20,257 cached content tokens** are included in provider usage.
- Tavily reported **7 credits** including probes. Research reserved 10 credits conservatively.
  Invoice cost and account billing tier were not inspected; dollar cost is unknown.
- HTTP 429, HTTP 503, timeouts and provider HTTP errors: **0**.
- Total HTTP time: **32.671 seconds**. Remaining wall time: **64.985 seconds**, including
  configured pacing and local processing; it is not provider latency.

### Bosch

The model selected `bosch.com` from actual resolution search results. The worker read its
homepage, two official search results, and one repair result. Initial results included an
old annual report and a chip-factory story, illustrating weak date/relevance ranking.

Three real analysis responses failed: malformed nested JSON, schema/passage validation,
and unsupported `capability_fit` evidence references. No Bosch card was saved. Its queue
entry remains **needs_research**, with retrieved sources and explicit issues.

### PepsiCo

The model selected `pepsico.com` and read its homepage. The signal search returned off-domain
results, which were not admitted as company evidence. One bound Research record passed the
existing deterministic checks and was saved as **Needs research**.

The saved card still has substantial limitations:

- No explicit industry observation supports the model's `eligible` label.
- US operations are inferred from a Texas image-asset filename; that is weak evidence.
- The digital-agriculture event points to a homepage news link, not the article body.
- Publication date, current timing, verified buyer and contact remain unknown.

This is a preliminary research artifact, not an accepted useful lead. The actual article,
company manufacturing/operations pages and substantive event details need further checking.
No automatic human approval or qualification acceptance was performed.

## Post-trial correction

The trial exposed a local attempt-counter bug: the adapter could reject malformed nested
JSON after a successful model response but before returning text and usage. The global
request counter included that request, but the per-company assessment counter did not.
Bosch therefore used three real responses while its saved assessment counter showed two.

After the one live trial ended, `StagedDiscovery.model` was corrected to count adapter
ValueError responses when a model request had been consumed. Pre-call Stop/Halt does not
consume an assessment attempt. The added regression case verifies exactly two malformed
responses, an explicit unresolved disposition and no invalid saved card.

This correction passed the final offline suite. **It was not followed by another live run.**
Raw trial logs and counters were retained, including all seven real research model calls.
HTTP instrumentation is authoritative for token totals because an adapter parse failure can
prevent usage from reaching the worker's accumulated usage map.

## Assessment and boundaries

The same model now nominated companies and progressed through official-company research,
where the free action loop had stopped. This supports making tasks and transitions explicit.
It does not establish that this model is sufficient for reliable discovery, or that changing
the model would solve evidence quality. Formatting, evidence references, source ranking and
following actual event pages remain material limitations.

No genuine consultant feedback was supplied; the orchestrator was not exercised. The trial
used a separate SQLite file. Main workspace row counts were unchanged, and application source
hashes were unchanged during the trial. Only the documented retry correction followed it.

## Evidence

Local ignored artifacts:

- Baseline: `runtime/live-tests/2026-10-22-225033-af8e50/`.
- Staged run: `runtime/live-tests/2026-10-22-231251-a741a4/`.
- Runner: `runtime/live-tests/run_staged_snapshot_trial.py`.

The staged directory contains the frozen plan/input snapshot, HTTP calls, run snapshot,
candidate queue, company cards, research database, comparison, report and quality review.
Keys stayed in `.env`; headers and request bodies were not logged. No second real trial,
paid model switch or further implementation phase was started.
