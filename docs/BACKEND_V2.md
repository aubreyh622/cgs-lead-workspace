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
| 5 | P1: real evidence extraction and scoring proposals (evidence agent) and reply labels, through a switchable AI provider; code checks every proposal | Done; verified with real Gemini on 26 Oct |
| 6a | Evidence agent prompt v1.1 (conservative, complete); AI exam with 4 graded cases (`AI Exam.cmd`) | Done |
| 6b | Real outreach drafts, next actions, feedback agent, each with code checks; progress monitor `GET /api/alerts` (rules only) | Done |
| 6c | Real discovery (Step 1: Tavily search + AI nominations, code-checked) and research (Steps 2–3: Aubrey's research v2 pipeline in the background, collected by `POST /api/research/sync`) | Done; first live run pending (`Check Research.cmd`) |
| 6d | Evidence agent prompt v1.2 (engagement counts only completed actions; a condition is false only when stated); exam forbids those over-claims; K1 knowledge lookup with a synthetic CGS library | Done |
| 6e | Search plug and settings for CGS (`api/search.py`, `config/search.json`); synthetic internet; synthetic research; `Check Workflow.cmd` runs Steps 1–8 with real AI on the synthetic internet | Done; passed with real Gemini on Northwind, Ardent and Westford |
| 6f | Workflow check acts like a careful consultant: decline or blocker → pack to Step 8 and close (or nurture if deferred); defer or courtesy → nurture on the month the client named; engaged → internal conflict check, then submit only if Ready, otherwise stay Active with a task. Each simulated company has a story the result is compared with. Evidence agent prompt v1.4: data the client already sent supports E 3; N 4 needs the objective and the support scope in the notes. AI exam case 5 (Harrow Ridge) tests both | Done; live run pending |
| 6g | Autonomous agents (`api/autonomy.py`, `api/jobs.py`): task board, background worker, daily patrol, handoffs, inbox, brakes. Synthetic internet gains dated future news revealed by time travel; time travel works in live mode on the synthetic internet. Evidence agent prompt v1.5 ("not now" is PROJECT_DEFERRED, not NO_EXTERNAL_SUPPORT; approvers named only by role go to the research agent); AI exam case 6. `Check Agents.cmd` runs the story with the real AI | Done; live run pending |
| 7 | P3: Insightly and mail adapters, after CGS grants access | |
| 8 | Merge with the UI: the prototype calls the API; offline demo still works without a server | |

## Agent names

Each agent is named after its job. The blueprint's ids (A1-A4) are kept here only to map older documents.

| Agent | Job | Step | Code | Blueprint id |
|---|---|---|---|---|
| Discovery agent | Finds companies in recent news that match the research direction | 1 | `api/discovery_agent.py` | |
| Direction agent | Turns CGS's solutions into the problems discovery watches for (a proposal a person approves) | 1 | `api/direction_agent.py` | |
| Research agent | Everything looked up on the public web about one company: writes the brief for a picked company, finds the person in a role when another agent asks, and checks for news since the brief | 2-6 | `api/research_agent.py` | |
| Outreach agent | Writes first emails, follow-ups and reconnect drafts | 5 | `api/outreach_agent.py` | A1 |
| Reply classifier | Labels a client reply (meet, need, defer, decline ...) | 5-6 | `api/live_agents.py` | A2 (reply part) |
| Evidence agent | Reads conversation notes and proposes scores, conditions and blockers with quotes | 6 | `api/evidence_agent.py` | A2 + A3 |
| Next-step agent | Suggests up to three next actions for the open gaps | 7 | `api/next_step_agent.py` | A4 |
| Reminder rules | Not AI: reminds the owner when Ready, suggests nurture on the month the client named | 6-8 | `api/autonomy.py` | A4 (reminders) |
| Feedback agent | Reviews accept and reject reasons weekly and may propose a direction change | 1 | `api/live_agents.py` | |
| Manager agent | Looks over every open opportunity daily, hands work to the agents, writes notes to owners | all | `api/manager_agent.py` | |

Prompt versions follow the same names (`outreach-agent-1.0`, `evidence-agent-1.0`, `next-step-agent-1.0`, `research-agent-1.1`). Agent runs saved before the rename keep their old names in the history.

## Run it (Windows)

1. Run `Setup.cmd` once. It creates `.venv` and installs `requirements.lock`, which now includes FastAPI.
2. Run `Start Workspace v2.cmd`.
3. Open http://127.0.0.1:8800/ for the workspace and http://127.0.0.1:8800/docs for the interactive API page.

From a terminal: `python -m api.main`. Tests: `python -m pytest -q` (the old P0–P5 suites plus `tests/v2`).

## AI agents (step 5)

- `api/llm.py` is the plug between agents and AI companies. Single-step agents call `provider.json(...)`; tool-using agents call `provider.chat(...)`. `AI_PROVIDER` picks Gemini, OpenAI or Claude.
- `api/live_agents.py` holds the one-request evidence agent and the reply classifier. Before a person sees anything, code checks each proposal:
  - Every quote must appear word for word in the notes.
  - Keys and scores must be valid under the rubric.
  - An inference can never mark a condition as met.
  - At most one proposal per key, and 12 in total.
- Each call is logged in `agent_runs` with the model, prompt version and tokens. If a call fails, the note is still saved, the failed run is recorded, and the API returns the error. It never falls back to demo results.
- `Check AI.cmd` (`python -m api.check_ai`) checks the key and model. It then runs the reply classifier, evidence agent, next-step agent, outreach agent and feedback agent through the whole backend, on a throwaway copy of the demo data.
- `AI Exam.cmd` (`python -m api.ai_exam [models]`) grades the evidence agent on 6 synthetic notes with known answers (`api/seed/ai_exam.json`). It reports hits, misses, over-claims and wrong values, and can compare several models side by side.
- Code checks for the other agents:
  - Outreach agent: every cited fact must exist in the brief, and the draft may not make prices, fees, percentages, guarantees or promises.
  - Next-step agent: at most 3 actions, each tied to an open gap, with no owners or dates.
  - Feedback agent: whitelisted fields only, and at least 3 supporting records.

## Autonomous agents (step 6g)

Agents work like employees: they notice work, do it, and hand the result to a person. Three ways they find work:

| How | Example | Who acts |
|---|---|---|
| Event | A reply says "not now" | Reminder rules suggest nurture until the month the client named |
| Event | The approver returns a pack | Next-step agent suggests next actions, or passes on the approver's question |
| Event | The rules now say Ready | Reminder rules remind the owner to submit |
| Patrol (daily) | No reply 7 days after the email | Outreach agent writes a follow-up draft |
| Patrol (daily) | A nurture review date is reached | Research agent checks for news since the last brief |
| Patrol (daily) | No progress for 14 days | Next-step agent suggests next actions |
| Patrol (weekly) | — | Discovery agent runs a round; feedback agent reviews decisions |
| Handoff | Notes say "the CEO would decide" and the CEO is not a contact | Evidence agent asks the research agent to find the CEO |
| Handoff | The research agent finds news on a deferred client | It asks the outreach agent for a reconnect draft that cites the news |

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
  - The evidence agent may use a library passage only for the “CGS service fit” condition, quoted word for word.
  - The outreach agent may cite one passage in a draft, but must still cite at least one brief fact.
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
| Research agent, contact task | `api/research_agent.py` | Finds the person in a role ("the CEO would decide"). Its answer is checked by code: the page was read in this run, the quote is on it word for word, the quote names the person, the title is that role. A rejected answer goes back to the agent, which looks again |
| Evidence agent (step 2) | `api/evidence_agent.py` | Reads the new notes and up to five earlier ones, checks what is confirmed and which gaps remain, may search the CGS library for fit, and records each proposal with `propose`, checked at once (same rules as before). A quote may come from an earlier note; the proposal links to that note. Asks the research agent directly with `ask_research` |
| Outreach agent (step 2) | `api/outreach_agent.py` | First emails, follow-ups and reconnect messages. Reads the brief and what was already sent or said, may look for newer news, and can cite a new development only after reading the page and adding the sentence word for word with `add_fact`. `submit_draft` is checked: every fact cited, each cited phrase close to its source (75% of its words, every number), no prices, percentages, guarantees or promises |

- The result is still a suggestion in the owner's inbox; nobody becomes a contact until a person applies it.
- **Approval gate** (from the MVP): each inbox item records a fingerprint of what the agent prepared (`payloadHash`), the
  opportunity's stage at that moment, and an expiry date (14 days). Applying is refused when the item is not the version the
  person saw (send `payload_hash`), when the opportunity has moved to another stage, when it has expired, or, for a draft,
  when a fact it cites now says something else in the brief. Dismissing is always allowed. Applying a draft adds the facts
  it cites to the brief, so the citations resolve. The daily patrol first dismisses unread items past their expiry date, and
  a new manager note (like new next actions or reminders) replaces the unread one for the same opportunity.
- A `null` argument from the model counts as left out (`api/agent_runtime.py`), so no tool ever sees the text "None".
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
- One voice per opportunity per day. The next-step agent turns open gaps into next actions; the reminder rules send Ready
  reminders and nurture suggestions; the manager's notes are only for what neither covers (work waiting on the owner, a client
  message nobody acted on, an opportunity stuck so long it needs a decision). Code refuses a manager note for an opportunity
  the next-step agent is working on that day. When the manager asks for next actions that are already on the board, its reason
  is added to that job, so the next-step agent sees both reasons in one run.
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

