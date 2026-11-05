# Frontend phase F4 verification

Date: 2026-11-05. Scope: the Sources and Agents pages, as listed under "Frontend phases" in `DEVELOPMENT_PLAN.md`.
Desktop only.

## What F4 contains

- `frontend/app/pages/sources.js` (#/sources, opened from "Find more companies" on Companies; Companies stays
  highlighted in the navigation):
  - Find companies: ask the discovery agent, optionally in plain words; add a company by hand (company, website,
    contact, where it came from, notes), which starts research.
  - Research direction: the version in use with every field; proposed changes with what they add or remove, who
    proposed them and why, approve or reject; propose a change yourself (problem, signals, buyer roles, exclusions,
    excluded websites, company size; for the team or only your own searches); ask the direction agent to review
    feedback, or to propose problems from the CGS library; versions and decided proposals.
  - What searches taught: signals with how often they were found, picked, accepted, rejected or skipped; searches
    that led to accepted leads or to nothing; common skip reasons; the feedback records the direction agent reads.
- `frontend/app/pages/agents.js` (#/agents, navigation count = new inbox items other than drafts and replies):
  - Header: patrol now; run the queue now (useful when no background worker runs, as in the dev server).
  - Team: what each agent does and how much it did today; pause or resume all agents; AI calls per day, patrol hour
    and manager mode; CRM status with the opportunities waiting to sync, and Sync now when writes are on.
  - Work and inbox: inbox items the other pages do not handle (apply, done, dismiss); the task board with a status
    filter, the reason, who asked, and the result or error of each job.
  - AI runs: recent runs; a run shows model, prompt version, calls, tokens, status, error and each step of a
    tool-using agent (model turns, tool calls with arguments and results, finish accepted or refused).
  - Permissions: connectors with today's use against the daily limit; switch a connector for every agent; set a
    limit; the agent-by-connector table (code-forbidden cells cannot be changed); every change with its reason and
    Undo.
  - Lessons: waiting, approved (with days in use and times used), rejected and retired; propose, approve, reject,
    retire; what stands out in the scorecard; run the lessons review now (live mode only, otherwise the backend's
    message is shown).
  - Scorecard: how people received each kind of work, evidence agent calibration per score or condition (with the
    rules' names), and the manager agent's decisions. Small samples are marked.
- `frontend/app/forms.js`: shared helpers for these two pages (busy buttons, missing-field lines, tab keys, one write
  then redraw). `pages/later.js` (read-only placeholder pages) was removed; its link helpers moved to `pages/links.js`.

No backend change in F4.

## Checks run

- `tests/v2/test_frontend.py` now has 13 tests. New: the Agents page takes only new inbox items that are not drafts
  or replies (run with Node against the real module).
- Full offline suite (same command as F1): 538 passed, 0 failed.
- Manual run in the app's browser, demo database, agents off:
  - Sources: asked the discovery agent (round 4, 10 candidates, toast linking to them); added a company by hand
    (research started); approved the pending direction proposal (direction v4 in use); proposed a new signal (shown
    as a pending proposal); asked the direction agent to review feedback (it proposed nothing and said why); the
    What searches taught tab showed the signal table, skip reasons and feedback.
  - Agents: ran the queue (3 jobs); the task board showed results, including a skipped reminder; marked an inbox
    item done; switched the outreach agent's library access off and undid it, both recorded with reasons; proposed a
    lesson and approved it (it moved to "In the agents' prompts"); the lessons review in demo mode showed "needs a
    real AI model"; opened a run (demo text rules, no steps); paused and resumed the agents; the scorecard showed
    work, calibration and an empty decisions list.
- Fixed while testing: a notice from an earlier action stayed on screen after the next one; scorecard rows showed
  rule keys (A, commercial) instead of names.

## Not verified

- Tool-using agent traces with real steps (demo runs have none), the lessons review, direction problems from the
  library, and discovery on the real web: all need live mode.
- CRM Sync now (CRM off in the dev server), the connector switch and daily limit forms, rejecting or retiring a
  lesson, and the patrol button were built but not clicked.
- `CGS_ADMINS` restrictions on permissions and lessons (none set in the demo).
