# Backend phase B2 verification

Date: 2026-11-05. Scope: B2 in `DEVELOPMENT_PLAN.md`, following the Version 2 design's Sources screen (Ask now with
"How many", Automatic searches) and the research agent's page ("Try it on a company"). Each part was done in the
backend and on the page together. All checks are offline. No real AI, web search, mail or CRM call was made.

## How many

- **Backend:** `POST /api/candidates/recommend` takes `count` from 1 to 10. The discovery agent is told the round's
  limit, and code refuses any nomination past it. Demo mode takes that many from the prepared pool.
- **Page:** Ask now has a "How many companies, at most" choice of 3, 5 or 10, with 10 preselected.

## Automatic searches

**Backend** (`api/discovery_searches.py`, table `discovery_searches`)

- The fixed weekly discovery round became the **main search**: every week, 10 companies per run, using the research
  direction alone. Its first run follows the older `last_discovery` date, so the schedule carries on where it was.
- People add their own searches. Each one has:
  - a name;
  - what to look for;
  - how often: every day, 3 days, week, 2 weeks or 4 weeks;
  - an optional weekday, for weekly or less often;
  - companies per run, from 1 to 10.
- A person's search runs with that person's own research direction, and the inbox item for its candidates goes to them.
- **Next run:** the last run plus the interval, moved forward to the chosen weekday. A search that has never run is due
  today, or on the next chosen weekday.
- **Daily check:** it queues one discovery job when any search is due. The job runs each due search as its own
  round. If one search fails, the others still run; the job fails only if all of them failed.
- **Edits:** a search can be changed or switched off. The main search can be switched off but not deleted.
- **Not built:**
  - The design's "At" time of day. Searches run at the daily check.
  - The design's proposed "Top up when the list runs low".

**Page:** the Sources "Find companies" tab has an Automatic searches card.

- Each search shows its name, what it looks for, how often, companies per run, its next run, its last run and how
  many companies it found and how many people picked or skipped.
- Each search has an Edit button and an on/off switch. The edit form for a person's search also offers Delete.
- "New automatic search" opens the same form.

**Note:** "Every month" in the design is "Every 4 weeks" here, because that is what the schedule actually does.

## Try it on a company

**Backend** (`api/trials.py`, table `trial_runs`)

- `POST /api/agents/research/try` runs the research agent on a company and website a person names. It uses the same
  agent, web tools, limits, checks, approved instructions and connector permissions as Steps 2-3.
- `GET /api/agents/research/try` lists that person's last 10 trials and collects any that have finished.
- **What is kept:** the brief, kept with the trial. The AI run is logged as "Try it: <company>" with its trace, and
  its web searches are metered.
- **Nothing else is written:** no company, candidate, opportunity, research task, inbox item, CRM record or email.
- Demo mode answers 409, because the research agent does not run there.

**Page**

- The research agent's page has a "Try it on a company" button next to its pause switch. It opens a form for the
  company and its website.
- Trials are listed with their state. A finished one shows its brief: the possible problem, the quoted facts with
  their sources, flags, a contact if a source named one, and what the agent would suggest.
- The page checks a running trial again every 8 seconds.
- Demo mode explains why the form is not offered.

## Checks

| Check | Result |
| --- | --- |
| New: `test_discovery_searches.py` (5 tests) | **Main search:** listed with week, 10 per run; the daily check queues it once and the round runs; not due again until 7 days later. **People's searches:** creating one checks the name, query, weekday and the person; editing clears the weekday; the main search keeps its name; switched off it is not queued; a person's search finds at most its per-run count and its inbox item goes to that person; the main search cannot be deleted, a person's can. **How many:** returns exactly 3; 11 is refused. **Weekday maths:** checked. **Limit:** with a limit of 1 the agent's second nomination is refused |
| New: `test_try_it_shows_a_brief_and_saves_nothing_to_the_workflow` | **Live mode, fake AI on the synthetic web.** A website is required. The brief comes back with facts. Companies, opportunities, candidates, inbox items and research tasks are unchanged. The AI run is logged as "Try it: Ardent Castings" with its trace. Each person sees only their own trials. Demo mode answers 409 |
| New: `test_try_it_shows_the_brief_with_safe_links` (Node) | A finished trial shows its brief and links its http sources. A `javascript:` source is not linked. A running trial says it checks again |
| Existing discovery and autonomy tests | Pass unchanged, including the weekly round that stops at the AI budget, which now runs through the main search |
| `tools/a11y_audit.py` | 0 problems on 30 checks; it opens the new automatic search form and the Try it panel |
| Browser, dev server 8810 (demo) | **Sources:** "How many" defaults to 10. A search "New Chief Digital Officers" (every 2 weeks, Wednesday, 3 per run) was added through the form and listed with "Next: 28 Oct". **Daily check:** after moving 2 days on, both searches ran as rounds 5 and 6 and found no new companies, because the demo pool is used up in that database; next runs 4 Nov and 11 Nov. **Research agent page:** "Try it on a company" shows the live-mode explanation |
| Full suite | See below |

**Not checked:**

- A real Try it run in the browser. It would call the real AI and web search; the offline test covers the backend
  with a fake AI.
- A live discovery round with a per-run limit. The limit is checked with the agent's own tools.

**Dev database note.** `runtime/frontend-dev.sqlite3` now holds the extra search, rounds 5 and 6, and a simulated
date two days later. It is invented demo data; delete the file to reset it.

## Full suite

Same command as F1, including the browser test: 549 passed, 0 failed (542 after B1, plus the seven new tests).
