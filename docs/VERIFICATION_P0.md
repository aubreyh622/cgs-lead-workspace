# P0 verification — 2026-10-22

Historical phase record. Current implementation and verification are documented in DEVELOPMENT_PLAN.md and VERIFICATION_P4.md.

## Result

P0 foundation is complete. Stop here and wait for user agreement before P1.

- Python: 3.12.14, using the existing local CGS research environment.
- New P0 tests: 12 passed.
- Inherited research non-UI tests: 62 passed.
- Combined pytest result: 74 passed, 2 deselected, 7.02 seconds.
- Inherited qualification business/provider-contract tests: 35 passed.
- Inherited JavaScript rule tests: 13 passed.
- dist/app.js syntax check: passed.
- workspace.py check: passed; both stores opened in one process, with distinct runtime database paths.
- workspace.py serve: started successfully on 127.0.0.1:8766; smoke-test process was then stopped.
- Original qualification blobs: all 16 match pinned upstream Git blob hashes.
- Original research files: all 32 copied source-file hashes still match the unchanged sibling project.
- Five workspace JSON Schemas exported from the implemented Pydantic models.

The executed suites total 122 passing offline tests. This is not a claim that all inherited
tests or a browser end-to-end workflow passed.

## Commands

Using a Python environment with requirements-dev.lock installed, from the workspace root:

    python -B -m pytest tests/p0 tests/research -q -p no:cacheprovider --tb=short -k "not app_loads_and_reviews_synthetic_cards and not simplified_app_requires_keys_before_creating_run"

The qualification checks were run with tests/qualification added to PYTHONPATH:

    python -B -m unittest test_backend.WorkspaceTests test_backend.ProviderContractTests -q
    node --test test/rules.test.cjs
    node --check dist/app.js
    python -B workspace.py check
    python -B workspace.py serve

Inside the Codex sandbox, TEMP/TMP and pytest basetemp were explicitly redirected to the
task's writable visualization directory after default temporary-directory permissions
failed during the earlier review. Test databases did not use existing business databases.

## Not verified / not implemented

- Two historical Streamlit UI tests were excluded after failing to complete in the earlier review environment.
- Three inherited loopback HTTP tests were not rerun here; the earlier review hit WinError 10013 socket restrictions.
- The startup smoke test confirms imports, initialization and binding, not browser interaction or HTTP contract coverage.
- No real Gemini, Tavily or OpenAI calls were made. Provider tests use controlled transports.
- No real-company quality or CGS stakeholder acceptance is claimed.
- Fresh-machine installation through Setup.cmd was not exercised; no package was installed in the borrowed environment.
- Research HTTP routes, opportunities/feedback persistence, orchestrator behavior, approved version application and qualification handoff remain P1–P4 work.