## Next-step agent (multi-agent step 5)

`api/next_step_agent.py`, prompt version `next-step-agent-1.0`. Used in live mode by `POST /api/opportunities/{id}/suggestions` and by
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

## Research agent (multi-agent step 6)

`api/research_agent.py`, prompt version `research-agent-1.1`. One agent for everything looked up on the public web about a
company, with three tasks that share the same web tools, limits and checks:

| Task | When | Result |
|---|---|---|
| brief | Picking a candidate or adding an opportunity (Steps 2-3) | The Step 4 brief |
| contact | Another agent asks for a person named only by role (`person_lookup`) | A suggested contact in the owner's inbox |
| refresh | A nurture review date, or the manager asks (`research_refresh`) | News since the brief, and a reconnect draft for a deferred client |

The contact and refresh tasks run as the agents' own work: they check the daily AI budget before every AI request and stop when it
is used up (news recorded before the stop is kept). A brief that a person starts is not stopped by the agents' budget, like the
other buttons a person presses; its AI requests still count toward the day's total.

The refresh task records news with `add_news`: only events dated after the brief's latest fact, from pages that are not already
sources in the brief.

For the brief, `RESEARCH_ENGINE` in `.env` picks the engine:

| `RESEARCH_ENGINE` | Who researches |
|---|---|
| `agent` (default) | The tool-using research agent, with the AI from `AI_PROVIDER`, on the web that `config/search.json` names |
| `pipeline` | Aubrey's research pipeline on the real web (Gemini + Tavily), or one AI request on the synthetic web |

