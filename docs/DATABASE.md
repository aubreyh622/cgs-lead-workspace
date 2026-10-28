# The workspace database, in plain language

The database is the workspace's ledger. It is one file, `runtime/workspace-v2.sqlite3`, made of 28 tables. Each table holds one kind of record, the way an Excel workbook has one sheet per topic.

Code reaches the file through SQLAlchemy, a translator layer. To move to a shared PostgreSQL database later (for several people at once), set `DATABASE_URL`. The code stays the same.

## The tables

| Table | One row is… | Workflow step | Who writes it |
|---|---|---|---|
| `companies` | a company | 1–3 | research agent, person |
| `contacts` | a person at a company; the name can be empty ("contact to confirm"); `email` only as a person entered it | 2, 4 | research agent, person confirms |
| `opportunities` | one possible piece of work at one company; a company can have several | all | everyone |
| `candidates` | a Step 1 recommendation not yet picked | 1 | discovery agent |
| `research_tasks` | progress of one research run (running, paused, failed, done) | 2–3 | research agent |
| `research_briefs` | the brief: dated facts, hypothesis, flags, priority | 3 | research agent; frozen at Step 4 |
| `decisions` | a human decision at Step 4 or Step 8 | 4, 8 | person only |
| `outreach` | the message draft and when it was sent and answered; with email on, the address used and the Gmail thread and waiting draft ids | 5 | Outreach agent drafts, person sends |
| `notes` | an email or meeting notes, in the original words | 6 | person |
| `proposals` | an AI suggestion (a score, condition or blocker) with its quote | 6 | Evidence agent |
| `evidence` | what a person confirmed, and how (accepted, edited, manual review) | 6–7 | person only |
| `suggestions` | an AI-suggested next action, not yet agreed | 7 | Next-step agent, or the manager's triage |
| `tasks` | an agreed action with an owner and a date | 6–7 | person only |
| `packs` | a frozen qualification pack sent for Step 8 approval | 7–8 | person |
| `feedback` | why someone skipped, accepted, returned or rejected something | 1, 4 | person |
| `direction_versions` | an approved research direction; never edited, only added to | learning loop | person approves |
| `direction_proposals` | a suggested change to the direction, waiting for approval | learning loop | AI or person proposes |
| `agent_runs` | one AI call: agent, model, prompt version, success or failure | all | system |
| `agent_jobs` | one piece of agent work on the task board: which agent, why (event, patrol or handoff), who asked, status and result | all | agents |
| `manager_decisions` | one decision of the manager agent (shadow mode too): what kind of work, for which opportunity, why, the outcome; `scope` says whether the daily review or a triage made it | all | Manager agent |
| `agent_lessons` | a lesson for one agent (L1, L2 ...): the text, the finding it rests on and its numbers, proposed, approved, rejected or retired (by whom, when, why), and how often it was in a prompt | learning loop | Manager agent or person proposes; person approves |
| `opportunity_memory` | the manager's notebook for one opportunity: a summary, and the promises each side made (who, what, due date, the quote and its note, open, done or dropped) | 6–8 | Manager agent; a person can close a promise |
| `inbox_items` | what an agent prepared for a person (draft, facts, contact, suggestion, reminder) and what the person did with it | all | agents, people |
| `activity` | one line of history for an opportunity | all | system |
| `settings` | workspace settings, such as the demo date | — | system |
| `connector_switches` | which connector a person switched on or off for an agent (`*` = every agent), with who, when and why | all | person (permission screen) |
| `permission_changes` | every change to the agents' connector permissions and daily limits, with the state before and after and the previous setting (for undo) | all | person (append-only) |
| `connector_uses` | one call to a connector: day, agent (or "Workspace (person)", "Mail rules"), connector, what was called, outcome (ok, failed, denied, limit) | all | the toolkit gate (append-only) |

## Rules the database follows

- **Pending is not zero.** An unknown score or condition is stored as empty (null). It never counts as 0.
- **History is never rewritten.** Decisions, evidence, feedback, direction versions and activity only get new rows; old rows stay.
- **The original words are kept.** Notes keep the client's text even if AI analysis fails.
- **Packs are snapshots.** A submitted pack stores a copy of the scores at that moment. If the opportunity changes afterwards, the pack is marked out of date and cannot be approved.
- **Every AI call is logged** in `agent_runs`, including failures.

## Demo data

In demo mode the empty database is filled from `api/seed/demo_seed.json`. That file was exported from the prototype, so the backend and the UI start from the same 14 opportunities, 10 candidates and 7 feedback records. A test checks that the Python rules and the prototype's JavaScript rules give identical results for all 14. In live mode the database starts empty.
