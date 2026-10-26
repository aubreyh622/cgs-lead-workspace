# Verification results

Verified on 20 October 2026 using the existing CGS prototype's Python 3.12.14 environment.

## Automated checks

**61 passed in 14.73 seconds.** The command was the existing prototype interpreter running
`-m pytest -q --tb=short` from this project, with a fresh workspace-local `--basetemp`.
Streamlit's test harness required the local runtime outside the sandbox because the
sandbox restricts local sockets. The automated suite uses no live keys or paid API calls.

Coverage includes exact excerpt/source membership, date provenance, identity ambiguity,
stale/conflicting evidence, exclusions, explicit unknowns, weighted scoring, nullable
contacts, current-role checks, schema round trips, approval gates, immutable outbox,
cross-run duplicate handoffs, provider auth/quota/503 behavior, redaction, request/credit
limits, baseline search construction, worker leases, crash recovery, Stop, pending-action
reuse on resume, JSON/CSV export, mock intake and Streamlit card/export rendering.

The revised five-company test exercises the actual Gemini/Tavily HTTP adapter using
`httpx.MockTransport`. One company has an inaccessible page; it receives an incomplete
card and the other four finish with supported fixture evidence. Other new checks cover
one-source signals, unknown eligibility with a retained provisional score, undated
official leadership pages, omission of unsupported contacts, research-revision history,
per-company allowances, batch resume and API connection diagnostics. Missing keys are
checked before creating a run. Historical fixtures are hidden by default in the UI.
Live-test regressions also cover official www/corporate subdomains, same-domain redirects,
cached page aliases, rejection of unsearched unrelated domains and pacing retained on resume.
The prepared-source path has an HTTP-adapter integration test covering five companies,
including inaccessible pages. It checks the compatible action envelope and local Research
schema, source membership, date filters that retain undated pages, one fallback search,
equivalent citation references, ambiguous dates, and an assessment saved before Stop.

These are controlled responses, not real-company or live-provider validation results.

## Demonstrated backend outputs

The prepared fictional batch contains 25 stable company records:

- 5 high-priority records approved by a mock scenario reviewer.
- 5 medium-priority records deferred.
- 5 low-priority records rejected.
- 10 ambiguous or conflicting cases left Needs research.

The approved outbox contains five schema-valid synthetic records, all marked
`approval_basis=mock` and `research_method=synthetic_fixture`. A fresh local mock intake
accepts five; retry accepts zero and skips five duplicates. The consumer rejects those
records by default unless `--allow-mock` is explicitly supplied.

Five additional public-company cards are labelled `manual_public_web`. Their short
excerpt snapshots contain supported observations and original hypotheses, while gaps
keep them in Needs research. They are not AI-generated discovery results.

## Limits of verification

The provider adapters and orchestration were tested with controlled provider responses.
The user subsequently entered keys directly in the local app. Live connection tests
succeeded for Gemini 3.6 Flash structured output, Tavily search and Tavily page retrieval.
The first Ford run exposed an overly strict official-URL check and the account's Gemini
free-tier limit of five requests/minute. The URL issue was fixed, and new UI runs default
to four model requests/minute. The failed run remains saved; no demo data replaced it.
Actual costs are unknown. No real
20–30 company agent pilot, held-out quality comparison or CGS acceptance review has been
completed. Current scoring, approval roles and pipeline ownership are mock assumptions.
The application reports absent cost/labels/reviews as unknown rather than successful.

Fresh-machine dependency installation, cloud hosting and confidential data integration
remain untested. This is a local identification prototype with a working backend and
review workflow, not a production deployment.
