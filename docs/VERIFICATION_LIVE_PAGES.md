# Live check of the workspace page

Date: 2026-11-05. The core workflow was clicked through the page, in live mode, with the real AI for the first time.
Until now the v2 pages had only been checked in demo mode.

**Setup**

- **AI:** Claude Haiku 5.5, real requests.
- **Web:** the synthetic web (invented companies, no search cost).
- **Mail:** the mock mailbox; nothing left the computer.
- **CRM:** off. No Insightly call was made.
- **Background worker:** off; agents ran only when the page asked.
- **Data:** a copy of the demo database (`runtime/live-check/live.sqlite3`). The demo database itself was not touched.

## What ran

| Step | On the page | Result |
| --- | --- | --- |
| 1 | Sources, Ask now, "How many" 3 | The discovery agent found exactly 3 companies, each with its news and a named buyer |
| 2-3 | Companies, Research on Ardent Castings | **First try failed** (see below). After the fix: a brief with 4 quoted, dated facts, an alternative explanation, 4 open questions, the CIO as contact; suggested accept |
| 4 | Accept with the duplicate check | Accepted; the first-draft job was queued |
| 5 | Agents, Run the queue; Outreach, Approve and send | The outreach agent wrote a first email citing 3 brief facts and one CGS library reference. The checks row was honest ("Contact not confirmed by a person yet"). It was sent to a test address through the mock mailbox |
| reply | Record the client's reply | The evidence agent labelled it "meet"; the lead moved to In conversation |
| 6 | Qualification, notes with analysis | The evidence agent proposed 9 items, each with the client's words; "Accept all" took all 9 in one click; score 50-55, more evidence needed |
| manager | Agents, Check now, Run the queue | 10 jobs. The manager agent's daily review covered 10 open opportunities and delegated 3 tasks. It triaged 4 opportunities, Ardent among them, and wrote one message per owner. The main automatic search found 7 companies. A follow-up draft was written |
| memory | Qualification, Ardent | "What the manager agent remembers" showed the summary and CGS's promise to send a proposal, with the client's sentence word for word |
| brake | | The daily limit of 60 AI requests ran out mid-queue; 3 jobs waited for the next day with that reason |
| Try it | Research agent, Try it on a company (Kessler Motion Controls) | A brief with 4 dated facts and a named contact, shown on the page. No Kessler lead was created. The run was logged as "Try it: Kessler Motion Controls" |

## Found and fixed

1. **Malformed tool arguments.** In the research agent's final step, Claude Haiku 5.5 wrote the "alternative" and
   "open questions" fields inside the "relevance" text, as `<parameter name=...>` tags. The code kept asking for an
   alternative and refused the brief 8 times, until the step limit.
   - Tools now go to Claude with `strict: true` (`api/llm.py`), so each argument must be a field of its own.
   - The agent loop also moves leaked `<parameter>` text back to the fields the schema names, when those are missing
     (`api/agent_runtime.repair_args`).
   - Test: `test_claude_tools_are_strict_and_leaked_arguments_are_moved_back`.
2. **Retry race.** A retry marked the task running while it still pointed to the old run. The page's research check
   looked at that old run, which was gone after a server restart, and marked the new attempt failed.
   - Now the old run's id is cleared before the new run starts (`api/services.run_research`).
   - Test: `test_a_retry_never_looks_at_the_old_run` (it fails without the fix).

## Cost

15 AI runs, 65 requests: 230,575 tokens in and 28,561 out. On Claude Haiku 5.5 that is about **$0.04**; the same on
Claude Sonnet 5.5 would be about $0.75.

## Not covered

- **The real web (Tavily).** It was checked on 30 Oct with the backend only.
- **Gmail and Insightly.** Both were off here.
- **The background worker.** Runs were started from the page.
- **The direction agent's "problems from the library" and the lessons review.** The daily limit was used up first.
- **Demo fixtures in live mode.** The two demo companies shown as "Researching" have no real run behind them, so in
  live mode they turn into "failed: no longer in memory". This is expected on demo data and does not happen with live
  data.