| Tool | What it does |
|---|---|
| `company_pages` | Pages from and about the company (its website, coverage) |
| `search_web` | A web search when something is missing |
| `read_page` | The full text of a page it found, or of a page on the company's own website |
| `add_fact` | One fact. The page must have been read in this run, the quote must be on it word for word, no repeated quote or fact. An event date the agent gives must appear on the page (search results on the real web often carry no date) |
| `set_contact` | The best contact, only with the sentence from a read page that names them |
| `record_size` | Employees and/or annual revenue, with the sentence that states them; code checks the numbers are in it |
| `finish` | Identity, hypothesis, fit, another explanation, open questions, confidence. Refused until there is a fact and a hypothesis |

- `read_page` fetches the whole page once (on the real web a search result is only a snippet).
- At most 8 searches, 10 page reads and 8 facts per company. On the synthetic web it sees only news published by the simulated date.
- When a reviewer returns the brief at Step 4 with a question, the next run is told to answer that question first.
- Company size: the research direction may set a range (`company_size`: `min_employees`, `max_employees`, `min_revenue_musd`,
  `max_revenue_musd`; empty = no limit). It changes only through a direction proposal that a person approves
  (`POST /api/direction/proposals` with `field: company_size`). The discovery agent skips companies clearly outside it. The
  research agent records the size it finds; a size outside the range flags the brief "Outside the direction", and a range
  with no size found flags a research gap. No range is set yet: CGS has to say what it is.
- The result has the same format as Aubrey's pipeline, so the brief, flags and recommendation are built the same way.
- In the server the agent runs in a background thread; `POST /api/research/sync` (or the worker, every 30 seconds) collects the
  brief. Every step is saved with the run (`GET /api/agent-runs/{id}`). A run lives in memory: after a restart, retry it.

## Problem-based research direction (direction agent)

The research direction can list **problems**: what a client would be struggling with, the public signals that suggest a
company has it now, and who usually owns it. They come from what CGS offers, not from a list of target companies.

