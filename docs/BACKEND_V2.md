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
| 6 | AI caution | “When unsure, say less”: agents leave out what the notes do not clearly support; consultants add it. The AI exam counts over-claims as the serious error | 26 Oct 2026 |
| 7 | Web search | Tavily free plan (1,000 credits a month, about 40 company researches) for discovery and research | 26 Oct 2026 |
| 8 | Knowledge library (RAG) | Small K1 lookup for the “CGS service fit” condition and outreach drafts. Demo library is synthetic until CGS shares its service list and cases; swap by adding files to `knowledge/` | 26 Oct 2026 |
| 9 | Web for testing | Default search is a synthetic internet (`config/search.json` provider `mock`): invented companies, client replies and call notes, so the whole workflow can be tested with real AI and no real company. CGS connects its own search service through the search plug and sets its own queries (docs/SEARCH_INTEGRATION.md) | 26 Oct 2026 |
| 4 | Who is acting | No login yet. Each request names the person in the `X-User` header, chosen from `CGS_USERS`; every change records the name | 26 Oct 2026 |
| 10 | Autonomous agents | Agents find work themselves (events, a daily patrol, handoffs between agents) and work in the background. They only prepare: drafts, facts, contacts, suggestions and reminders go to a person's inbox; nothing is sent, scored or approved without a person | 26 Oct 2026 |
| 11 | Agent brakes | Handoffs between agents allowed, at most 3 per opportunity per day and chains of 2; a daily AI budget (60 calls by default); a pause switch | 26 Oct 2026 |
| 12 | Patrol and discovery | Patrol every morning (08:00) and on demand; the discovery agent runs a round of candidates once a week | 26 Oct 2026 |

## Plan

