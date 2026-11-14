# Rebuild the workspace page to the new design

You are working in `C:\dev\cgs-workspace`. Read `AGENTS.md` first and follow it: talk to me in Chinese, write code, comments and UI copy in English, and work one phase at a time. When a phase passes its checks, report and stop until I say go.

## What this is

The workspace page (`frontend/`) has been redesigned and the client (our contact at CGS) has reviewed the design. Your job is to rebuild the page to match that design: its layout, interactions and wording. Keep the v2 API and the rules the page already keeps.

The design source is in `design/redesign/`. Read `design/redesign/README.md` first. It explains:

- the prototype format the screens are written in;
- which file is which screen;
- the product rules the design keeps;
- the open questions for CGS.

The `.dc.html` files do not render in a browser. Read them as source:

- the markup gives the layout;
- `app.css` and the inline styles give the look;
- `renderVals()` gives the behaviour and the exact UI copy.

The sample companies and people in them are invented. Never hard-code them. Everything on screen comes from the API.

## What to keep from the current code

`docs/FRONTEND.md` describes the page and its rules. All of these stay:

- **Code structure.** Plain ES modules with no build step. Each page module keeps its contract: `title`, `load`, `view`, `actions`, and optionally `after`, `instant`, `slots` and `changed`. Clicks go through `data-act` delegation.
- **Escaping.** Every value goes through the `html` template. Pages never set `innerHTML`, and links from the web go through `safeUrl`.
- **State in the URL.** Tabs and selections live in the URL, for example `#/companies?tab=review&id=…`.
- **Background loading.** Keep the instant draw and background refresh behaviour.
- **Data safety.** Keep `X-User`, the `revision` check with its 409 message, and the fingerprint check on Approve and send.
- **Undo.** Keep the Undo windows from `undo.js` (10 seconds), not the 6 seconds in the prototype. Keep the Send now button.
- **Background updates.** Keep the update polling (`updates.js`), the bell history and the agent dock.
- **Counts.** A number always equals what you see when you open it.
- **Options.** Choices the backend validates stay in `options.js`, and the test that keeps them equal to `api/services.py` stays.
- **Accessibility.** Keep everything in the Accessibility rule of `FRONTEND.md`.
- **Desktop only.** The page needs at least 1024px. Do not add a phone layout.
- **Backend.** Do not change the backend unless a phase says so and I agree.

## Where the design and the code differ

Each item below lists what the design wants and what to do. The ones marked **ask** need my decision in R0. Do not guess them.

