# The workspace page

The browser interface of the v2 workspace. It reads and writes the v2 API (`api/`) and shows only what the backend
records. Desktop only: there is no phone or tablet layout, and the page is at least 1024px wide.

Open it with `Start Workspace v2.cmd` (http://127.0.0.1:8800/). The `Legacy - …` launchers start the earlier P0-P5
integrated app, which has its own interface.

## Pages

The page is being rebuilt to the reviewed redesign (`design/redesign/`, plan in `docs/REDESIGN_PLAN.md`), one phase at a
time. R1 changed the look and the frame around every page, R2-R7 rebuilt the pages' own content, and R8 added the
problem banners and the final sweep. All eight phases are done.

| Route | Page | Steps | What a person does there |
| --- | --- | --- | --- |
| `#/inbox` | Inbox | All | Every row of `GET /api/todo` (All, Approvals, Company decisions, Agent proposals) with the selected item and its quick actions; Done. The rail's Inbox badge is `todo.count`, the rows under All |
| `#/overview` (old `#/home`) | Overview | All | Outreach results, the pipeline by stage (whole team), and four columns: Needs you, Agents working, Waiting on others, Done this week; workspace problems only when there is one |
| `#/companies` | Companies | 1-4 | Pick or set aside candidates, watch research, decide on each brief (`?tab=found|research|decision|identified|aside&id=…`) |
| `#/find` (old `#/sources`) | Find | 1 | Ask the discovery agent (and how many), automatic searches, add a company, set and approve the research direction, see what searches taught |
| `#/outreach` | Outreach | 5 and the first reply | Needs you, With a teammate, Agent drafting, Waiting for a reply: drafts, contact plans, sends, replies |
| `#/qualification` | Qualification | 6-8 | Active (one reply on one screen), Awaiting approval (one pack), Qualified, Parked |
| `#/agents` | Agent hub | All | The agents by stage with their state and switches, the manager agent, AI requests today, proposals waiting in the Inbox; each agent's own page (`?agent=research`) with its pause switch, instructions and, for the research agent, Try it on a company; the records: task board, AI runs, lessons, team instructions (edit the three sections, each save a revision; a save over someone's newer revision is refused and the text stays on the page; restore an earlier revision, refused while changes are unsaved), scorecard |
| `#/library` | Library | All | What the agents may quote: the library's size and search (`?q=`), and the documents copied from Google Drive with Sync from Drive |
| `#/connectors` | Connectors | All | Connected systems (state, data, today's use and limit, last use, which agents use it; CRM sync and read-back) and the agent permission table with its change history and undo |
| `#/record?id=…` | Company record | All | Everything about one company, read only: where it is in the journey (with the Insightly status of each stage), the next step with a link to the page where it is done, and the tabs Brief, Outreach, Conversation, Qualification, Activity (`&tab=brief|outreach|conv|qual|activity`). Not in the rail: Search, each side panel and the pack review open it |
| `#/settings` | Settings | All | People (role, sends from, open leads, second approval, Act as), scoring criteria (admins), email standard, outreach playbook |

Old route names (`#/home`, `#/sources`) still open their pages and are rewritten in the URL (`lib.js ROUTE_ALIASES`), so
links in notifications and bookmarks keep working.

**The frame.** A side rail on the left (`shell.js rail`): the brand, Search, the Work group (Overview, Inbox, Find,
Companies, Outreach, Qualification), the Agents group (Agent hub; Library and Connectors join when their pages exist),
and at the foot the Synthetic / Live data chip, the CRM sync line, Settings and the person acting. For an admin the person
block holds the "Acting as" list; while acting as someone else it has an orange ring. The rail collapses to icons with
the button at its top; the choice is kept in `localStorage` (`cgs-rail-collapsed`), and without a choice the rail starts
collapsed on windows narrower than 1280px. Collapsed, every link keeps its name (`aria-label`) and the Inbox badge
becomes a dot. Only the Inbox carries a count in the rail; the other pages show their counts on their own tabs.

**Search** (`search.js`): the rail's Search button or Ctrl K opens a dialog that searches the workspace's own
opportunities and new candidates (company, contact) and the pages, links a company in the workflow to its record
(`#/record`) and a new candidate to Found, moves with the arrow keys and closes with Escape. Records are read once a minute at most and again after
the person changes something.

Tabs and selections live in the URL (`#/companies?tab=decision&id=ostrander`), so a link opens the same view. When a page
opens without a tab, the tab it chooses is written into the URL, so a later redraw (a brief finishing, a click elsewhere)
never moves the person to another tab. Picking another item on
the page you are on shows it at once: Companies already holds every brief in Your decision and every row of its tables, and Outreach and
Qualification fetch the other items of the open list in the background (kept for a minute). The full load follows and
fills in what was still being checked (the Insightly duplicate check says "Checking…" until then, and is kept for five
minutes) and draws again only if something changed. After any change the person makes, the next view loads fresh. An
item not held yet keeps the page on screen, not clickable, until it is ready; there is no loading skeleton, and focus
stays on the item picked.

Every page also has, at the bottom right, the agents pill and the Activity button. The pill shows whether agents are
running, queued, idle or paused, and a small purple count of the items the agents prepared that wait for this person
(its accessible name says the whole sentence); it opens a panel with each agent's state. The count is the sum of that
panel's agent rows. Sticky decision bars stop 60px above the bottom so the pill never covers their buttons.

Beside it, the Activity button (it replaced the bell) opens the notification history (`app/updates.js`). The page asks `GET /api/updates` every 20
seconds while the tab is visible, and at once when the person comes back to it. What the agents and the workspace
finished in the background (an agent's draft or new candidates, a client reply, a brief that is ready or research that
stopped, an approved email that went out or did not) shows as a notice with its link, the navigation counts and the
dock are brought up to date, and the page on screen is drawn again unless the person is typing or has unsaved text in a
field. The history keeps those items and the notices of the person's own actions for a week; the bell counts the ones
not seen yet ("3 new" on the button), per person in this browser.

Counts follow one rule: a number equals what you see when you open it. The rail's Inbox badge equals the rows on the
Inbox page under All (`todo.count`), each page's tab counts equal their lists, and the Inbox lists everything that waits
for the person, agent suggestions included, each piece of work once (an agent's "Ready to submit" replaces the rules'
Ready row, a follow-up draft replaces "no reply yet", an overdue step replaces "follow up"). Overview's pipeline counts
the same whole-team lists its rows open, and each Overview column counts its cards. A client reply found in the email
thread waits on the lead it answers, in Outreach under Needs you, where the person records it. The rail shows the CRM sync state (`GET /api/crm/state`): synced and when, how many wait or
failed or need a decision, or read only or off; it links to the agent controls, where a failed sync can be retried
and the read-back run. A field someone changed in the CRM shows as a notice on the opportunity's Outreach and
Qualification pages ("Keep Insightly's" or "Use the workspace's") and as a To do on the owner's Home.

Undo (`app/undo.js`, `api/undo.py`): Approve and send waits 10 seconds before the email goes out. The toast offers Undo,
and the email's page shows when it goes out with Undo and Send now; a send that failed or did not go out says so there.
Accepting or rejecting at Step 4 can be undone from its toast for 10 seconds, before anything reaches the CRM or the
agents (returning to research starts at once, so it has no undo). Setting a candidate aside offers Undo in its toast,
and its row in Set aside keeps Bring back to Found.

**Companies** (R2, `design/redesign/Companies.dc.html`). The stage bar is the funnel and the tab control at once: Found,
Researching, Your decision, Identified, Set aside, each with its count and where the company stands in Insightly
(`config/crm.json`: nothing before Accept, Identified then Contacted after it, Disqualified for a rejected or closed lead
that reached it). Tab names from before the redesign (`new`, `review`, `accepted`) still open their stage, and `open=<id>`
works like `id=<id>`.

- Found, Researching, Identified and Set aside are tables. The company name opens a side panel (`id` in the URL) with
  what is known and the actions for that stage; Escape or the close button shuts it and puts focus back on the row, J
  and K move to the next and previous company. Found has a checkbox per row: Request briefs and Set aside work on the
  ticked ones (one reason for all; one Undo puts them all back). Researching shows the research run's own log lines and
  its state; the run does not report a fixed step number, so there is no "Step 3 of 4" (REDESIGN_PLAN G3). Identified
  lists the accepted leads before a reply (Identified and Contacted), with a link to Outreach. Set aside lists skipped
  candidates (Bring back to Found) and rejected or closed leads with the reason from their decision.
- Your decision is the full-page brief review: the queue on the left (J/K), then the decision card. Its band says what
  the Research agent suggests (or what the research checks say) and why, the confidence, the brief's priority cells in
  words, how many flags and unknowns there are, and Accept and plan contact (A), Send back with questions (S) and Reject
  (R); the suggested one is the primary button. Each form opens inside the band. Below it, five folds: What we don't know
  (the brief's unknowns and flags as questions to tick; they go with Send back, and the fold is open when the suggestion
  is to send back), The case, Sources and quotes, Who to contact and checks, Priority conditions. Open all / Fold all;
  folds and ticks change in place without reloading. The keys work wherever focus is on the page, except in a field.
- No revenue tier or amount is shown: the priority cells are fit, contact authority and relationship, in words. The
  brief has no versions, so there is no "What changed since last time" line (G1), and no "N of 6 priority conditions"
  count, since the brief does not mark conditions met or missing (G2).
- Facts that cite the same page sit together and name it once, with the quote when the brief has one; news over a year
  old is marked. Who to contact lists up to 3 people with their source and a LinkedIn search link. The accept form's
  duplicate confirmation follows the checks: when they ran and found nothing, the person confirms only what the checks
  cannot see. Reject asks whether it applies to just this company or to companies like this (the feedback scope).

### Outreach (R3)

Each lead before the first reply is in one stage, by whose move it is (`leadItems` in outreach.js, tested):
Needs you (a client reply to record, a second approval this person gives, a draft to review, a contact plan to decide,
a plan step that is due, or a first email with no draft yet), With a teammate (the owner or the second approver has
it), Agent drafting (the plan's next step is due later; the Outreach agent drafts it then), and Waiting for a reply.
Parked leads (backend `nurture`) are on Qualification since R4; a client reply to a parked lead still comes to Needs
you, where recording it makes the lead Active. Mine / Whole team switches between the person's own leads and everyone's
(`team=1`). Old tab names in links still open: `approve` → Needs you, `waiting` and `talking` → Waiting for a reply,
`nurture` and `parked` → Qualification, Parked; `item=<inbox id>` opens the lead that item belongs to.

- Needs you is a queue in three columns: the list (J/K), the message in the middle, and check-and-decide on the right.
  The middle shows the company, why it is worth writing to (from the brief), earlier plan steps (folded), the draft
  with its cited phrases highlighted and numbered against the brief's facts, the contact and the plan. The person's own
  draft opens for editing when its text is clicked; the editor counts words against the email standard's length. An
  edited agent draft says "Edited by you" and has Restore the agent's draft (the agent's text is kept on the item).
- The right column: the CGS email standard (the reviewer's score per line, the weighted mean "of 5" and the length;
  "Not reviewed" when the draft has no review), Where this sits in the plan (day N of M, and what happens with no
  reply), who approves after this person, and the actions. Skip this step and Stop the plan show while a plan runs.
- The other stages are tables (Company, Contact, Next step, When, Route, Plan, Status) with the same side panel as
  Companies. Nothing on these pages shows a 0–100 score or a revenue amount.
- Approve and send still re-reads the preview and stops if the text changed since it was drawn; the email waits 10
  seconds (Undo, Send now).

### Find, Agent hub, Library, Connectors, Settings (R6)

The rail's Agents group now has Agent hub, Library and Connectors; Settings stays in the rail footer, and the CRM line
there opens Connectors.

- **Find.** The ask box sits above the tabs (Searches, Research direction, What searches taught; the old `tab=find`
  opens Searches). Ask shows what will be asked, from where and how many, before anything runs (the backend has no dry
  run that reads the request, G17): Run once, Run and repeat every Monday (saved as an automatic search, then run now),
  or Change the wording. "Already have a company in mind?" opens Add a company. The header links to the candidates
  waiting in Found; the searches card says how many of today's AI requests are used. What searches taught adds the
  acceptance rate per signal.
- **Agent hub.** "N of 6 working" and an All agents switch; when paused, a banner to turn them back on. Four stage
  cards (Find companies: Discovery and Direction; Research; Reach out: Outreach; Qualify: Evidence), each with how
  people received that work from the scorecard (`stageMetric()` in team.js, tested) and each agent's state, what it is
  doing and its own switch (the Evidence agent has none: it runs when notes or a reply are saved). Below: the manager
  agent (mode, switch, daily check), AI requests today with Change the limit, and the agent proposals waiting in the
  Inbox (the Inbox's Agent proposals count). The design's Workflows tab has no backend (G18). The suggestions list moved
  to the Inbox. The records keep the task board, AI runs, lessons, team instructions and the scorecard;
  `agents?tab=permissions` opens Connectors.
- **Library.** The backend searches the library passage by passage (`GET /api/knowledge`, at most 20 results) and
  lists the Drive documents (`GET /api/drive`). The page shows the number of passages agents can quote, the Drive
  documents, how the library is searched, a search box, and Sync from Drive (off with its reason when Drive is not set
  up). There is no list of every document with passages and times quoted, nor a per-document switch (G19).
- **Connectors.** One card per connector (web search, CGS library, email, Google Drive, CRM) and the AI model: on or
  off for agents, the kind of data, today's use against the daily limit, the last use, which agents may use it, and
  Change (on, off or back to the default, and the daily limit, each with a reason). The CRM card has Sync now and Read
  back. Agent permissions is the table from the old Agents page, with a "Send or write: People only" column, and the
  change history with undo. The design's graded levels are on/off in the backend; the table shows the backend's states.
- **Settings.** People, Scoring, Email standard, Outreach playbook. The people table adds open leads (Identified to
  Awaiting approval and Parked) and Act as for an admin (the same switch as the rail's person picker).

### Stage cards

Companies, Outreach and Qualification open with the same row of stage cards (`.stages`, design `isFunnel`): the count,
who has the move (orange you, purple an agent, green done, grey someone else), and a line under the name. On Companies
and Qualification that line is what Insightly shows (`config/crm.json`); on Outreach it says who has the next move.
In Qualification a dimension's value is written in the rules' words (`GET /api/rules` anchors), never as a number.

### The side panel

`shell.js drawer` is drawn by Companies, Outreach and Qualification. `main.js draw` keeps it honest: everything behind
the panel is `inert` (no keyboard access to what the scrim covers), focus goes to the panel's heading when it opens
from a link or a bookmark (so Escape and J/K work at once), and a panel that was already open does not slide in again
when the page redraws. In the Inbox, E runs the main action only when it opens a page or offers Undo; an action that
cannot be undone (a direction change, a lesson) only gets the focus.

### Problem banners (R8)

What is wrong with the workspace itself shows as a banner under the page title, only on the pages whose work it stops
(`shell.js problems`, `PROBLEM_PAGES`). The data is the last `GET /api/todo` (read with the Inbox count), so a banner and
the Inbox always agree; a healthy workspace shows nothing. The banners are redrawn only when their text changes.

| Problem | From | Shows on | Says, and offers |
| --- | --- | --- | --- |
| AI limit used up | `health` key `budget` (the backend never raises it in demo mode), with today's requests and limit from `GET /api/agents` | Companies, Find, Outreach, Qualification | What waits until tomorrow and what still works; Raise today's limit (Agent hub), Not now (hidden for the rest of the day in this browser) |
| Gmail signed out | `health` key `gmail` | Outreach, Qualification, Company record | Emails cannot go out and replies are not read; how an admin signs in again; Open Connectors |
| Research stopped | the Inbox's "Research stopped" rows whose reason is not a pause | Companies | The company and why (several: their names); Open the company in Researching |

Affected rows say so where they are listed: Researching shows a stopped or paused research in its Now and Status
cells and side panel (since R2); with Gmail signed out, Outreach rows whose next move is an email read "Can't send, Gmail
off" and rows waiting for a reply read "Replies not read". Paused agents and failed agent jobs stay where they were:
Overview and Inbox list every `health` line, and the Agent hub has its paused banner.

### Company record (R7)

`#/record?id=…` (`pages/record.js`, decision D4) shows one opportunity from `GET /api/opportunities/{id}` and `GET
/api/rules`. It decides nothing: its only actions switch tabs, and every next step is a link to the stage's own page.

- **Header.** Breadcrumb back to the stage's list, company, industry and place, website, stage, the contact plan's
  route, owner, and the Insightly links when the CRM sync made records.
- **Journey.** Researching to Qualified with what Insightly shows at each stage; the date each was reached when a
  record says so (brief written, Accept, sent, reply, pack sent, approval). Parked and set-aside companies show where
  they left.
- **Next step.** What the stage waits for and who has the move (orange when it is a person's in this workspace), with
  its due date and a button to the page where it is done. Qualified and set-aside companies show a settled line.
- **Tabs.** It opens on the tab for the stage (Brief before Accept, Outreach while contacting, Qualification after a
  reply). Brief: the case, evidence with quotes and sources, the decision (the Research agent's suggestion in the
  Companies wording, who decided), contact and priority in words. Outreach: the contact plan's steps and the email
  (citation markers removed). Conversation: replies and call notes with the Evidence agent's reading. Qualification:
  the six conditions with their evidence and the D3 wording, and the open pack (whether a revenue estimate is
  recorded, never the amount). Activity: every change; lines that name an amount show "[amount]".

### Overview and Inbox (R5)

**Overview.** The subtitle says the date, how many things need the person (`todo.count`) and how many agents are
working (running jobs and research runs, as the dock counts them). Outreach results shows what the API counts: reply
rates per contact-plan approach from `GET /api/manager/scorecard` (replied of the plans that ended), and what the
replies in Active said (the Evidence agent's labels), linking to that list. There is no count of single emails and
replies per four weeks (REDESIGN_PLAN G4), so the page shows none. The pipeline counts, for the whole team, Found,
Researching, Your decision, Identified and Contacted, Active, Awaiting approval, Qualified and Parked, each linking to
the list with that count (`pipeline()` in home.js, tested). Below, four columns: Needs you (the first two Inbox rows and
"N more in Inbox"), Agents working, Waiting on others (the person's leads where a second approver or the client has the
move, from `leadItems` in outreach.js) and Done this week (leads qualified and client replies in the last seven days;
there is no list of everything settled, G5).

**Inbox.** All lists every to-do row; Approvals (Approve email, Approve or return, Answer the approver), Company
decisions (Accept or reject, Decide the contact plan, Decide: nurture?) and Agent proposals (the steer and check groups)
filter it; problems, replies and due steps are under All only. The selected row (`sel` in the URL) shows what it is
about and its actions; J/K move, E runs the main quick action. Quick actions, where the API allows them:

- Approve email (second approval): Approve and send (10 s Undo) and Return with a note (`POST /api/outbound/{id}/second-approval`).
- Direction change: Apply, Dismiss. Lesson: Approve lesson, Reject with a reason (admins only).
- Agent items: Done / Not useful; failed emails: Checked.
- Everything else opens the page where it is decided (the pack, the brief, the plan, the candidates).

The email, direction proposal and lesson ids come from the row key that `api/todo.py` builds; a test checks the
backend still builds them. Done lists the agent items the person settled (`GET /api/inbox?status=all`) and what was
settled in this browser; approvals and decisions are recorded on each company's page (G6).

### Qualification (R4)

Stages: Active, Awaiting approval, Qualified and Parked (backend `active`, `approval`, `qualification`, `nurture`),
with Mine / Whole team (Awaiting approval always shows the whole team: approvers are not owners). Old links still open:
`tab=qualification` → Qualified, `tab=nurture` → Parked, `tab=closed` → Companies, Set aside (D2). A link to someone
else's lead shows the whole team so the lead is in the list.

- **Active** handles one reply on one screen, in three columns: Conversations (J/K); the middle with the company, the
  line "N of 6 conditions met. Missing: …", the latest reply (or the newest call note), Add call notes (from text or a
  .vtt/.srt transcript), what the Evidence agent did, earlier in the conversation, the next step, and folded sections
  (dimensions, conditions and revenue by hand; client need; tasks; the manager agent's memory; history); and on the
  right: 1. what the reply means (the agent's label, read-only: there is no endpoint to change it, G16; Not now parks
  the lead with a date, Not interested sets it aside), 2. the six conditions (Confirmed, Your call, Not met, Missing),
  3. the reply and the pack. Below 1280px the right column moves under the middle one.
- A condition the Evidence agent proposed shows its quote and Confirm / Not quite (Too weak, Wrong person, Out of date:
  the reason goes to the agent). Pointing at a proposal highlights its phrase in the reply (an exact match of the
  quote). Missing conditions have "Ask about this in the reply", which adds a question to the reply, and Confirm by hand.
- What stands between the lead and Ready is said in words (`readiness()` in qualification.js, tested; decision D3):
  missing conditions, "Budget: not confirmed yet" or "not strong enough yet" for a dimension, and when only the strength
  of the evidence is short, "The evidence is not strong enough yet for approval. Confirm more about …". No 0-100 score.
- 3. Your reply: the workspace sends email only before the first reply (the preview and send endpoints are for Step 5),
  so the reply is written here and copied into the person's own email (G21). The pack: "All six confirmed. Send the
  pack for approval" when the rules say Ready; otherwise "Ask for approval with N things missing", which lists them and
  needs a one-line reason of up to 140 characters (page-side, G11). The approver's name is not exposed (G10).
- **Awaiting approval** reviews one pack: conditions met, service, revenue estimate (recorded or changed, never the
  amount), next step; an exception, an out-of-date pack or a pending handover as a notice; the six conditions with the
  evidence, its source and who confirmed it. On the right: Approve (or, when out of date, ask the owner to send it
  again), Return with a comment, and Approve and hand over, Park it, Close. The owner of an out-of-date pack sends it
  again from here.
- **Qualified** and **Parked** are tables with the side panel. Parked shows what the client said, why and when it was
  parked, the check-back date and what happens then, with Resume now, Change the date, Record a reply and Not a fit.
- Revenue (D5): the estimate is entered in a form that starts empty and is never shown again ("Revenue estimate
  recorded, changed <date>"); activity lines that name an amount show "[amount]".

The page supports windows from 1024px wide (the body's minimum is 1000px, so a 1024px window keeps room for its
vertical scrollbar without scrolling sideways). Below 1280px the rail starts collapsed (icons only) unless the person
opened it.

Stage labels (`options.js STAGE_LABELS`; the backend's values do not change): researching Researching, review Your
decision, identified Identified, contacted Contacted, active Active, approval Awaiting approval, qualification
Qualified, nurture **Parked**.

## Files

```
frontend/
  index.html          the page: side rail, main region, search dialog, agents pill and Activity, toast region; loads app/main.js
  app/
    main.js           person picker, router (with the old route names), the Inbox count, the dock, event delegation
    api.js            fetch wrapper: X-User header, error messages, ApiError
    lib.js            html`` escaping template, icons, dates, URL helpers, ROUTE_ALIASES
    shell.js          the side rail, CRM sync line, chooser, toasts, loading/empty/error states, the dock
    search.js         the search dialog (Ctrl K)
    forms.js          busy buttons, "what is missing" lines, tab keys, one write then redraw
    undo.js           the Undo toast, the waiting-email notice and its Undo and Send now buttons
    updates.js        background updates every 20 seconds, notices, and the notification history (the bell)
    options.js        choices the backend accepts (tests keep them equal to api/services.py)
    styles.css        the redesign's tokens and every style; no colour outside :root
    pages/
      home.js (Overview) inbox.js companies.js sources.js (Find) outreach.js qualification.js agents.js settings.js
      library.js connectors.js
      record.js       the company record (read only)
      team.js         the six agents: cards, state, suggestions, each agent's page (used by agents.js and the dock)
      links.js        which page handles an opportunity at each stage
```

There is no build step. FastAPI serves `frontend/index.html` at `/` and `frontend/app/` at `/app/`, both with
`Cache-Control: no-cache`, so an edited file shows on the next reload.

## How a page works

Each page module exports:

- `title`: the browser tab title.
- `load(ctx, params)`: fetch what the page needs; `params` are the URL's query values.
- `view(data, ctx, params)`: return the page as an `html` template.
- `actions`: handlers named by `data-act` attributes. An element with `data-act="name"` calls `actions.name(element,
  event, ctx)` on click; `data-on="submit input change"` chooses other events. The nearest element listening for the
  event type wins, so a button inside a form can have its own handler.
- `after(root, ctx, data)` (optional): runs after drawing, for example to poll running research.
- `instant(ctx, params, data on screen)`, `slots(data, ctx)`, `changed(early, data)` (optional): draw a picked item at
  once from data the page already holds; `slots` names parts (`data-slot`) the full load replaces in place; `changed`
  says whether the full load differs enough to draw again.

`ctx` carries the person acting (`ctx.me`), the workspace users, the configuration, the workspace date
(`ctx.today`, which is the simulated date in demo mode), the API client, `toast`, `go(page, params)` and `refresh()`
(load and draw the current page again, keeping the scroll position).

Open forms, errors and notices live in a small module-level state object, so they survive a redraw and are cleared
when the person moves to another item.

## Rules the code keeps

- **Escaping.** Every value goes into the page through the `html` template, which escapes it. Pages never assign
  `innerHTML` themselves, and links from the web pass through `safeUrl`, which drops anything but http and https.
  API data comes from web pages and AI models, so this matters.
- **Who is acting.** There is no sign-in yet. The person picks their name; every request sends it in `X-User`.
- **No silent overwrite.** Forms that change an opportunity send the `revision` the person read; the backend refuses
  a change made on an older version (409) and the page says so.
- **Exactly what was approved goes out.** Approve and send re-reads the email preview and stops if the text changed
  since the person opened it; the backend checks the same fingerprint.
- **Nothing invented.** Counts, contacts and suggestions come from the API. Empty states say what happened and what
  comes next.
- **Design.** The reviewed redesign (`design/redesign/app.css`), which replaced `DESIGN.md`'s warm neutrals and single
  orange accent in R1 (13 Nov 2026). Geist at 400-600 on a cool grey canvas, white 14px cards with a border and a low
  shadow, Lucide-style stroke icons. Four colours carry meaning and nothing else does: ink (#101828) for an action you
  can take (primary buttons), purple (#5B4FE0) for agent work (also links, focus rings and the selected row), orange
  (#B8430F / #D9622B) for "waiting on you" (counts, decisions), green (#17663F) for done. Red only for something broken.
  Every colour is a token in the `:root` block of `styles.css`; there are no colour literals anywhere else (a test
  checks). A disabled submit button has a line next to it saying what is missing. Toasts are for things not visible on
  screen.
- **Accessibility.** Every control has a name (a wrapping label or `aria-label`), tabs follow the ARIA pattern with
  arrow keys, Home and End, the main region takes focus after navigation, a skip button leads to it, errors use
  `role="alert"`, the toast region is a polite live region, and focus rings are always visible.

## Adding a page

1. Create `frontend/app/pages/<name>.js` with `title`, `load`, `view` and `actions`.
2. Import it in `main.js` and add it to `PAGES`; add it to `NAV` in `shell.js` if it belongs in the side rail (a test
   checks that every rail link has a page).
3. If it offers fixed choices the backend validates, put them in `options.js` and add a check to
   `tests/v2/test_frontend.py`.
4. Add its routes to `tools/a11y_audit.py`.

## Checks

| Check | Command | Needs |
| --- | --- | --- |
| Static and logic tests | `python -m pytest tests/v2/test_frontend.py` | Node.js for the module checks (skipped without it) |
| Whole workflow in a browser | `python -m pytest tests/e2e` | Playwright and its Chromium (skipped without them) |
| Accessibility audit | `python tools/a11y_audit.py http://127.0.0.1:8800 "Alex Morgan"` | A running server, Playwright. It also checks the problem banners on three pages, with `/api/todo` and `/api/toolkit` answered as if every problem were on |

The end-to-end test starts its own server on a fresh demo database (agents run only when asked, mock mail, no CRM)
and clicks through: research a candidate, accept it (undo that from the toast, then accept again and let the window
pass), let the outreach agent draft, approve and send (then Send now instead of waiting), record the reply, close Meridian's gaps, send the pack, and approve it as another person.

Playwright is not in `requirements.lock`. To install it: `pip install playwright` then `python -m playwright install
chromium`.

## Known limits

- No sign-in: anyone at the computer can choose any name. The backend trusts `X-User`.
- Live mode (real AI, Gmail, Insightly) has not been clicked through the page; the page calls the same endpoints the
  live checks use.
- Agent traces show real steps only for tool-using agents in live mode; demo runs have none.
- Gmail state is the workspace's mailbox as a whole: the API has no per-person mailbox health, so the banner cannot
  name the person whose Gmail is signed out, and the design's "Tell Alex" is left out (G22).
- Geist loads from Google Fonts; without a connection the page uses Segoe UI or the system font.
