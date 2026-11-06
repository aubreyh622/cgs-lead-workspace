# The workspace page

The browser interface of the v2 workspace. It reads and writes the v2 API (`api/`) and shows only what the backend
records. Desktop only: there is no phone or tablet layout, and the page is at least 1024px wide.

Open it with `Start Workspace v2.cmd` (http://127.0.0.1:8800/). The `Legacy - …` launchers start the earlier P0-P5
integrated app, which has its own interface.

## Pages

| Route | Page | Steps | What a person does there |
| --- | --- | --- | --- |
| `#/home` | Home | All | The queue of things waiting for them, their next steps, their pipeline by stage |
| `#/companies` | Companies | 1-4 | Pick or skip candidates, watch research, decide on each brief |
| `#/sources` | Sources | 1 | Ask the discovery agent (and how many), automatic searches, add a company, set and approve the research direction, see what searches taught |
| `#/outreach` | Outreach | 5 and the first reply | Approve and send email drafts, record what was sent, follow-ups, replies, nurture |
| `#/qualification` | Qualification | 6-8 | Notes and evidence proposals, scores and conditions, tasks, the pack, the Step 8 decision |
| `#/agents` | Agents | All | The six agent cards and their suggestions; each agent's own page (`?agent=research`) with its pause switch, instructions and, for the research agent, Try it on a company; the records: controls, task board, AI runs, permissions, lessons, scorecard |

Tabs and selections live in the URL (`#/companies?tab=review&id=ostrander`), so a link opens the same view. When a page
opens without a tab, the tab it chooses is written into the URL, so a later redraw (a brief finishing, a click elsewhere)
never moves the person to another tab. Picking another item on
the page you are on shows it at once: Companies already holds every brief in Ready to decide, and Outreach and
Qualification fetch the other items of the open list in the background (kept for a minute). The full load follows and
fills in what was still being checked (the Insightly duplicate check says "Checking…" until then, and is kept for five
minutes) and draws again only if something changed. After any change the person makes, the next view loads fresh. An
item not held yet keeps the page on screen, not clickable, until it is ready; there is no loading skeleton, and focus
stays on the item picked.

Every page also has the agent dock at the bottom: a pill with the number of agent tasks running or queued (or
"paused") and how many items the agents prepared that wait for this person, which opens a panel with each agent's
state. The pill's number is the sum of that panel's agent rows.

Beside it, the bell opens the notification history (`app/updates.js`). The page asks `GET /api/updates` every 20
seconds while the tab is visible, and at once when the person comes back to it. What the agents and the workspace
finished in the background (an agent's draft or new candidates, a client reply, a brief that is ready or research that
stopped, an approved email that went out or did not) shows as a notice with its link, the navigation counts and the
dock are brought up to date, and the page on screen is drawn again unless the person is typing or has unsaved text in a
field. The history keeps those items and the notices of the person's own actions for a week; the bell counts the ones
not seen yet, per person in this browser.

Counts follow one rule: a number equals what you see when you open it. Each navigation badge equals its page's list
(Agents: the suggestions on the Agents page; Outreach: drafts to approve plus client replies to record), and Home's To
do lists everything that waits for the person, agent suggestions included. A client reply found in the email thread
waits on the lead it answers, in Waiting for a reply (or Nurture), first in the list, where the person records it. The header shows the CRM sync state (`GET /api/crm/state`): synced and when, how many wait or
failed or need a decision, or read only or off; it links to the agent controls, where a failed sync can be retried
and the read-back run. A field someone changed in the CRM shows as a notice on the opportunity's Outreach and
Qualification pages ("Keep Insightly's" or "Use the workspace's") and as a To do on the owner's Home.

Undo (`app/undo.js`, `api/undo.py`): Approve and send waits 10 seconds before the email goes out. The toast offers Undo,
and the email's page shows when it goes out with Undo and Send now; a send that failed or did not go out says so there.
Accepting or rejecting at Step 4 can be undone from its toast for 10 seconds, before anything reaches the CRM or the
agents (returning to research starts at once, so it has no undo). Skipping a candidate offers Undo in its toast, and the
Skipped candidates list keeps an Undo button.

Companies, Ready to decide: facts that cite the same page name it once, and news over a year old is marked; long
paragraphs read as points. Priority shows only cells with something in them: no revenue tier from public research
("Not estimated"), contact authority from the brief or the job title, and the relationship from the Insightly check and
the check for other CGS work. Who to contact lists up to 3 people with their source and a LinkedIn search link. The
accept form's duplicate confirmation follows the checks: when they ran and found nothing, the person confirms only what
the checks cannot see. Send back offers the brief's open questions and flags as cards to tick. Sources has a back link
to Companies.

Outreach: an agent's draft has Edit; the edited text is what the person approves. Nurture shows why and when the lead
was moved, the review date and what happens then, with Reconnect now, Change the review date, Record a reply and Close
the lead.

Qualification keeps one lead's first screen short: a "Next:" line that says what to do now (and the evidence agent's
last summary), the six conditions with what the rules say, and "To reach Ready": every gap, one per question, including
the score threshold (Q 70), the top three first, each with what to ask and a button to confirm it by hand; the questions
can be copied for the next call. Then evidence waiting for review, and the next step with Add notes (notes can be loaded
from a text file or a .vtt/.srt transcript) (Send for approval when the rules say Ready; for
an approver, the quick decision). Scores and their reviews, the client need, tasks, the manager agent's memory, the
pack and the history are folded sections; a section stays open or closed while the person moves between leads.

