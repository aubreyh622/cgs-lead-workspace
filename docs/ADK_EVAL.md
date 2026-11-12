# Evaluating the agents on Google ADK with agents-cli

The agents moved from a hand-written loop to Google's Agent Development Kit (ADK) (`api/adk_runtime.py`, see
[ADK_MIGRATION.md](ADK_MIGRATION.md)). This page shows how we check that nothing got worse, and what the check found.

## Two layers

| Layer | What it proves | How |
|---|---|---|
| Offline, exact | The ADK runtime sends the AI company **the same requests** as the original loop and ends with the same result and trace, in every edge case: rejected answer, text-only turn, cut-off answers, step limit and last-step note, tool-call limit, unknown tool, answer mid-turn, budget stop, tolerated and fatal tool failures, bad/null/leaked arguments, long results, model failure, tools never overlapping, running inside an event loop | `tests/v2/test_adk_runtime.py` (fake AI companies; a mutation check confirmed it fails when the ADK side differs). The whole offline suite also passes with `AGENT_RUNTIME=adk` and with `legacy` |
| Live, statistical | With a real model (which never answers the same way twice), the agents on ADK are as good as on the original loop | agents-cli: the quality-check cases on both runtimes, graded by local metrics, compared, and held to a bar (below) |

## The live check

```
python -m api.adk_eval pair --reps 3           # or double-click "ADK Eval.cmd"
```

does three things:

1. `generate --runtime legacy` and `generate --runtime adk`: every quality-check case runs end to end through a throwaway
   workspace (the agent, its code checks, the database), on the synthetic internet with email and CRM off. Each case is
   saved as an agents-cli eval case (`runtime/eval/<stamp>-<runtime>/<part>/<dev|holdout>/rep<N>.json`). The case holds
   the agent's runs as events (model turns with tool calls, tool results, accepted or rejected answers), its final answer,
   what the quality check measured (`observed`), and per-run effort (`runs`).
2. `agents-cli eval grade` on each part with `tests/eval/<part>.yaml`. The metrics are local Python files in
   `tests/eval/metrics/`, so **no Google Cloud project is needed and nothing is sent to Google**. agents-cli still sets up its
   Google client, so the harness gives it a placeholder credential that is never used.
3. `agents-cli eval compare` legacy vs ADK, then the parity bar (`BAR` in `api/adk_eval.py`). The result is printed and saved as
   `parity.txt` next to the ADK traces.

The cases are the quality check's: evidence 26 (the AI exam's 6 + 20), research 8 companies, outreach 8 first emails (read by
a second model, Claude Opus by default, against a five-line rubric), discovery 1 round. Every fourth case is **held out**
(saved and graded apart), so a later prompt change can be checked on cases nobody tuned against.

### Metrics

| Part | Metric | Meaning | Bar for ADK |
|---|---|---|---|
| all | `task_completed` | every agent run ended with an accepted answer | at most 0.05 (evidence), 0.10 (research, outreach) below legacy |
| evidence | `no_overclaim` | nothing proposed above what the notes support (the serious error) | **1.0 in every case** |
| evidence | `evidence_recall` | share of the expected evidence found in range | at most 0.05 below legacy |
| research | `no_invented_name` | no brief names a person the pages do not show | **1.0 in every case** |
| research | `research_checks` | share of the six checks (brief, signal, dated, contact, no invented name, recommendation) | at most 0.08 below legacy |
| outreach | `outreach_rubric` | reviewing model's mean over hook, insight, specific, ask, voice (1-5; no draft = 0) | at most 0.30 below legacy |
| outreach | `no_unsupported_claim` | no statement the facts do not support | at most 0.15 below legacy |
| discovery | `no_misfit` | no nomination that is clearly not a fit | **1.0** |
| discovery | `discovery_fit` | share of nominations that fit the direction | at most 0.15 below legacy |
| all | `model_turns`, `answers_sent_back`, `tokens_in_k` | effort and cost | shown, not gated |

The tolerances are about the spread between repeated runs of the same runtime. A real model differs from run to run,
so a parity check on one run would be noise.

### Using it after the migration

