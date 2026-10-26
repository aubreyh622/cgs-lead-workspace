# P2 verification (historical)

Date: 2026-10-22. Scope: research UI, sources, opportunity snapshots and structured human review.
P2 was completed and approved before P3.

- Python: 170 passed, 2 deselected in 20.29 seconds: P0 12, P1 31, P2 27, research 62, qualification 38.
- P2 covered multiple opportunities per company, frozen sources/snapshots, optional contacts,
  invalid/foreign references, active-task guard, injected fields, idempotency/concurrency,
  stale revisions, source-check requirement, Other explanation, null/invalid duration,
  feedback scopes, append-only history, restart reads and unchanged theme files.
- Real localhost HTTP covered new routes, CSRF/errors/static resources and unchanged qualification cases.
- Headless Chrome/Playwright covered creation, a committed save with a lost response and safe retry,
  review validation, refresh persistence, sources, script-string escaping, research stop/resume,
  qualification navigation, missing credentials and 390px mobile layout without overflow.
- No pageerror. Non-local browser requests were blocked. Screenshots were visually inspected.
- JavaScript: 13 rules passed; app.js, research.js and engine.js syntax passed.
- Actual workspace retained zero research test tasks/opportunities and seven original synthetic cases.
- Original adjacent research was unchanged. The three integrated frontend adaptations were documented.

Initial combined test collection found a duplicate P1/P2 test module filename; the P2 module was
renamed, then the full suite passed. Two excluded historical Streamlit tests do not cover the new UI.
Browser helper: tests/p2/browser_check.cjs with explicit runtime paths and a new temporary output
directory. It starts the isolated --synthetic-test-data preview server and closes test processes.

No real Gemini/Tavily call or research-quality validation occurred. No handoff, mail, CRM write,
orchestrator or automatic theme change was included in P2.