| Step | Scope | Status |
|---|---|---|
| 1 | Choose the route | Done |
| 2 | FastAPI app, settings (APP_MODE, CRM_MODE), rules engine over HTTP, frontend served at `/` | Done |
| 3 | One database whose tables (now 22) match the v2 data model; demo seed; read endpoints (see DATABASE.md) | Done |
| 4 | P0: every human action through the API (Step 4 and 8 decisions, evidence review, tasks, packs, feedback, direction approvals), with tests | Done |
| 5 | P1: real evidence extraction and scoring proposals (A2/A3) and reply labels, through a switchable AI provider; code checks every proposal | Done; verified with real Gemini on 26 Oct |
| 6a | A2/A3 prompt v1.1 (conservative, complete); AI exam with 4 graded cases (`AI Exam.cmd`) | Done |
| 6b | Real A1 outreach drafts, A4 next actions, feedback agent, each with code checks; progress monitor `GET /api/alerts` (rules only) | Done |
| 6c | Real discovery (Step 1: Tavily search + AI nominations, code-checked) and research (Steps 2–3: Aubrey's research v2 pipeline in the background, collected by `POST /api/research/sync`) | Done; first live run pending (`Check Research.cmd`) |
| 6d | A2/A3 prompt v1.2 (engagement counts only completed actions; a condition is false only when stated); exam forbids those over-claims; K1 knowledge lookup with a synthetic CGS library | Done |
| 6e | Search plug and settings for CGS (`api/search.py`, `config/search.json`); synthetic internet; synthetic research; `Check Workflow.cmd` runs Steps 1–8 with real AI on the synthetic internet | Done; passed with real Gemini on Northwind, Ardent and Westford |
| 6f | Workflow check acts like a careful consultant: decline or blocker → pack to Step 8 and close (or nurture if deferred); defer or courtesy → nurture on the month the client named; engaged → internal conflict check, then submit only if Ready, otherwise stay Active with a task. Each simulated company has a story the result is compared with. A2/A3 prompt v1.4: data the client already sent supports E 3; N 4 needs the objective and the support scope in the notes. AI exam case 5 (Harrow Ridge) tests both | Done; live run pending |
| 6g | Autonomous agents (`api/autonomy.py`, `api/jobs.py`): task board, background worker, daily patrol, handoffs, inbox, brakes. Synthetic internet gains dated future news revealed by time travel; time travel works in live mode on the synthetic internet. A2/A3 prompt v1.5 ("not now" is PROJECT_DEFERRED, not NO_EXTERNAL_SUPPORT; approvers named only by role go to the research agent); AI exam case 6. `Check Agents.cmd` runs the story with the real AI | Done; live run pending |
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
- `Check AI.cmd` (`python -m api.check_ai`) checks the key and model. It then runs A2, A3, A4, A1 and the feedback agent through the whole backend, on a throwaway copy of the demo data.
- `AI Exam.cmd` (`python -m api.ai_exam [models]`) grades the evidence agent on 6 synthetic notes with known answers (`api/seed/ai_exam.json`). It reports hits, misses, over-claims and wrong values, and can compare several models side by side.
- Code checks for the other agents:
  - A1: every cited fact must exist in the brief, and the draft may not make prices, fees, percentages, guarantees or promises.
  - A4: at most 3 actions, each tied to an open gap, with no owners or dates.
  - Feedback agent: whitelisted fields only, and at least 3 supporting records.

## Autonomous agents (step 6g)

Agents work like employees: they notice work, do it, and hand the result to a person. Three ways they find work:

| How | Example | Who acts |
|---|---|---|
| Event | A reply says "not now" | A4 suggests nurture until the month the client named |
| Event | The approver returns a pack | A4 suggests next actions, or passes on the approver's question |
| Event | The rules now say Ready | A4 reminds the owner to submit |
| Patrol (daily) | No reply 7 days after the email | A1 writes a follow-up draft |
| Patrol (daily) | A nurture review date is reached | Research agent checks for news since the last brief |
| Patrol (daily) | No progress for 14 days | A4 suggests next actions |
| Patrol (weekly) | — | Discovery agent runs a round; feedback agent reviews decisions |
| Handoff | Notes say "the CEO would decide" and the CEO is not a contact | A2 + A3 ask the research agent to find the CEO |
| Handoff | The research agent finds news on a deferred client | It asks A1 for a reconnect draft that cites the news |

- **Task board** (`agent_jobs`): every piece of work, with who does it, why, who asked, and the result.
- **Inbox** (`inbox_items`): what agents prepared for each owner (or the team). A person applies, finishes or dismisses each item. Applying is the only way anything changes: facts join the brief, a contact is added, the opportunity moves to nurture. Drafts are never sent by the workspace.
- **Worker**: started by `python -m api.main` when `AGENT_WORKER=on` (default). It patrols once a day after `patrol_hour` (or at once after time travel) and works through the board one job at a time, pausing between AI calls for the free tier. Temporary AI errors are retried up to 3 times.
- **Brakes**: daily AI budget (`AGENT_AI_DAILY_BUDGET`, also `PATCH /api/agents`); at most 3 handoffs per opportunity per day and chains of 2; pause switch. Every job and AI call is logged.
- **Time travel** (`POST /api/demo/advance-days`) works with demo data and, in live mode, on the synthetic internet. The synthetic internet hides news dated after the simulated day, so a research refresh after time travel finds it.
- `Check Agents.cmd` (`python -m api.check_agents`): three leads, then 8 days and 5 months of simulated time, with the real AI.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/agents` | The team, what each agent does, paused or not, today's AI calls and budget, queue size |
| PATCH | `/api/agents` | Pause or resume; change the AI budget or patrol hour |
| GET | `/api/agents/jobs` | The task board (filter by status or opportunity) |
| POST | `/api/agents/patrol` | Patrol now (`run=true` also works through the queue) |
| POST | `/api/agents/run` | Work through the queue now |
| GET | `/api/inbox` | Items prepared by agents (`owner=` shows that person's and the team's) |
| POST | `/api/inbox/{id}` | `apply`, `done` or `dismiss` |

## Discovery, research and the CGS library (step 6c–6d)

- **Discovery agent** (`api/discovery_agent.py`, `POST /api/candidates/recommend`). Runs 4 Tavily searches built from the research direction, then asks the AI to nominate up to 10 companies. Code then keeps only nominations that:
  - point to a search result that was actually returned,
  - name a company that appears in that result,
  - use a person's name only when it appears word for word in that result; otherwise the contact is “contact to confirm”,
  - are not on an excluded domain and are not already in the workspace.
- **Research agent** (`api/research_bridge.py`). Picking a candidate starts Aubrey's research v2 for that company in the background. That pipeline is Gemini + Tavily only, uses her `config/problem_brief.json`, and caps each company at 10 searches, 12 page reads, 18 model calls, 22 credits and 8 minutes. `POST /api/research/sync` turns a finished company card into the Step 4 brief: quoted, dated facts with source URLs; flags; and a contact only if the sources name one. A run that produces no brief stays visible as failed or paused. Research needs the company website; set it with `PATCH /api/opportunities/{id}/company`.
- **CGS library** (`api/knowledge.py`, `GET /api/knowledge?q=`). A keyword (BM25) search over `knowledge/*.md|*.txt` and, while `KNOWLEDGE_DEMO=on`, a synthetic demo library.
  - A3 may use a library passage only for the “CGS service fit” condition, quoted word for word.
  - A1 may cite one passage in a draft, but must still cite at least one brief fact.
- `Check Research.cmd` runs one discovery round and one company's research live, on a throwaway copy of the demo data.

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