The same datasets are CGS's regression check for any change to a prompt, a tool or the model. Examples: the planned
switch of the default to Gemini (`AI_PROVIDER=gemini python -m api.adk_eval generate …`, compared with the Claude
traces), or a new prompt version. With a Google Cloud project, agents-cli's managed metrics (`multi_turn_tool_use_quality`,
`hallucination`) can be added to the configs. They send the traces to Google, which is acceptable only because the cases
are synthetic.

## Result (2026-11-11)

Claude Haiku 5.5 for the agents (the default), Claude Opus 5.5 reading the emails, 3 repetitions per runtime, every case
synthetic. Legacy took 33 min, ADK 32 min. Traces: `runtime/eval/live-legacy`, `runtime/eval/live-adk` (not in git).
Graded by agents-cli 1.13.0 with the local metrics. **PARITY: PASS** on every part, both on the dev cases and on the held-out cases.

| Part (dev cases) | Metric | Legacy | ADK | Bar |
|---|---|---|---|---|
| evidence (60 runs per runtime) | no_overclaim | 0.978 | **1.000** | must be 1.0 |
| | evidence_recall | 0.822 | 0.845 | ≥ legacy − 0.05 |
| | task_completed | 1.000 | 1.000 | |
| research (18) | no_invented_name | 1.000 | 1.000 | must be 1.0 |
| | research_checks | 0.778 | 0.833 | ≥ legacy − 0.08 |
| | task_completed | 0.778 | 0.833 | |
| outreach (18) | outreach_rubric (1-5) | 3.211 | 3.178 | ≥ legacy − 0.30 |
| | no_unsupported_claim | 1.000 | 0.889 | ≥ legacy − 0.15 |
| discovery (3 rounds) | no_misfit / fit | 1.000 / 1.000 | 1.000 / 1.000 | |

| Part (held-out cases) | Metric | Legacy | ADK |
|---|---|---|---|
| evidence (18) | no_overclaim / recall | 1.000 / 0.902 | 1.000 / 0.868 |
| research (6) | no_invented_name / checks | 1.000 / 0.500 | 1.000 / 0.500 |
| outreach (6) | rubric / no_unsupported_claim | 3.200 / 0.833 | 3.000 / 1.000 |

Effort was the same or lower on ADK: evidence 4.2 vs 4.2 model turns, research 4.1 vs 4.8, discovery 6.3 vs 6.7, and the
same prompt tokens apart from noise. Claude prompt caching works on both.

Reading it:
- Every serious error across the 3 repetitions: legacy over-claimed once (evidence `kessler-defer`, rep 3, `external=False`);
  ADK never did. Emails with a statement the facts don't support: legacy 1 of 24, ADK 2 of 24. Both are within the spread
  between repetitions of the same runtime, as expected when the requests are byte-identical.
- Research `task_completed` below 1 on both: two companies (Silverline Medical Devices, Quillon Specialty Chemicals) get
  no brief on **either** runtime, because the domain search on the synthetic internet does not find their website. This
  happens before any agent runs (`api/services.py run_research`), and the quality check on 2026-11-10, before ADK, failed
  the same two the same way. It is a gap in the synthetic data, not in the agents. It explains the research hold-out 0.500.

The single-step agents (moved to ADK after this run: reply labels, the weekly feedback review, LinkedIn notes) send the same
request on both runtimes (`tests/v2/test_adk_runtime.py`, Gemini and Claude formats) and passed live on ADK with Claude: a
reply labelled `meet`, a feedback review that proposed dropping a too-junior buyer role, a grounded LinkedIn note.

Live checks on ADK (`AGENT_RUNTIME=adk`): `Check Workflow` passed (7 real AI runs, all completed). `Check Agents` passed
(24 jobs on the task board, 0 failed, 26 real AI runs, 2 handoffs through the manager, no email sent). `Check Orchestrator`
passed 16/16 **after one fix that both runtimes needed**. The manager saved Daniel's "come back within two weeks" without a
due date on legacy and ADK alike, so the overdue-promise checks failed. Cause: Claude's strict tool use (since 5 Nov) leaves
out optional arguments. Fix: `add_commitment` now always takes `due` (empty when the notes give no date), prompt
`manager-triage-1.4`. After the fix, the check passed on both runtimes.
