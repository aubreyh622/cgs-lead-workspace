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

- `api/llm.py` is the plug between agents and AI companies. Single-step agents call `provider.json(...)`; tool-using agents call `provider.chat(...)`. `AI_PROVIDER` picks Gemini, OpenAI or Claude.
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
- **Worker**: started by `python -m api.main` when `AGENT_WORKER=on` (default). It patrols once a day after `patrol_hour` (or at once after time travel) and works through the board one job at a time, pausing between AI calls for the free tier. Each job is claimed in one database update, so the worker and a "run now" request never run the same job twice. While the day's AI budget is used up, AI jobs wait and jobs that need no AI still run. Temporary AI errors are retried up to 3 times, after 60 s and then 120 s.
- **Budget counting**: each AI request counts toward the daily budget (discovery makes two), including requests that failed.
- **Personal directions**: a consultant-scope proposal changes the proposer's own direction, whoever approves it. Versions are named `<shared>-<person>-p<n>` (for example `v4-alex-morgan-p2`). When a shared change is approved, each person's own changes are carried onto the new shared version, so shared exclusions always reach everyone.
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

- **Discovery agent** (`api/discovery_agent.py`, `POST /api/candidates/recommend`). In live mode this is the tool-using agent (see “Discovery agent” below). The older fixed pipeline, `discover()`, is kept for direct use: 4 Tavily searches built from the research direction, then one AI request nominates up to 10 companies. Either way, code keeps only nominations that:
  - point to a search result that was actually returned,
  - name a company that appears in that result,
  - use a person's name only when it appears word for word in that result; otherwise the contact is “contact to confirm”,
  - are not on an excluded domain and are not already in the workspace.
- **Research agent** (`api/research_bridge.py`). Picking a candidate starts Aubrey's research v2 for that company in the background. That pipeline is Gemini + Tavily only, uses her `config/problem_brief.json`, and caps each company at 10 searches, 12 page reads, 18 model calls, 22 credits and 8 minutes. The background worker checks running research every 30 seconds (or press `POST /api/research/sync`) and turns a finished company card into the Step 4 brief: quoted, dated facts with source URLs; flags; and a contact only if the sources name one. A run that produces no brief stays visible as failed or paused. Research needs the company website; set it with `PATCH /api/opportunities/{id}/company`.
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
| 8 | Approve, return, nurture, close | `POST /api/packs/{id}/decision` | An out-of-date pack cannot be approved; the role must fit the person (Lead owner = the owner; Another consultant = not the owner; Director of Operations = someone in `CGS_DIRECTORS`, or anyone while it is empty); approval sets Insightly sync to pending |
| 8 | Accept or decline a handover | `POST /api/packs/{id}/handover` | An approval that names a new owner waits until that person (X-User) accepts; if they approve themselves, they accept in one step. Declining needs a reason and leaves the pack open |
| Loop | Propose, analyze, approve or reject direction changes | `POST /api/direction/proposals`, `/analyze`, `/proposals/{id}/{approve,reject}` | Whitelisted fields only; a proposal made on an older version cannot be approved; personal versions apply to one person |

Agents in this step are demo text rules ported from the prototype (`api/demo_agents.py`). In live mode they record a failed run and return an error.

### Step 3: reads

Read endpoints: `GET /api/opportunities` (filter by stage or owner), `GET /api/opportunities/{id}`, `GET /api/companies/{id}/opportunities`, `GET /api/candidates`, `GET /api/direction`, `GET /api/feedback`, `GET /api/agent-runs`, `GET /api/stats`, and `POST /api/demo/reset` (demo mode only; needs X-User and waits for the agents' current job).

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

## Local safety

- The server answers only requests addressed to 127.0.0.1 or localhost, and refuses changes sent from another website's page (Origin check). Every change needs the X-User header, which other websites cannot add without the server's permission.
- Demo mode refuses a database file that holds live records. Live mode on the demo sample is allowed (the check scripts use it) and `GET /api/config` reports `demoData: true`.
- Keep the database outside OneDrive: set `CGS_V2_DB_PATH` to a local folder. The server prints a warning when the file is in OneDrive.

## Tool-using agents (multi-agent step 1)

Until now every agent was one AI call with a fixed form to fill, at a moment chosen by code. Step 1 adds agents that
reason in a loop and choose their own tools. The research agent's person lookup is the pilot (live mode only; demo mode
keeps the text rule).

| Piece | File | What it does |
|---|---|---|
| Tool calling | `api/llm.py` | `chat(system, messages, tools)` for Gemini, OpenAI and Claude, with one neutral history format |
| Agent loop | `api/agent_runtime.py` | model → tools → results → model … until `finish`. Brakes: step limit, tool-call limit, a budget check before every AI request. Every step is kept in a trace |
| Tools | `api/agent_tools.py` | `company_pages`, `search_web`, `read_page`, `known_contacts`. Nothing sends, approves or changes a score. `read_page` opens only pages the agent found, or the company's own site |
| Research agent (pilot) | `api/research_agent.py` | Finds the person in a role ("the CEO would decide"). Its answer is checked by code: the page was read in this run, the quote is on it word for word, the quote names the person, the title is that role. A rejected answer goes back to the agent, which looks again |
| A2 + A3 evidence agent (step 2) | `api/evidence_agent.py` | Reads the new notes and up to five earlier ones, checks what is confirmed and which gaps remain, may search the CGS library for fit, and records each proposal with `propose`, checked at once (same rules as before). A quote may come from an earlier note; the proposal links to that note. Asks the research agent directly with `ask_research` |
| A1 outreach agent (step 2) | `api/outreach_agent.py` | First emails, follow-ups and reconnect messages. Reads the brief and what was already sent or said, may look for newer news, and can cite a new development only after reading the page and adding the sentence word for word with `add_fact`. `submit_draft` is checked: every fact cited, each cited phrase close to its source (75% of its words, every number), no prices, percentages, guarantees or promises |