1. **Look.**
   - The code follows `DESIGN.md`: warm neutrals, one orange accent, Inter at 400/500.
   - The design uses:
     - Geist on a cool grey canvas;
     - ink (#101828) for primary actions;
     - purple (#5B4FE0) for agent work;
     - orange (#B8430F / #D9622B) for "waiting on you";
     - green (#17663F) for done;
     - red only for real errors.
   - The design wins. Move its colours into the `:root` token block in `styles.css`, so there are still no colour literals outside it. Note the change in `docs/FRONTEND.md`.
2. **Navigation.**
   - The code has a top header with Home, Companies, Outreach, Qualification and Agents.
   - The design has a collapsible side rail (`Rail.dc.html`): Overview, Inbox, Find, Companies, Outreach, Qualification | Agent hub, Library, Connectors | Help, Settings.
   - Mapping: Home becomes Overview; `sources.js` becomes Find; `agents.js` and `team.js` become the Agent hub; `settings.js` becomes Settings.
   - Inbox, Library and Connectors are new.
   - Keep these from the current header, placed where the rail or page header has room: the person picker, the CRM sync status, the bell, and the agent dock. **Ask** where each one goes.
3. **Inbox.**
   - Today, Home lists everything that waits for the person (`GET /api/todo`, in groups).
   - The design splits this into a short "Needs you" column on Overview and a full Inbox page with tabs (All, Approvals, Company decisions, Agent proposals, Done) and quick actions.
   - Check that `/api/todo` and the existing write endpoints are enough. List anything missing.
4. **Stage labels.**
   - The backend stages are `researching, review, identified, contacted, active, approval, qualification, nurture, rejected, closed`.
   - Change only the labels in `STAGE_LABELS` and the pages, not the backend values:

     | Backend stage | Label |
     |---|---|
     | `researching` | Researching |
     | `review` | Your decision |
     | `identified` | Identified |
     | `contacted` | Contacted |
     | `active` | Active |
     | `approval` | Awaiting approval |
     | `qualification` | Qualified |

   - Nurture is "Parked" in the design. **Ask** whether to rename it.
   - Check `config/crm.json` so the Insightly line under each stage matches the real mapping.
5. **Scores.**
   - The backend has BANTHE scores, and the readiness rule includes a score threshold (Q 70). The current "To reach Ready" list shows it.
   - The design shows no 0–100 number on any lead screen, only "N of 6 conditions met" and what is missing. Weights and thresholds appear only in Settings > Scoring, for admins.
   - **Ask** how to word a gap that is only the score threshold. Never show the number itself on a lead screen.
6. **Revenue.** No revenue amounts on screen anywhere. Show "Kept in Insightly" or "Changed <date>", with a link.
7. **One place to decide.**
   - Decisions happen on the review screens: brief review (Companies), the Needs you queue (Outreach), reply handling and pack review (Qualification).
   - The Inbox keeps quick actions plus an "Open …" link to the review screen.
   - There is no separate company record page today: each stage has its own page (`links.js`). **Ask** whether to build the record (`Record.dc.html`) as a new page now or later.
8. **Exception.**
   - The button reads "Ask <approver> to approve with N conditions missing".
   - It lists what is missing and needs a one-line reason (140 characters at most) before Send is enabled.
   - Check what the backend's current exception path accepts and list any gap.
9. **Second approval for emails.** The design has a per-person setting (Settings > People) and a second-approver step in the Outreach queue. Check what the backend supports and list any gap.
10. **Data the design shows that the API may not have yet.** Do not invent values. If the data is missing, leave the element out and list it as a gap. Check each of these:
    - the "What changed since last time" line;
    - source phrases highlighted in drafts;
    - evidence proposals linked to the phrase in a reply;
    - email-standard checks;
    - the plan position ("Day 3 of 12");
    - reply rates on Overview;
    - "what searches taught" on Find;
    - agent permissions on Connectors;
    - Library passages.

## Phases

Stop after each phase and report in Chinese:
- what changed;
- what the checks said;
- anything you need from me.

**R0: survey and plan. No code changes.**
- Read `docs/FRONTEND.md`, `docs/BACKEND_V2.md` (the endpoints the pages use), `docs/API.md`, `frontend/app/*` and every file in `design/redesign/`.
- Write `docs/REDESIGN_PLAN.md`. It covers:
  - each current page and tab mapped to the design screen it becomes;
  - every element in the design, with the endpoint and field that feed it, or "gap";
  - your proposed answer to each **ask** item above;
  - the phase list below, adjusted to fit what you found.
- Then stop and wait for my decisions.

**R1: tokens and the frame around every page.**
- Font and tokens in `styles.css`.
- The side rail: collapse state kept in localStorage; the Inbox badge counts the same items as the Inbox page.
- Page headers, cards, chips, buttons, tabs, the segmented control, the stage bar, toasts, the agent dock and the activity drawer.
- Every page should still work with its current content, restyled.

**R2: Companies.**
- The stage bar with an Insightly line under each stage.
- Found: the table with bulk Request briefs / Set aside.
- Researching: progress shown as 4 steps.
- Your decision: the full-page brief review.
  - Queue on the left.
  - The "What changed" line.
  - The decision band: the agent's suggestion and reason, confidence, "N of 6 priority conditions met, missing …", and Accept / Send back / Reject with their forms opening inside the band. The suggested option is the primary button.
  - Five folds:
    - What we don't know: tickable, and open by default when the suggestion is Send back.
    - The case.
    - Sources and quotes.
    - Who to contact, and checks.
    - Priority conditions.
  - Open all / Fold all.
  - Keys J/K/A/S/R.
- Identified and Set aside: tables with a side panel.
- `open=<id>` in the URL preselects a brief.

**R3: Outreach.**
- Tabs: Needs you, With a teammate, Agent drafting, Waiting for a reply.
- Mine / Whole team beside the title.
- Needs you is the three-column review queue:
  - the draft, with click-to-edit for your own drafts, the word count and "Restore the agent's draft";
  - the email standard checks;
  - "Where this sits in the plan";
  - the actions for each kind of item;
  - Skip this step / Pause the plan.
- Nurture, as Parked or whatever we decide.

**R4: Qualification.**
- Active: reply handling on one screen.
  - What the reply means.
  - The six conditions, with Confirm / Not quite and a reason. Hovering a proposal highlights the phrase in the reply. "Ask about this in the reply" adds the question to the draft.
  - Call notes.
  - Your reply.
  - Send the pack for approval at 6 of 6, or the exception otherwise.
- Awaiting approval: pack review, with the "changed since it was sent" warning and no amounts.
- Qualified and Parked.

**R5: Overview and Inbox.**

**R6: Find, Agent hub, Library, Connectors, Settings.** Build only what the API supports. List the rest as gaps.

**R7: company record, if we decided to build it.**

**R8: problem states and wrap-up.**
- Problem states as banners: AI limit used up, research failed, Gmail disconnected, with the affected rows flagged.
- Then:
  - search the code for any 0–100 score, revenue amount or old stage label left on screen;
  - update `docs/FRONTEND.md`, `tests/v2/test_frontend.py` and `tools/a11y_audit.py` (new routes);
  - fix the end-to-end test if it relies on old labels or layout.

## Checks for every phase

- `python -m pytest tests/v2/test_frontend.py`
- `python -m pytest tests/e2e` when Playwright is installed. Say if it is skipped.
- Once the page has new routes: `python tools/a11y_audit.py http://127.0.0.1:8800 "Alex Morgan"` with the server running.
- Start the demo (`Start Workspace v2.cmd`, http://127.0.0.1:8800/). Click through what you changed, at 1024px and at 1440px wide, and compare it with the design source.
- Say clearly which checks ran, which were blocked by the environment, and which failed because of the code.