1. CGS's solutions document goes in `knowledge/` as a `.md` file whose name contains "solutions"; each `## ` section is one
   solution and each paragraph a quotable passage. Until CGS provides one, `api/seed/cgs_solutions_demo.md` (SYNTHETIC, six
   invented solutions) stands in while `KNOWLEDGE_DEMO` is on.
2. `POST /api/direction/problems` runs the direction agent (`api/direction_agent.py`, `direction-agent-1.0`). It reads the
   library and records 2-6 problems with `add_problem`. Code checks each one: it quotes a passage the agent read, word for
   word; it has 2-5 signals of 2-8 words (events a news article could show); 1-4 buyer roles; it does not repeat another
   problem. The result is a direction proposal; nothing changes until a person approves it. Demo mode proposes three fixed
   problems from the demo document, without AI. Problems can also be edited by hand (`POST /api/direction/proposals`,
   `field: problems`).
3. Discovery works problem by problem: each nomination must name its `problem_id`, and the candidate shows it (`problemId`,
   `problem` in `GET /api/candidates`).
4. A picked candidate keeps the problem (`targetProblem` on the opportunity). The research agent treats it as a hypothesis:
   it checks whether the public evidence supports it and says so in the brief.

Profile criteria (company size today) filter the same direction; they do not make a separate mode. Under SoW section 9, a
real CGS solutions document may go to an external AI provider only if CGS approves; the demo document is synthetic.

## Shared toolkit, connector permissions and email (Gmail)

`api/toolkit/` is the one way agents reach outside systems (connectors). Every connector client an agent works with comes
through its gate (`web_for`, `library_for`, `mail_for`, `connector`, or `grant` for ready-made tools), so one permission
table governs every agent. `GET /api/toolkit` shows the connectors and the table.

| Connector | Data | Status |
|---|---|---|
| web (Tavily or the synthetic web) | public | in use |
| library (CGS solutions, services, cases, documents) | cgs | in use |
| mail (Gmail, or MockMail) | client | in use |
| drive (Google Drive) | cgs | not built |
| crm (Insightly) | client | not built |

Permissions have three layers, strongest first:

1. **The ceiling, in code** (`CEILING` in `api/toolkit/__init__.py`): what each agent may ever use. "on" = on by default,
   "allowed" = may be switched on, anything else is forbidden. Changing it is a code change, reviewed like code.

   | Agent | web | library | mail | drive | crm |
   |---|---|---|---|---|---|
   | Discovery agent | on | - | - | - | allowed |
   | Direction agent | - | on | - | allowed | - |
   | Research agent | on | - | - | - | allowed |
   | Outreach agent | on | on | allowed | - | - |
   | Evidence agent | - | on | allowed | - | - |
   | Next-step agent | - | on | - | - | - |
   | Manager agent, Feedback agent, Reply classifier | - | - | - | - | - |

2. **Switches people set in the workspace** (for a management screen; stored in `connector_switches`, every change kept in
   `permission_changes` and the activity log):

   | Endpoint | What it does |
   |---|---|
   | `GET /api/toolkit/permissions` | Every agent x connector: `state` (on, off, connector off, not built, forbidden), `editable`, `ceiling`, `source` (default, config file, switch) and, for a switch, `changedBy`, `changedOn`, `reason`. Also each connector's own on/off and whether the current user `canChange` |
   | `PUT /api/toolkit/permissions/{agent}/{connector}` | `{"enabled": true \| false \| null, "reason": "..."}`: on, off, or back to the default for one agent. A reason is required |
   | `PUT /api/toolkit/connectors/{connector}` | The same for every agent at once (a kill switch) |
   | `GET /api/toolkit/permissions/history` | Every change (switches and limits), newest first: `id`, `kind`, who, when, why, state before and after, `undoable` |
   | `POST /api/toolkit/permissions/history/{id}/undo` | `{"reason": "..."}`: put that setting back as it was before the change. Only the latest change to a setting can be undone; the undo is itself recorded |
   | `PUT /api/toolkit/limits/{connector}` | `{"perDay": n \| 0 \| null, "reason": "..."}`: a daily limit for the connector (all agents and people together); 0 = no limit; null = the config file's default |
   | `GET /api/toolkit/usage?days=7` | Calls per day, agent and connector (`ok`, `failed`, `denied`, `limit`), and each connector's limit and use today |

   What the ceiling forbids, or a connector not built yet, is refused (409). `CGS_ADMINS` in `.env` names who may change
   permissions (empty = any workspace user); others get 403. Switching email on for an agent warns that
   `CLIENT_DATA_TO_AI=allowed` is also needed. The switches apply at once, also to research running in its own thread.