- The result is still a suggestion in the owner's inbox; nobody becomes a contact until a person applies it.
- **Approval gate** (from the MVP): each inbox item records a fingerprint of what the agent prepared (`payloadHash`), the
  opportunity's stage at that moment, and an expiry date (14 days). Applying is refused when the item is not the version the
  person saw (send `payload_hash`), when the opportunity has moved to another stage, when it has expired, or, for a draft,
  when a fact it cites now says something else in the brief. Dismissing is always allowed. Applying a draft adds the facts
  it cites to the brief, so the citations resolve.
- Demo mode keeps the text rules for all three agents. The single-step `extract_evidence` and `write_outreach` remain for
  `Check AI.cmd` and the AI exam.
- `GET /api/agent-runs/{id}` shows every step: what the model said, which tools it called with which arguments, and what they returned.
- Each AI request counts toward the daily budget; the loop stops when the budget is used up.
- Tests run behind an offline guard (`tests/offline_guard/`, from the MVP in cgs-lead-workspace): no internet, no real keys, no `.env`.

## Manager agent (multi-agent step 3)

`api/manager_agent.py`. Once a day the patrol queues a `manager_review` job (live mode, when `MANAGER_MODE` is not `off`).
The manager reads the board (every open opportunity: stage, progress, outreach, readiness, recent agent work), looks closer
at the ones that need it, and then delegates work (`delegate`) or tells an owner something only a person can act on
(`note_for_owner`). It sits beside the rules, it does not replace them: the rules keep the predictable work, the manager adds
judgment.

| Mode | What happens |
|---|---|
| `off` (default) | The manager does not run |
| `shadow` | It decides; every decision is recorded and compared with the rules' work that day. Nothing is queued, nothing reaches an inbox |
| `on` | Its delegations are queued (trigger `manager`), its notes go to the owners' inboxes (kind `advice`) |

- Code checks every delegation: the kind of work must fit the stage, a follow-up needs a sent email without a reply, a person
  lookup needs a job title not already a contact, Ready reminders need the rules to say Ready. At most 8 decisions per review
  and 2 per opportunity. Work already on the board, or already done today by the rules, is never queued twice.
- For a fair comparison, the manager does not see the work the rules queued that day.
- `GET /api/manager/decisions` lists every decision; `GET /api/manager/compare?day=` shows them next to the rules:
  both, manager only (flagged when it repeats recent work), rules only, notes to owners, and what the code refused.
- Switch modes with `MANAGER_MODE` in `.env` or `PATCH /api/agents {"manager_mode": "shadow"}`.
- `Check Agents.cmd` prints the comparison after each patrol when the manager is not off.

## Discovery agent (multi-agent step 4)

`discover_with_agent()` in `api/discovery_agent.py`, prompt version `discovery-agent-1.0`. Step 1 in live mode and the
weekly `discovery_round` job both use it. The agent picks its own searches instead of running a fixed list.

| Tool | What it does |
|---|---|
| `direction` | The research direction, the consultant's request, today's date, and any `custom_queries` from `config/search.json` |
| `search_news` | One news search (recency and news-only from `config/search.json`); returns titles, dates and 300-character previews. At most 6 per round |
| `read_page` | The full text of a result that a search returned, nothing else. At most 6 per round |
| `check_company` | Whether the workspace already has the company, or the direction excludes its website |
| `nominate` | One company. Checked at once; a rejection goes back to the agent with the reason |
| `finish` | Ends the round |

- `nominate` applies the same checks as before (source returned by a search, company named in it, not excluded, not known,
  not a duplicate, a person kept only when the source names them). It also needs the sentence from the source that shows the
  signal, copied word for word; it is saved as `Candidate.evidence_quote` and shown as `quote` (with `sourceUrl`) in `GET /api/candidates`.
- Each round is capped at 10 nominations and 16 model turns. The weekly job also stops at the agents' daily AI budget;
  what was checked before the stop is kept.
- Every step is saved with the run (`GET /api/agent-runs/{id}`), and the last round is written to `runtime/discovery-last-round.json`.

## A4 next-actions agent (multi-agent step 5)

`api/actions_agent.py`, prompt version `a4-agent-1.0`. Used in live mode by `POST /api/opportunities/{id}/suggestions` and by
the `gap_actions` job (a stalled opportunity, or a pack the approver returned). Demo mode keeps the fixed list in
`api/demo_agents.py`; the older one-request version (`live_agents.next_actions`) is no longer called.

| Tool | What it does |
|---|---|
| `situation` | Open gaps from the rules engine, open and done tasks, days without progress, why it was asked, and the approver's question when the latest pack was returned |
| `notes` | The latest five conversation notes |
| `known_contacts` | The people already on record at the company |
| `search_library` | The CGS library, for the service-fit gap |
| `suggest` | One next action for one gap. Checked at once; a rejection goes back with the reason |
| `finish` | Ends the run; refused until there is at least one suggestion, and until the approver's question is answered |

- Code checks every suggestion: the gap must be open (or `approver`), one per gap, at most three; a title and a question for
  the next conversation; no near-copy of an open task; no fees, percentages, discounts or guarantees; an optional quote must
  be in the given note word for word. When the approver returned the pack, the first suggestion must answer their question.
- No open gaps and no approver question: no AI request at all. A run that ends with nothing valid fails visibly (`ai_invalid_output`)
  and the open suggestions stay as they were.
- The `gap_actions` job passes its reason and the agents' daily AI budget. Every step is saved with the run (`GET /api/agent-runs/{id}`).

