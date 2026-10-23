# P3 verification (historical)

Date: 2026-10-22. Scope: explicit acceptance, unknown contacts, contact stages and deduplication.
P3 was completed and approved before P4.

- Python: 194 passed, 2 deselected in 24.45 seconds: P0 12, P1 31, P2 27, P3 24, research 62, qualification 38.
- Final P3 targeted check after cleanup: 24 passed in 4.08 seconds.
- JavaScript: 13 rules passed; app.js, research.js, engine.js and Python/JSON syntax checks passed.
- Pending submission created no case. Acceptance with unknown contact created a Not contacted
  case with owner/task/date, null BANTHE/conditions/revenue and no confirmed evidence.
- Tests covered latest review/version checks, required fields/dates, return/reject/resubmit,
  same-company distinct opportunities, duplicate/concurrent acceptance, restart persistence,
  transaction rollback after case insertion, frozen accepted sources and reset protection.
- Contact confirmation, draft guard, manual outreach and reply stages preserved qualification
  uncertainty. Received-case system dates fixed the demo-clock future-evidence conflict.
- Real localhost HTTP checked routes, CSRF and 400/404/409 behavior.
- Browser: full P2/P3 flow, lost acceptance response with safe retry, unknown-contact acceptance,
  contact confirmation, draft still Not contacted, recorded outreach Contacted, reply Active.
  Final received case had all BANTHE null and no confirmed evidence. Seven samples plus one case.
- No pageerror; desktop case screenshot visually inspected. Controlled providers and isolated
  databases only; non-local browser requests were blocked.
- Actual workspace retained zero test handoffs and seven original qualification samples.
- Original research folder and remote teammate repository remained unchanged. Integrated
  server.py adaptations were recorded; qualification rules and assistant code were unchanged.

Reproduce with the full suite through tests/p3. Browser helper: tests/p2/browser_check.cjs,
CGS_TEST_P3=1, explicit Python/Playwright/Chrome paths and a new output directory.
Two historical Streamlit tests were excluded. No real provider/account/quality validation,
mail/CRM integration, production identity control or P4 work was claimed.
