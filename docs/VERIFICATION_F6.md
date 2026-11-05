# Frontend phase F6 verification

Date: 2026-11-05. Scope: design fidelity, as listed under "Frontend phases" in `DEVELOPMENT_PLAN.md`. The pages were
brought back to the Version 2 design wherever the backend already records the data. Items the backend cannot
supply yet are listed at the end. None of them are faked on the page. Desktop only.

## What changed

**Agents** (new `pages/team.js`)

- The landing view now has the six agent cards: research, outreach, evidence, manager, discovery and direction. Each
  card shows what the agent does, when it runs and what it gives a person. It also shows a state worked out from the
  task board: Paused, Working, Waiting for you, Queued or Idle, with a line saying what the agent is doing now.
- Above the cards is a control strip. It has the workspace pause switch, this month's AI usage against the limit and
  the daily check.
- Below the cards are suggestions from agents: research direction proposals, proposed lessons and inbox items no
  other page handles. Each one can be decided there.
- Each agent has a detail page with Overview, Work, Runs, Permissions and Lessons tabs. All of them are filtered to
  that agent.
- The earlier records view is still reachable as "Controls and CRM" and the other record tabs.

**Agent dock.** A pill at the bottom of every page shows how many tasks are running, queued or waiting for a person,
or that the agents are paused. It opens a panel listing each agent's state, with links to the agent pages.

**Qualification**

- The list is now a table with Lead, Evidence and Next columns. The Evidence column shows a six-segment condition bar
  with "n of 6 conditions". The Next column shows the next step, marked when overdue or ready.
- "Accept all" covers only evidence proposals that replace nothing a person confirmed. The others still need a
  reason each.
- "What the manager agent remembers" shows the manager's summary and open promises from either side, read from
  `/opportunities/{id}/memory`. A person can close a promise as done or no longer applying, with a reason.
- A person who is not the owner sees an approval card above the pack. It says who asks, when, the score range and
  priority, and the client's problem, and offers Approve or Send back with a comment. The full decision form stays
  below the pack for nurture, close, handover and dated questions.

**Companies**

- Every company waiting for a decision shows the research agent's suggestion in the list ("Suggested: accept",
  "look again", "not now"), taken from its brief.
- Running research shows its log as a checklist, with a "Working on the next step" line while it runs.

**Outreach**

- The draft now has a To line: the address from the email preview, or "Address needed".
- Below the draft is a checks row. It shows only what the page can verify: how many cited facts come from the
  research brief (or that there are no citations), whether a person confirmed the contact, and any block the backend
  reports.
- "Skip for now" opens the next item in the list without deciding the current one.

**Home and header**

- "Your leads by stage" became "Your pipeline": one bar per stage, scaled to the largest stage.
- The header shows the CRM setting from `/api/config` (for example "CRM: off" or "Insightly: writes after approval"),
  not a claim that the CRM is connected. It also shows the initials of the person acting.

## Checks

| Check | Result |
| --- | --- |
| `tests/v2/test_frontend.py` (module parse, logic, escaping, colour tokens, reason lists, counts) | 13 passed |
| `tests/e2e` (whole workflow in Chromium on a fresh demo database) | Passed in 6.6 s |
| `tools/a11y_audit.py`, now including the agent cards, the Controls tab and an agent's five detail tabs | 0 problems on 30 checks, as Alex Morgan and as Sarah Patel (before the new routes: 24 checks) |
| Full suite | See below |

The browser test now uses the new parts. In step 8, Sarah Patel approves through the approval card. The last check
looks for Meridian in the new Qualification table.

It was checked in the browser on the dev server (8810):

- the suggestion tags on all seven review companies;
- the To line, which says "Address needed" because demo contacts have no address;
- the checks row: "1 cited fact from the research brief" and "Contact not confirmed by a person yet";
- the pipeline bars, whose widths match the counts;
- the header CRM label and initials;
- the Qualification table;
- the agent cards, the agent detail tabs and the dock.

**Not checked in a browser:**

- **Memory block.** The demo data has no manager memory for any opportunity. The block draws only when
  `/memory` returns a summary or promises, and closing a promise calls an endpoint the backend tests already cover.
- **Skip for now.** The demo data has a single item in the Approve list, so the button correctly does not appear.

**Dev database note.** A mistyped link during the check recorded two reviews on Bluegate Polymers in the dev database
`runtime/frontend-dev.sqlite3`: Authority 4, and funding and decision access met. The data is invented demo data.
Delete the file to reset it.

## What the backend cannot supply yet

These appear in the Version 2 design but have no data or endpoint behind them, so the page does not show them:

| Design item | What is missing |
| --- | --- |
| Pause one agent | Only the whole workspace can be paused (`/agents/settings`) |
| Agent instructions and "Try it" | No per-agent instructions or test run endpoint |
| Discovery "how many" and automatic search management | The discovery agent takes a request, not a standing target |
| Watch for news on a company | No news monitoring |
| More than one contact per lead | One contact per opportunity |
| Email open tracking | Not recorded by the mail connector |
| Links to the Insightly record | The CRM view has ids but no record URL |
| Greeting and five stat tiles on Home | Left out on purpose, per the earlier design audit |

## Full suite

Same command as F1: 539 passed, 0 failed.
