# P1 verification (historical)

Date: 2026-10-22. Scope: bounded company research service and qualification HTTP integration.
P1 was completed and approved before P2. This record is historical; current checks are in P4.

- Python: 143 passed, 2 deselected in 12.30 seconds: P0 12, P1 31, research 62, qualification 38.
- JavaScript: 13 rule tests passed; app.js and engine.js syntax passed.
- Real localhost HTTP roundtrip: bootstrap/CSRF, background task, evidence results, idempotency;
  qualification cases remained unchanged.
- Service checks: concurrent/replayed requests, input conflict, one-active-task guard, stop/resume
  with saved sources and consumed budgets, missing configuration, redaction, lease recovery,
  dispatch failure and shutdown boundaries.
- Real LiveProviders adapter code ran through httpx.MockTransport for Gemini/Tavily response
  parsing and evidence binding. No real provider account call was made.
- API example validated against StartResearch; schema exported.
- Provenance at P1: original research 32/32 unchanged; qualification copied files 16/16 unchanged.
  Two local research adaptations were recorded: transaction-aware run creation and run-error redaction.
- Actual workspace: zero research tasks, seven synthetic qualification cases, both providers unconfigured.
- Two excluded tests belong to historical Streamlit UI. New integrated UI was not part of P1.

Reproduce the historical scope with:

```powershell
python -B -m pytest tests/p0 tests/p1 tests/research tests/qualification -q --tb=short -p no:cacheprovider -k 'not app_loads_and_reviews_synthetic_cards and not simplified_app_requires_keys_before_creating_run'
node --test test/rules.test.cjs
```

Runtime: existing Python 3.12.14. Temporary isolated databases were used. Local HTTP checks required
permission outside the restricted socket sandbox; final checks passed there. Real model access,
account quotas, public-web research quality, CRM integration and business conversion were unverified.
