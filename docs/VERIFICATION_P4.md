# P4 verification

Date: 2026-10-22 (America/Toronto). Scope: bounded feedback orchestration, human-approved
direction versions, future-task use, and English maintained engineering documentation.
P4 is complete. Stop here; P5 has not been authorized or started.

## Results

- Python: **229 passed, 2 deselected in 34.20 seconds**.
  P0 12, P1 31, P2 27, P3 24, P4 35, inherited research 62, qualification 38.
- JavaScript: **13 rule tests passed**; app.js, research.js and engine.js syntax checks passed.
- Modified Python files parse; exported workspace JSON schemas parse.
- Maintained README, AGENTS and top-level engineering documents contain no Chinese text.
  Nine existing documents were translated. Upstream original archives remain unmodified.

## P4 coverage

- Manual proposal remains pending until an explicit valid human decision.
- Request replay, changed-input conflict, no-op patches and invalid human decision fields.
- Whitelisted patches only; weights, model, budget, industry and approval-basis injection rejected.
- Feedback existence, scope, consultant, current version, uniqueness and latest-review checks.
- Personal approvals affect only the named consultant. Actual subsequent tasks for another
  consultant use the shared version; the named consultant's tasks use the personal version.
- Shared and CGS-scoped changes target the configured shared theme; excluded domains block new tasks.
- Stale approval and superseded feedback cannot overwrite current settings.
- Approval transaction fault injection rolls back the version/head and decision; replay/concurrent
  approval creates only one approved version.
- Config/rubric files are unchanged by approval. Mock approval basis remains mock.
- AI review reserves one call, creates only a pending proposal, and replays without another call.
- No-change model response, invalid output, provider failure/redaction, missing configuration,
  busy review and expired interrupted review are handled without automatic retries or fake success.
- Real Gemini adapter code is exercised with httpx.MockTransport, including request headers,
  bounded structured output and server-side validation. No real Gemini account is contacted.
- KPI denominators, null durations, sample counts and synthetic counts are verified.
- Approved signals enter the next task's actual search query and model prompt; old task snapshots
  retain their original version. Approval racing task creation is caught inside the task transaction.
- Default and modified searches remain within the inherited 500-character query limit.
- Real localhost HTTP covers dashboard, model-review creation/status, manual proposal,
  approval/replay, effective-theme reads, new-task version use, CSRF and invalid context.

## Browser validation

Headless Chrome + Playwright passed the full P2/P3 path and the following P4 flow:

1. Record theme-scoped feedback on a saved research opportunity.
2. Save a clearly labeled manual pending proposal; current direction remains unchanged.
3. Run one controlled AI review; inspect its pending proposal.
4. A named human approves the AI proposal after reviewing its diff.
5. Reject the now-unneeded manual proposal without changing the approved head.
6. Start another research task; its persisted theme_version matches the approved version.
7. Reload and verify decisions/versions remain available.

The workflow-manager screenshot was visually inspected. The UI preserves metric-cohort and
synthetic-data labels, no-data states and separate review/approval statuses. No pageerror occurred.
The inherited browser checks also cover sources, input escaping, mobile layout, lost-response
idempotency, missing configuration, research stop/resume, acceptance and manual contact stages.
Non-local browser requests were blocked, and providers were explicit controlled fixtures.

## Fix found during verification

The first direction-aware query exceeded the Worker's existing 500-character validation limit.
Regression testing caught the resulting incomplete research runs. Searches now bound signal,
problem and capability terms and retain that limit; a dedicated test covers the default direction.
The initial affected test run was stopped, the defect fixed, and the final full suite passed.

## Reproduce

```powershell
python -B -m pytest tests/p0 tests/p1 tests/p2 tests/p3 tests/p4 tests/research tests/qualification -q --tb=short -p no:cacheprovider -k 'not app_loads_and_reviews_synthetic_cards and not simplified_app_requires_keys_before_creating_run'
node --test test/rules.test.cjs
node --check dist/app.js
node --check dist/research.js
node --check dist/engine.js
python -B workspace.py check
```

Browser helper: tests/p2/browser_check.cjs with CGS_TEST_P3=1 and CGS_TEST_P4=1, explicit
CGS_TEST_PYTHON / CGS_TEST_PLAYWRIGHT / CGS_TEST_BROWSER paths, and a new CGS_TEST_OUTPUT directory.
Its isolated preview server explicitly selects synthetic test data and injected providers, then
closes the test server/browser. Existing Python 3.12.14 and bundled Node/installed Chrome were used.
The two excluded tests belong to the historical Streamlit UI, not the current integrated interface.

## Current workspace and limits

- workspace.py check reports P4 and orchestrator_implemented=true, configured=false.
- Actual workspace: zero research tasks/proposals from tests and seven original qualification samples.
- Original adjacent research baseline: 32/32 recorded files unchanged.
- Qualification baseline: only the three documented integrated frontend files and server.py differ;
  rule/assistant code and upstream source archives remain unchanged. No remote push occurred.
- Real Gemini/Tavily credentials are not configured. Live account connectivity, availability,
  research quality, feedback usefulness and commercial conversion remain unverified.
- This is structured persistent workflow adaptation, not model training, autonomous outreach,
  unrestricted multi-agent management or a completed CGS pilot. P5 remains unstarted.
