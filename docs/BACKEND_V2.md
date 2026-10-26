# Workspace v2 backend

The v2 backend joins three things into one product: the Step 1–8 prototype, Aubrey's research and feedback services, and Linda's qualification rules. Steps 3–8 below follow Linda's P0–P3 build order.

## Decisions so far

| # | Decision | Choice | Date |
|---|---|---|---|
| 1 | Route | Build on Aubrey's backend; add Linda's rules; follow Linda's P0–P3 order | 26 Oct 2026 |
| 2 | Framework | FastAPI, replacing the hand-written standard-library server for new endpoints | 26 Oct 2026 |
| 3 | Database | SQLite through SQLAlchemy; switch to PostgreSQL later with one setting | 26 Oct 2026 |
| — | Order of work | Backend first, with all tests and agents working; merge with the UI last | 26 Oct 2026 |
| — | Who decides | Technical choices follow the recommendation and are recorded here; product choices go to Mikey | 26 Oct 2026 |
| 5 | AI provider | Gemini for now (free tier, synthetic data only). Switchable: change AI_PROVIDER and AI_MODEL in .env; OpenAI is already supported. Use a paid or CGS account before any real client data, because the free tier may use inputs to improve Google products | 26 Oct 2026 |
| 4 | Who is acting | No login yet. Each request names the person in the `X-User` header, chosen from `CGS_USERS`; every change records the name | 26 Oct 2026 |

## Plan

| Step | Scope | Status |
|---|---|---|
| 1 | Choose the route | Done |
| 2 | FastAPI app, settings (APP_MODE, CRM_MODE), rules engine over HTTP, frontend served at `/` | Done |
| 3 | One database whose 20 tables match the v2 data model; demo seed; read endpoints (see DATABASE.md) | Done |
| 4 | P0: every human action through the API (Step 4 and 8 decisions, evidence review, tasks, packs, feedback, direction approvals), with tests | Done |
| 5 | P1: real evidence extraction and scoring proposals (A2/A3) and reply labels, through a switchable AI provider; code checks every proposal | Done; first real run on Mikey's computer pending |
| 6 | P2: real discovery and research (Steps 1–3), outreach drafts (A1), next actions (A4), feedback agent, stall monitor | Next |
| 7 | P3: Insightly and mail adapters, after CGS grants access | |
| 8 | Merge with the UI: the prototype calls the API; offline demo still works without a server | |

## Run it (Windows)

1. Run `Setup.cmd` once. It creates `.venv` and installs `requirements.lock`, which now includes FastAPI.
2. Run `Start Workspace v2.cmd`.
3. Open http://127.0.0.1:8800/ for the workspace and http://127.0.0.1:8800/docs for the interactive API page.

From a terminal: `python -m api.main`. Tests: `python -m pytest -q` (the old P0–P5 suites plus `tests/v2`).

## AI agents (step 5)

- `api/llm.py` is the plug between agents and AI companies. Agents call `provider.json(...)`; `AI_PROVIDER` picks Gemini or OpenAI.
- `api/live_agents.py` holds the real A2 + A3 agent and the reply labeler. Before a person sees anything, code checks each proposal:
  - Every quote must appear word for word in the notes.
  - Keys and scores must be valid under the rubric.
  - An inference can never mark a condition as met.
  - At most one proposal per key, and 12 in total.
- Each call is logged in `agent_runs` with the model, prompt version and tokens. If a call fails, the note is still saved, the failed run is recorded, and the API returns the error. It never falls back to demo results.
- `Check AI.cmd` (`python -m api.check_ai`) sends two synthetic requests and prints what the AI proposed and what the checks kept. Run it after adding a key.

## Endpoints

### Step 4: actions (all need the `X-User` header)

| Step | What a person does | Endpoint | Rule the backend enforces |
|---|---|---|---|
| 1 | Pick or skip a candidate | `POST /api/candidates/{id}/pick`, `/skip` | Skip needs a reason and scope; “existing relationship” or “competitor” proposes an exclusion for approval |
| 1 | Add a lead by hand | `POST /api/opportunities` | Company is required |
| 2–3 | Stop, resume, retry research | `POST /api/opportunities/{id}/research/{stop,resume,retry}` | Live mode shows the failure; it never invents a brief |
| 4 | Accept, return or reject | `POST /api/opportunities/{id}/step4` | Accept needs the duplicate check; reject “Other” needs a comment; review time is optional and never counted as zero |
| 4 | Confirm a contact; save another opportunity at the same company | `POST …/contact`, `POST …/siblings` | Confirming needs where it was verified |
| 5 | Save the draft; record sent, follow-up, nurture | `PUT …/outreach/draft`, `POST …/outreach/sent`, `/followup`, `POST …/nurture` | The workspace never sends email itself |
| 6 | Record a reply | `POST …/outreach/reply` | Any reply makes the opportunity Active at once; the label comes after |
| 6 | Add notes and get suggestions | `POST …/notes` | The note is saved even if analysis fails; a client quote that is not in the notes is thrown away |
| 6 | Accept, edit or reject a suggestion | `POST /api/proposals/{id}` | Edit and reject need a reason; replacing a confirmed value needs a reason |
| 6–7 | Review a score, condition, revenue or blocker by hand; edit the need statement | `POST …/review`, `PATCH …/need` | Every change needs a reason and marks a submitted pack out of date |
| 7 | Get up to 3 next actions; turn one into a task; tick a task | `POST …/suggestions`, `POST /api/suggestions/{id}/task`, `POST /api/tasks/{id}/toggle` | A suggestion becomes a task only with an owner and date; finishing a task never changes a score |
| 7 | Submit or resubmit the pack | `POST …/packs` | Not Ready needs an exception reason |
| 8 | Approve, return, nurture, close | `POST /api/packs/{id}/decision` | An out-of-date pack cannot be approved; a handover needs the new owner's acceptance; approval sets Insightly sync to pending |
| Loop | Propose, analyze, approve or reject direction changes | `POST /api/direction/proposals`, `/analyze`, `/proposals/{id}/{approve,reject}` | Whitelisted fields only; a proposal made on an older version cannot be approved; personal versions apply to one person |

Agents in this step are demo text rules ported from the prototype (`api/demo_agents.py`). In live mode they record a failed run and return an error.

### Step 3: reads

Read endpoints: `GET /api/opportunities` (filter by stage or owner), `GET /api/opportunities/{id}`, `GET /api/companies/{id}/opportunities`, `GET /api/candidates`, `GET /api/direction`, `GET /api/feedback`, `GET /api/agent-runs`, `GET /api/stats`, and `POST /api/demo/reset` (demo mode only).

### Step 2

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