The page supports windows from 1024px wide. Below 1400px the header hides the brand text and the CRM sync time (a
failed or waiting count stays); below 1200px it also hides the navigation icons and the avatar.

## Files

```
frontend/
  index.html          the page: header, main region, agent dock, toast region; loads app/main.js
  app/
    main.js           person picker, router, navigation counts, agent dock, event delegation
    api.js            fetch wrapper: X-User header, error messages, ApiError
    lib.js            html`` escaping template, icons, dates, URL helpers
    shell.js          header, chooser, toasts, loading/empty/error states
    forms.js          busy buttons, "what is missing" lines, tab keys, one write then redraw
    undo.js           the Undo toast, the waiting-email notice and its Undo and Send now buttons
    updates.js        background updates every 20 seconds, notices, and the notification history (the bell)
    options.js        choices the backend accepts (tests keep them equal to api/services.py)
    styles.css        DESIGN.md tokens and every style; no colour outside :root
    pages/
      home.js companies.js sources.js outreach.js qualification.js agents.js
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
- **Design.** `DESIGN.md` (AFP-Relay/design): warm neutrals, one orange accent for action and position, Inter at 400
  and 500, Lucide-style stroke icons, 12px cards, no colour literals outside the token block. A disabled submit
  button has a line next to it saying what is missing. Toasts are for things not visible on screen.
- **Accessibility.** Every control has a name (a wrapping label or `aria-label`), tabs follow the ARIA pattern with
  arrow keys, Home and End, the main region takes focus after navigation, a skip button leads to it, errors use
  `role="alert"`, the toast region is a polite live region, and focus rings are always visible.

## Adding a page

1. Create `frontend/app/pages/<name>.js` with `title`, `load`, `view` and `actions`.
2. Import it in `main.js` and add it to `PAGES`; add it to `NAV` in `shell.js` if it belongs in the navigation.
3. If it offers fixed choices the backend validates, put them in `options.js` and add a check to
   `tests/v2/test_frontend.py`.
4. Add its routes to `tools/a11y_audit.py`.

## Checks

| Check | Command | Needs |
| --- | --- | --- |
| Static and logic tests | `python -m pytest tests/v2/test_frontend.py` | Node.js for the module checks (skipped without it) |
| Whole workflow in a browser | `python -m pytest tests/e2e` | Playwright and its Chromium (skipped without them) |
| Accessibility audit | `python tools/a11y_audit.py http://127.0.0.1:8800 "Alex Morgan"` | A running server, Playwright |

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
- Inter loads from Google Fonts; without a connection the page uses the system font.
