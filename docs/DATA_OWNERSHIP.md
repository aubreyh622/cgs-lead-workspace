# Where CGS data lives: cache, records and working state

For the handover review with CGS. CGS's rule (2026-11-12): **Insightly is the source of truth for leads and
opportunities; CGS records live in Google Drive; the workspace database (Cloud SQL) holds only caches and the working
copy.** This page sorts every table into one of four kinds and says where the record copy is.

| Kind | Meaning | If the database were lost |
|---|---|---|
| **Cache** | a copy of data whose source is Insightly, Google Drive or the web | rebuilt from the source |
| **Record** | what people and agents wrote: drafts, emails, notes, evidence, decisions, agent notes | the copy in Drive remains (see below) |
| **Working state** | the machinery: jobs, runs, usage counters, candidates being researched | lost work in progress only; nothing CGS must keep |
| **Settings** | how CGS configured the workspace: people, scoring, instructions, permissions, with their history | re-entered, or restored from a database backup |

## Records → Google Drive

With `DRIVE_RECORDS=on` the workspace saves (api/drive_records.py) into one folder (`DRIVE_RECORDS_FOLDER_ID`, else a
folder "CGS Workspace Records" it makes):

- **one Google Doc per opportunity**, named `<company> - <title> [<workspace id>]`, rebuilt whenever it changes;
- **one Google Sheet "Workspace activity log"**: every line of the activity history, newest first (up to 5,000 lines).

It saves right after a person changes something, once a day, and on `POST /api/drive/records/save`. A record is written
only when its content changed. A failed save is kept on its row (`drive_records.error`) and tried again; the work in
the workspace never waits for it. The Doc is a copy the workspace writes: edits made in Drive are not read back.

| Table | One row is… | Where it goes in Drive |
|---|---|---|
| `research_briefs` | the research brief: facts with sources, hypothesis, recommendation | Doc: Research brief |
| `outreach` | the Step 5 draft, when it was sent, the client's reply, the LinkedIn note and message, the contact plan | Doc: Contact plan; Drafts and emails |
| `outbound_emails` | an email a person approved: exactly what was sent, who approved it, when it went | Doc: Drafts and emails |
| `inbox_items` (drafts) | an email draft an agent prepared, and what the person did with it | Doc: Drafts and emails ("Agent draft") |
| `notes` | meeting notes and emails in the original words, with the evidence agent's summary | Doc: Notes |
| `opportunity_memory` | the manager agent's notes: summary and the promises each side made | Doc: Agent notes |
| `proposals` | an AI-proposed score, condition or blocker, with its quote and the person's decision | Doc: Agent notes |
| `suggestions` | an AI-suggested next action | Doc: Agent notes |
| `evidence` | qualification evidence a person confirmed | Doc: Confirmed evidence |
| `tasks` | an agreed action with owner and date | Doc: Confirmed evidence (Agreed actions) |
| `packs` | a qualification pack submitted for approval (frozen scores) | Doc: Qualification packs |
| `decisions` | a person's decision at Step 4 or Step 8 | Doc: Decisions |
| `activity` | one line of history | Sheet: Workspace activity log |

Other kinds of inbox items (new facts, a suggested contact, reminders) are working state: once a person applies one,
its result is in the records above.

## Caches

| Table | Source of truth | Notes |
|---|---|---|
| `opportunities` | **Insightly**, from Step 4 on (lead, then opportunity at Qualification) | The workspace writes the lead to Insightly when a person accepts it at Step 4 and keeps it in line (api/crm_sync.py). The daily read-back takes what people changed in Insightly; a field a person kept as Insightly has it stays so. Before Step 4 an opportunity is a research candidate and exists only here (working state). |
| `companies`, `contacts` | **Insightly** (organization, contact) once the opportunity is in the CRM; the web before that | Contacts are written to Insightly at Qualification. |
| `crm_links` | Insightly | Which Insightly records belong to an opportunity, and what the last read-back saw. Rebuilt by matching the Workspace ID field. |
| `drive_documents` | Google Drive (the CGS library folders) | Copied by the daily library sync (api/drive_sync.py). |
| `drive_records` | Google Drive (the records folder) | Only where each record's Doc or Sheet is and the hash last saved. Lost: the next save makes new files. |
| runtime/embeddings-*.json (a file, not a table) | the public library passages | Search vectors; rebuilt on demand. |

## Working state

| Table | What it is |
|---|---|
| `candidates` | Step 1 recommendations not yet picked |
| `research_tasks` | progress of one research run |
| `discovery_searches` | the searches the discovery agent runs on its own |
| `trial_runs` | "Try it on a company": a brief shown once, nothing else saved |
| `agent_jobs` | the agents' task board |
| `agent_runs` | one AI call: agent, model, prompt version, success or failure (an audit log; Cloud Logging / Cloud Trace hold a copy when `OTEL_TO_CLOUD=on`) |
| `manager_decisions` | what the manager agent decided to do and why |
| `connector_uses` | one call to an outside system (for daily limits and the usage page) |
| `feedback` | why a person picked, skipped, accepted, returned or rejected something; feeds the learning loop. The decision itself is a record (`decisions`, `activity`). |
| `inbox_items` (other than drafts) | facts, contacts and reminders an agent prepared for a person |

## Settings (with their own history)

| Table | What it holds |
|---|---|
| `settings` | people and admins, per-person second approval, scoring criteria and their versions, email standard, outreach playbook, demo date, the records folder |
| `team_instructions` | what CGS staff tell the agents, every revision kept |
| `direction_versions`, `direction_proposals` | the approved research direction and proposed changes |
| `agent_lessons` | lessons a person approved for the agents |
| `connector_switches`, `permission_changes` | which agent may use which outside system, and every change to that |

These are small and change rarely. They stay in the database (backed up with Cloud SQL's automatic backups). If CGS
wants them in Drive too, the same records job can save a "Workspace settings" Doc.

## Questions for the handover review

1. Is one Doc per opportunity the right unit, or should notes and drafts be separate Docs (or rows in one Sheet)?
2. Which Drive folder (a shared drive is best on Cloud Run: the service account can then save there)?
3. Should the activity log Sheet keep everything, or a rolling window (today: the newest 5,000 lines)?
4. Are the settings tables fine as database-only, with Cloud SQL backups?
