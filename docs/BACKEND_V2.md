# Workspace v2 backend

The v2 backend joins three things into one product: the Step 1–8 prototype, Aubrey's research and feedback services, and Linda's qualification rules. Steps 3–8 below follow Linda's P0–P3 build order.

## Decisions so far

| # | Decision | Choice | Date |
|---|---|---|---|
| 1 | Route | Build on Aubrey's backend; add Linda's rules; follow Linda's P0–P3 order | 26 Oct 2026 |
| 2 | Framework | FastAPI, replacing the hand-written standard-library server for new endpoints | 26 Oct 2026 |

## Plan

| Step | Scope | Status |
|---|---|---|
| 1 | Choose the route | Done |
| 2 | FastAPI app, settings (APP_MODE, CRM_MODE), rules engine over HTTP, frontend served at `/` | Done |
| 3 | One database whose tables match the v2 data model | Next |
| 4 | P0: Step 4 and Step 8 decisions, stale packs, audit trail, all through the API, with tests | |
| 5 | Frontend calls the API; offline demo still works without a server | |
| 6 | P1: real evidence extraction and scoring proposals (A2/A3) with quote validation | |
| 7 | P2: real discovery and research (Steps 1–3), outreach drafts (A1), next actions (A4), stall monitor | |
| 8 | P3: Insightly and mail adapters, after CGS grants access | |

## Run it (Windows)

1. Run `Setup.cmd` once. It creates `.venv` and installs `requirements.lock`, which now includes FastAPI.
2. Run `Start Workspace v2.cmd`.
3. Open http://127.0.0.1:8800/ for the workspace and http://127.0.0.1:8800/docs for the interactive API page.

From a terminal: `python -m api.main`. Tests: `python -m pytest -q` (the old P0–P5 suites plus `tests/v2`).

## Endpoints in step 2

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Server is running |
| GET | `/api/config` | APP_MODE, CRM_MODE, rule version |
| GET | `/api/rules` | Weights, anchors, minimum scores, conditions, blocker reasons |
| POST | `/api/rules/calculate` | Q, R, P, readiness and gaps for one opportunity |
| POST | `/api/rules/delta` | How a change moves Q and P, for example B 3 → 4: Q +5, P +3 |

The rules engine is `backend/rules.py` (rule version 1.1-proposed), unchanged from Linda's prototype. Its tests reproduce her regression cases: Meridian, Cedar Bay, Atlas, Northstar and Corvane.

## Ground rules carried from both prototypes

- The AI proposes; a named person decides. Only the rules engine calculates scores.
- Unknown values stay pending (null), never zero.
- Live mode never falls back to demo results; it shows the error.
- Nothing is sent by email or written to Insightly without a recorded human approval.
- All data is synthetic until CGS approves real data use.
