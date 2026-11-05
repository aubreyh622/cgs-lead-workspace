# Frontend phase F5 verification

Date: 2026-11-05. Scope: accessibility pass, end-to-end browser check of the whole workflow, documentation, and the
old launchers, as listed under "Frontend phases" in `DEVELOPMENT_PLAN.md`. Desktop only (the user ruled out a phone
or tablet layout on 2026-11-05).

## Accessibility

`tools/a11y_audit.py` (new, development tool) opens every page and tab of a running workspace (23 routes), then the
first form each one offers, and checks: every visible input, select, text area, button and link has an accessible
name; ids are unique; there is one h1; heading levels do not skip; text meets WCAG AA contrast against its actual
background (4.5:1, or 3:1 for large text). It also checks that focus moves to the main region after navigation, that
the skip button comes on screen when focused, and that the next Tab shows a focus ring.

Result against the dev server: 0 problems, run as Alex Morgan and again as Sarah Patel. Earlier phases had already
added names to repeated buttons (Review…, Change…) and the ARIA tab pattern with arrow keys, Home and End.

What the tool cannot judge, and was not tested with a screen reader: whether the reading order and wording make
sense when heard.

## End-to-end

`tests/e2e/test_workspace_page.py` (new) starts the v2 server on a fresh demo database (agents run only when asked,
mock mail, no CRM, `.env` ignored) and drives Chromium through the whole workflow with the page's own controls:

1. Choose Alex Morgan.
2. Research the first new candidate; the brief is ready.
3. Accept it at Step 4 with the duplicate check; it appears under Accepted.
4. Run the agents' queue; the outreach agent drafts the first email.
5. Approve and send the draft to a test address through the mock mailbox.
6. Record the client's reply; the lead moves to In conversation.
7. On Meridian, review Authority and the funding condition by hand; the rules say Ready; send the pack.
8. As Sarah Patel, approve the pack; Meridian appears under Qualified.

It also fails on any uncaught script error in the page. Google Fonts requests are blocked so the test stays offline.
It passed in 7.6 seconds. It is skipped when Playwright or its Chromium is missing (neither is in
`requirements.lock`).

## Documentation

- `docs/FRONTEND.md` (new): the pages and routes, the files, how a page module works, the rules the code keeps
  (escaping, X-User, revisions, approve-what-you-saw, nothing invented, design, accessibility), how to add a page,
  the three checks, and known limits.
- README: link to the guide; the checks now mention the browser test; the earlier app's launchers are named.

## Old launchers

The launchers of the earlier P0-P5 integrated app (`workspace.py`, `demo.py`, its own interface on ports
8766-8768) were renamed, not deleted, so that `Start Workspace v2.cmd` is clearly the main entry and the approved
P5 demo still works:

| Before | After |
| --- | --- |
| Start CGS Workspace.cmd | Legacy - Start P5 Workspace.cmd |
| Start Offline Demo.cmd | Legacy - Start P5 Offline Demo.cmd |
| Start Live Research Results.cmd | Legacy - Start P5 Live Research Results.cmd (its call to the workspace launcher updated) |
| Check Workspace.cmd | Legacy - Check P5 Workspace.cmd |

References were updated in `workspace.py` (a printed hint), `docs/DEMO_GUIDE.md` and `docs/OPERATIONS_HANDOFF.md`.
The historical P5 text in `DEVELOPMENT_PLAN.md` keeps the old name. `Start Workspace v2.cmd` now also opens the page
in the default browser a few seconds after the server starts.

The renamed launchers were not run in this phase.

## Full suite

Same command as F1, now including the browser test: 539 passed, 0 failed.
