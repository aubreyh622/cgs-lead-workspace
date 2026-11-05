# Backend phase B1 verification

Date: 2026-11-05. Scope: B1 in `DEVELOPMENT_PLAN.md`. Three of the design gaps listed in `VERIFICATION_F6.md` are closed.
Each was done in the backend and on the page together. All checks are offline. No real AI, mail or CRM call was made.

## Pause one agent

The workspace pause stops every agent. A person can now also pause a single agent: the research, outreach, manager,
discovery or direction agent.

**Backend**

- The paused agents are kept in the setting `paused_agents` (`api/jobs.py`, `PAUSABLE`).
- The task board skips their queued jobs. This applies to the background worker's lanes, to "Run the queue" and to a
  job run directly. A skipped job stays queued, and its result says the agent is paused.
- Other agents' jobs go ahead, and the daily check still queues work for the paused agent, so nothing is lost. When
  the agent is resumed, the waiting jobs run.
- New endpoint `POST /api/agents/pause` with `{agent, paused}`. It needs a person and records the change in the
  activity log.
- `GET /api/agents` now returns `pausedAgents`, and each agent entry has `paused` and `pausable`.
- Agents with no jobs on the board cannot be paused one at a time; the API returns 422. This covers the evidence
  agent, which runs when a person saves notes, and the reminder, mail and CRM rules.

**Page**

- Each agent's page has a pause switch in its header.
- The evidence agent's page explains why it has no switch.
- A paused agent's card and page say Paused, with how many jobs are waiting. The other cards keep their own state.

## Instructions for every agent

The design's "instructions" are the existing lessons. A person writes one, an admin approves it (only `CGS_ADMINS`
when that is set), and code refuses text that loosens a rule or mentions fees. An agent has at most five, and they
are added to its prompt after the rules. Until now only the evidence, outreach and manager agents took them.

**Backend**

- `api/lessons.py` `TARGETS` now lists all six AI agents.
- The approved lessons are added to these prompts:
  - **Research agent:** the brief (in the research thread, read through its own database session), the contact
    lookup and the news refresh.
  - **Discovery agent:** its round.
  - **Direction agent:** its review of the CGS library.
- The rule-based helpers still take none.
- The manager's weekly lessons review can now address a finding to any of the six agents.

**Page**

- The agent page's "Lessons" tab is now "Instructions" and is shown for all six agents.
- The tab lists each instruction with its status and has a form to write a new one. Approving, rejecting and retiring
  stay under Records, Lessons and in Suggestions from agents.
- The Records form now offers all six agents.

## Links to Insightly

`VERIFICATION_F6.md` listed these as a backend gap. That was wrong: the CRM sync already stores the Insightly lead and
opportunity addresses (`leadUrl`, `opportunityUrl`). The duplicate check at Step 4 also returns an address for each
record it finds. Only the page was missing.

- **Qualification header and Outreach contact block:** "Lead in Insightly" and "Opportunity in Insightly" links, which
  open in a new tab, plus "CRM sync failed" when the last sync failed.
- **Step 4 duplicate check:** now lists the records it found (up to eight), each linked to Insightly.
- **Link safety:** only http and https addresses become links. The test CRM's `mock://` addresses never do, so in
  demo mode no CRM link shows.

The address pattern (`https://crm.<pod>.insightly.com/details/<Kind>/<id>`, derived from `INSIGHTLY_API_URL`) was not
opened against the real Insightly account in this phase.

## Checks

| Check | Result |
| --- | --- |
| New: `test_one_paused_agent_waits_while_the_others_work` | The outreach agent is paused. The patrol still queues its follow-up. The queue runs other work only; a direct run of the paused job leaves it queued with the reason. After resume, every waiting job runs and the draft reaches the inbox. It also covers 401 without a person and 422 for the evidence agent |
| New: `test_research_discovery_and_direction_prompts_take_their_lessons` | An approved lesson for each of the three agents appears in that agent's prompt and in no other. Covered paths: the research thread, the recommend endpoint, the direction problems endpoint. Each use is counted |
| New: `test_one_paused_agent_shows_as_paused_and_crm_links_are_http_only` (Node) | The paused card text; the Insightly link is shown; a `mock://` and a `javascript:` address are not |
| Changed: `test_lesson_text_checks_limits_and_who_decides` | The refused agent is now "Mail rules", since the research agent takes lessons |
| `tools/a11y_audit.py` | 0 problems on 30 checks |
| Browser, dev server 8810 | Outreach paused from its page: the switch turned on, a notice appeared and its card said Paused while the other five stayed Idle; then resumed. Instruction L2 written for the research agent from its Instructions tab and shown as proposed |
| Full suite | See below |

**Dev database note.** The instruction L2 written during the browser check is still proposed in
`runtime/frontend-dev.sqlite3`. It is invented demo data.

## Full suite

Same command as F1, including the browser test: 542 passed, 0 failed (539 before B1, plus the three new tests).
