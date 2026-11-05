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

Tabs and selections live in the URL (`#/companies?tab=review&id=ostrander`), so a link opens the same view.

Every page also has the agent dock at the bottom: a pill with the number of agent tasks running, queued or waiting
for a person (or "paused"), which opens a panel with each agent's state.

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
and clicks through: research a candidate, accept it, let the outreach agent draft, approve and send, record the
reply, close Meridian's gaps, send the pack, and approve it as another person.

Playwright is not in `requirements.lock`. To install it: `pip install playwright` then `python -m playwright install
chromium`.

## Known limits

- No sign-in: anyone at the computer can choose any name. The backend trusts `X-User`.
- Live mode (real AI, Gmail, Insightly) has not been clicked through the page; the page calls the same endpoints the
  live checks use.
- Agent traces show real steps only for tool-using agents in live mode; demo runs have none.
- Inter loads from Google Fonts; without a connection the page uses the system font.
