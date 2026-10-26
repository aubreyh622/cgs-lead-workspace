# P5 functional acceptance record

P5 validates a local integrated workflow. This record is a synthetic engineering rehearsal,
not CGS client acceptance, live-provider validation, or a measurement of business value.
All company pages, contacts, reviews, approvals, outreach and replies in this rehearsal are fictional.

## Rehearsal and artifacts

Two fictional companies are researched in two rounds using the real worker, validators,
HTTP routes, SQLite stores, opportunity review, receiving workflow and direction versioning.
Only the search, page-extraction and model adapters are deterministic offline fixtures.
The server and databases are closed and reopened between rounds.

- `docs/p5/pilot-results.json`: dated preparation evidence, check IDs, source excerpts, task IDs,
  direction versions, receipts, execution trace and KPI snapshot.
- `runtime/offline-demo/pilot-evidence.json`: full preparation record for the default demo session.
- `runtime/offline-demo/runtime/`: isolated research and qualification databases.
- `docs/VERIFICATION_P5.md`: automated and browser validation results.

The saved preparation report is an immutable historical snapshot. Interactive edits subsequently
change the demo databases and displayed metrics; they do not rewrite that report.

## Two-round behavior

| Round | Company and opportunity | Scripted review | Receiving outcome |
| --- | --- | --- | --- |
| 1, baseline direction | Synthetic Factory 1, strategy team, digital pilot coordination | Research further; 8 minutes recorded | No handoff |
| 1, baseline direction | Synthetic Factory 2, strategy team, acquisition integration planning | Worth exploring; time unknown | Accepted; unknown contact; then fictional contact, outreach record and courtesy reply |
| 2, approved direction | Synthetic Factory 1, operations team, digital pilot coordination | Defer; 4 minutes | Ineligible handoff blocked |
| 2, approved direction | Synthetic Factory 2, operations integration team | Worth exploring; initially 7 minutes, revised to 5 | Same-company warning; returned for clarification; revised and accepted as a distinct opportunity |

After round 1, the simulated feedback analyst proposes a signal change. A scripted named human
approves it. Round 2 uses the resulting version in both actual search queries and assessment
inputs. The old tasks retain their original snapshots. The approval basis remains **mock**.
Fixture outputs stay deterministic; this proves version propagation, not improved relevance.

## Functional gates

All **27 preparation checks pass**. Exact IDs and evidence are in the JSON report.

| Gate | Required behavior | Result |
| --- | --- | --- |
| Provenance | All four cards synthetic, source-bound, dated and without validation issues | Pass |
| Retry | Replayed task starts and receiving decisions do not duplicate work/cases | Pass |
| Human control | Proposal remains pending until a named human approves | Pass |
| Durable memory | Approved version survives restart and reaches next search and assessment | Pass |
| Receiving | Unknown contact allowed; owner, next action, due date and relationship check required | Pass |
| Distinct opportunities | Same-company warning; receiving return creates no case; distinct teams remain separate | Pass |
| Stage integrity | Draft does not contact; recorded reply becomes Active, without confirming qualification | Pass |
| Evidence boundary | Public research never becomes confirmed qualification evidence or BANTHE scores | Pass |
| Metrics | Latest review per opportunity; explicit denominators and missing times | Pass |
| Offline isolation | No live mode, real domains, external API calls, sent messages or CRM writes | Pass |

The preparation creates four tasks, four opportunities, five feedback records, two accepted
handoffs, one returned handoff and two received cases. The seven inherited qualification
samples remain, so Work queue contains nine cases.

## Metric interpretation

The dashboard cohort is the configured theme across all versions and consultants, using the
latest review of each opportunity. It includes synthetic data and displays its count separately.

- Worth exploring: 2 / 4 reviewed opportunities = **50%**. This is scripted, not a quality estimate.
- Median review time: **5 minutes**, from three known latest-review durations (8, 4, 5).
  One unknown duration is excluded. The superseded 7-minute review is not counted again.
- Recorded replies / outreach: **1 / 1 = 100%**, entirely fictional. No email was sent.
- Accepted handoffs: **2**, reflecting receiving decisions rather than sales wins.
- Qualification evidence and coverage: **0** for both received cases.

No historical baseline, time saving, real response rate or commercial conversion was measured.

## Acceptance still pending

| Item | Current status | Evidence needed |
| --- | --- | --- |
| Live Gemini, Tavily and OpenAI account connectivity | Not performed | Authorized local account test and sanitized result |
| Useful signals and appropriate contacts | Not assessed | CGS consultant review of a small public-data sample |
| Direction changes improve relevance | Not assessed | Comparable real rounds, feedback reasons and review of false positives |
| Time reduction and business value | Not assessed | Agreed definitions, baseline and sufficient observations |
| CGS client acceptance | Not requested or recorded | Named client reviewer and explicit decision |
| Production, CRM, mail, scheduling and authentication | Outside this local rehearsal | Separate approved scope and integration work |

No numerical business target is treated as an approved CGS requirement. Agree those targets
and a stopping rule before running the real pilot in `LIVE_PILOT_CHECKLIST.md`.
