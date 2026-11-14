# CGS Lead Workspace redesign: design source

These files were exported on 13 Nov 2026 from the design canvas "CGS Lead Workspace Redesign" (claude.ai artifact, owner Aubrey). It is the version reviewed with our contact at CGS, with their six feedback points applied.

They are **reference material, not code to ship**. Each `*.dc.html` file is one screen in the canvas's prototype format:

- `{{name}}` holes are filled from `renderVals()` in the `<script type="text/x-dc">` block at the bottom.
- `<sc-if value=…>` and `<sc-for list=… as=…>` are conditionals and loops.
- `<dc-import name="X" …>` embeds another file (`X.dc.html`) with those attributes as props.
- `renderVals()` holds the sample data and the interaction logic: what each button does, what a toast says, and which state comes next.

The canvas runtime (`support.js`) is not included, so the files do not render when opened in a browser. Read them as source: the markup gives the layout, `app.css` and the inline styles give the look, and `renderVals()` gives the behaviour and the exact UI copy.

The sample companies and people (Northstar, Ostrander, Quinn Harper, Alex Morgan and the rest) are invented for the prototype. Real data must come from the API.

## Files

| File | Screen |
|---|---|
| `app.css` | Shared styles and colour meanings (see the comment at the top) |
| `Rail.dc.html` | Side navigation: collapsible, with search (Ctrl K) and an Inbox badge |
| `Activity.dc.html` | Activity button and drawer, bottom right of every page |
| `Main.dc.html` | Overview: outreach results, pipeline by stage, four columns (needs you, agents working, waiting on others, done this week) |
| `Inbox.dc.html` | Inbox: every decision that needs a person, with tabs, J/K/E keys, quick actions with Undo, and a link to each item's review screen |
| `Find.dc.html` | Find: ask the Discovery agent, scheduled searches, research direction, what searches taught |
| `Companies.dc.html` | One file that draws Companies, Outreach and Qualification, chosen by its `view` prop. See below |
| `Record.dc.html` | Company record: journey bar, Next step band (links to the review screen and makes no decisions itself), and tabs |
| `Agents.dc.html` | Agent hub: agents by stage with switches, manager agent, AI requests, workflows |
| `Library.dc.html` | Library: documents and passages agents may quote |
| `Connectors.dc.html` | Connectors: connected systems, and a permissions matrix of agent by tool |
| `Settings.dc.html` | Settings: people and second approval, scoring (admin only), email standard, outreach playbook |

## Companies.dc.html: props and stages

Props:

- `view`: `companies` (default), `outreach` or `qualification`.
- `stage`: the stage to open at.
- `open`: the company to preselect. It works for the brief review, the Outreach "Needs you" queue, and the table side panel.
- `issue`: `budget`, `gmail` or `research`, which shows a problem-state banner.

The stages under each view:

- **companies:** `found`, `research`, `decision`, `accepted`, `aside`.
  - `decision` is the full-page brief review: decision band on top, five folds of evidence below.
  - `accepted` is labelled Identified.
- **outreach:** `o-you`, `o-mate`, `o-agent`, `o-wait`.
  - These are Needs you, With a teammate, Agent drafting, and Waiting for a reply.
  - `o-you` is the review queue.
- **qualification:** `conv`, `qual`, `won`, `parked`.
  - These are Active, Awaiting approval, Qualified, and Parked.
  - `conv` is reply handling on one screen.
  - `qual` is the pack review.

## Boards on the canvas that are only wrappers

These canvas boards are a single `<dc-import name="Companies" …>` or `<dc-import name="Record" …>` with props. They are not copied here; the props below say which state each one opens at.

| Board | Opens |
|---|---|
| OutreachList | Outreach, Needs you |
| Outreach | Company record for Kessler (in outreach) |
| Outreach-approve | Outreach, Needs you, with Vesta selected (Alex's email to approve) |
| Outreach-talbot | Outreach, Needs you, with Talbot selected (Sam's email to approve) |
| Outreach-ended | Outreach, Needs you, with Whitcombe selected (plan ended with no reply) |
| Outreach-gmail | Outreach with the Gmail-disconnected banner |
| Qualification | Qualification, Awaiting approval (pack review) |
| Qualification-conv | Qualification, Active (reply handling) |
| Qualification-won / -parked | Qualification, Qualified / Parked |
| Companies-found / -research | Companies at Found / Researching |
| Companies-pellham | Companies, Your decision, with Pellham selected |
| Companies-budget / -failed | Companies with the AI-limit banner / the research-failed banner |
| Record-ardent / Record-northstar | Company record for Ardent (Active) / Northstar (Awaiting approval) |

## Product rules the design keeps

These come from the CGS contact's feedback and later decisions:

1. **Decision first.** Suggestion, reason, confidence and the three buttons go on top; evidence goes in folds below.
2. **Stage names.** The journey is Found → Researching → Your decision → Identified → Contacted → Active → Awaiting approval → Qualified. Identified means accepted with no email sent yet; Contacted means the first email has gone out.
3. **Exception wording.** The button reads "Ask Alex to approve with N conditions missing". It needs a one-line reason before it can be sent.
4. **No 0–100 score on lead screens.** Show "N of 6 conditions met" plus what is missing. Scoring weights and thresholds appear only in Settings, for admins.
5. **What changed.** A "What changed since last time" line sits at the top of every brief.
6. **No deal values on screen.** Show only that a revenue estimate exists or changed, with a link to Insightly.
7. **One place to decide.** Decisions happen on the review screens. The company record only links to them. The Inbox keeps quick actions for triage.

A few sample-data fields still hold old numbers that the design does not display: `score` in `Record.dc.html` and `Companies.dc.html`, and the "Priority 62" / "Score 80" chips that `Record` replaces when it draws. Do not bring them back.

## Open questions for CGS

- Are "Awaiting approval" and "Qualified" CGS's names for the last two stages?
- Does a request to approve with conditions missing need a second approver?
