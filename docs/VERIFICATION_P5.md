# P5 verification

Date: 2026-10-22 (America/Toronto; JSON evidence timestamps use UTC).
Scope: small two-round integration rehearsal, explicit offline demo, acceptance record,
demonstration instructions and operational handoff. P5 is complete for offline functional
validation and stops for user review. Real CGS client acceptance remains pending.

## Results

- **234 Python tests passed, 2 deselected in 37.99 seconds.**
  P0 12, P1 31, P2 27, P3 24, P4 35, P5 5, inherited research 62, qualification 38.
  The two deselected checks are historical Streamlit UI tests; the integrated UI was tested in Chrome.
- **13 JavaScript rule tests passed**; app.js, research.js and engine.js syntax checks passed.
- **27/27 two-round preparation checks passed**, using the actual localhost HTTP API, worker,
  evidence binding, databases and receiving/version logic with deterministic provider adapters.
- Full inherited **P2/P3/P4 Chrome workflow passed**, including missing-key errors, escaped
  input, lost-response retry, stop/resume, handoff/contact lifecycle and human direction approval.
- New **P5 Chrome workflow passed** through the real `demo.py` entry point, with no page errors
  and no horizontal overflow at 390px. Browser external traffic was blocked.
- Modified Python files parse. Maintained README, AGENTS, top-level engineering docs and new
  P5 code/tests/launcher are English; upstream source archives remain unchanged.

## P5-specific checks

The five added Python tests verify the full pilot plus directory preservation, incomplete
preparation refusal, explicit provider-factory guard, completed-session reuse and the no-change
feedback outcome. The pilot test inherits fake ambient credentials and blocks httpx and urllib
provider transports. It still completes without external calls or secret values in its report.
The demo does not load `.env`, exposes no inherited live readiness/model and cannot enable Live.

The real demo preparation produces:

- Two companies, two rounds, four completed synthetic worker tasks and four opportunity snapshots.
- Five append-only feedback records; four current reviews, including one unknown duration.
- Two accepted handoffs, one returned receipt and two received cases alongside seven inherited samples.
- One simulated feedback analysis and a human-approved direction version.
- A restart between rounds, with the approved direction reaching both search and model inputs.
- Preserved historical task versions, no duplicate case on retry and no public-research-to-score promotion.

Source quotes and dates pass the same production validator. All source domains are fictional
`.example` fixtures. They validate binding behavior, not external source availability or truth.

The additional browser test creates two more tasks and one more opportunity in a **different**
isolated session. It records an interactive human review, runs the simulated no-change analysis,
creates a manual pending proposal, approves it and verifies the next task uses that version.
The prepared user demo retains exactly its original four tasks/four opportunities.

Browser validation exposed a dropped refresh when a fast provider completed while another page
load was in flight. `research.js` now queues one follow-up refresh rather than discarding it.
Both the new demo and inherited browser flows pass with that correction.

## Reviewable evidence

- [Preparation report](p5/pilot-results.json): full checks, run IDs, sources, receipts, trace and snapshot.
- [Browser result](p5/browser-report.json): additional interactive browser checks.
- [Research screen](p5/offline-research.png), [approved manager screen](p5/offline-manager.png),
  [unknown-contact receiving case](p5/received-unknown-contact.png), [mobile screen](p5/offline-mobile.png).

The screenshots use the additional browser session. Its extra interactive changes explain
why its counts and generated version IDs differ from the four-task preparation report.
Preparation evidence is historical and is not silently rewritten after user edits.

## Isolation and provenance

`workspace.py check` reports phase P5, research tasks **0**, qualification cases **7**,
all real providers **not configured**, and the integrated workflow/demo flags available.
The user demo lives under `runtime/offline-demo/` with four tasks and nine total cases.
Neither default main database was populated with the rehearsal.

All **32** baseline files in the original sibling research project match the import manifest.
The integrated qualification baseline differences remain only app.js, index.html, styles.css
and server.py, documented across P2-P5. Integrated research changes remain storage.py,
worker.py and relocated test imports. Original upstream hashes are retained in the manifest.
No GitHub repository was changed. Local test servers and browsers were stopped after validation.

## Reproduction

Use the project's Python 3.12 environment and a fresh writable test directory:

```powershell
python -B -m pytest tests/p0 tests/p1 tests/p2 tests/p3 tests/p4 tests/p5 tests/research tests/qualification -q --tb=short -p no:cacheprovider -k "not app_loads_and_reviews_synthetic_cards and not simplified_app_requires_keys_before_creating_run"
node --test test/rules.test.cjs
node --check dist/app.js
node --check dist/research.js
node --check dist/engine.js
```

The actual sandbox run used an explicit fresh `--basetemp` and writable TEMP/TMP. Loopback
tests require local socket permission. Browser scripts require externally supplied test-only
`CGS_TEST_PYTHON`, `CGS_TEST_PLAYWRIGHT`, `CGS_TEST_BROWSER` and fresh `CGS_TEST_OUTPUT` paths.
Run `node tests/p5/demo_browser.cjs`; the inherited script is `tests/p2/browser_check.cjs` with
`CGS_TEST_P3=1` and `CGS_TEST_P4=1`. No runtime install or external browser download was needed.

## Not established by this verification

No real API account, actual prospect/contact relevance, model-quality improvement, time saving,
sales conversion, Insightly/mail integration, client sign-off, new-machine installation or cloud
deployment was verified. The orchestrator uses persisted feedback and human-approved direction
versions, not model-weight training. Functional acceptance and the real-pilot checklist keep
these remaining questions explicit.