3. **`config/toolkit.json`, the team's defaults**: `connectors` turns a connector off for every agent; `agents` sets exactly
   which connectors one agent has (an agent not listed keeps the ceiling's defaults). For example `"Research agent": []`
   takes the web away from the research agent. A connector the ceiling forbids cannot be given here either: the workspace
   refuses to start and says why. A switch set in the workspace overrides this file until it is reset to the default.

An agent built without a connector simply has no tools for it (the outreach agent without `web` has no web tools and no
`add_fact`). An agent that needs a connector it may not use stops with a clear answer: HTTP 403 `not_permitted`, or a
failed job whose error names the agent and the connector. Research is gated whichever engine runs it.

Other rules: agents only read. Sending an email, creating a Gmail draft or writing to a CRM is a connector method that runs
after a person clicks; it is never an agent tool. A tool that returns client data (an email thread) also needs
`CLIENT_DATA_TO_AI=allowed` (SoW section 9). An agent's own checks (word-for-word quotes, cited phrases) stay in the agent.
`tests/v2/test_toolkit_governance.py` fails if an agent module reaches a connector around the gate.

**Usage log and daily limits.** Every client the gate hands out is metered: each call (a web search or page read, a library
search, an email read or send) is recorded in `connector_uses` with the agent, the connector, what was called and the
outcome. A refused permission is recorded too, even though the work stops. Email a person sends from the workspace counts
as "Workspace (person)", the daily mail check as "Mail rules". A connector's daily limit (`limits` in
`config/toolkit.json`: web 150, mail 100; or set in the workspace) counts every caller together. Once today's calls reach
it, further calls are refused until tomorrow: inside an agent's tool the agent is told (a tool error) and may finish with
what it has; elsewhere the answer is HTTP 429 `limit_reached`. Research runs in its own thread: its calls are collected in
memory and saved with the research result, so the thread never waits on the database's write lock. Reading the library's
passage list (not a search) is not counted.

**Email** (`api/toolkit/mail.py`, `MAIL_PROVIDER`): `mock` (an in-memory mailbox; the default in demo mode), `gmail`
(live mode only), `off` (the default in live mode).

1. `GET /api/opportunities/{id}/outreach/email?to=...` shows exactly what would go out: the saved first draft, or a
   follow-up/reconnect draft from the inbox (`itemId=...`), with citation markers removed, and a `seenHash`. A draft that
   cites a SYNTHETIC library document is blocked.
2. `POST /api/opportunities/{id}/outreach/email` with `to`, `mode` (`draft` puts it in the person's Gmail drafts; `send` sends
   it now) and the `seenHash` the person saw. Anything else is refused. A follow-up goes into the same Gmail thread. The
   address is saved on the contact; agents never guess an address.
   For a follow-up or reconnect draft the agents put in the inbox, `POST /api/inbox/{id}` with `action: send`, the
   `payload_hash` the person saw and the `to` address approves it and sends it at once (approve and send). A changed draft,
   a missing address or a second send is refused and nothing goes out.
3. `mail_sync` (rules, no AI) runs with the daily patrol, or now with `POST /api/mail/sync`. A Gmail draft the person sent
   from Gmail is recorded as sent. A client reply goes to the owner's inbox (quoted history removed, no expiry); it is
   recorded, and the reply classifier runs, only when a person applies it, so an out-of-office message never makes a lead
   Active.

Gmail sign-in: enable the Gmail API in a Google Cloud project, create an OAuth client of type "Desktop app", put
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `%USERPROFILE%\.cgs-workspace\gmail.env` (outside the synced project
folder, because the token can read the whole mailbox; `GMAIL_CREDENTIALS_FILE` can point elsewhere), run `Gmail Sign In.cmd`
(`python -m api.toolkit.gmail_auth`; it opens Google's sign-in and saves `GMAIL_REFRESH_TOKEN` in that file without printing
it), check with `Check Mail.cmd` (`python -m api.check_mail`: a test draft to your own address), then set
`MAIL_PROVIDER=gmail` and `APP_MODE=live` in `.env` and restart. Scopes: `gmail.compose` (drafts and sending) and
`gmail.readonly` (threads). While the Google app is in "Testing", Google ends the sign-in after 7 days; sign in again.

