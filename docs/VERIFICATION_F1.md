# Frontend phase F1 verification

Date: 2026-11-05. Scope: the shell, Home queue and Companies page (Steps 1-4) described in
`DEVELOPMENT_PLAN.md` under "Frontend phases".

## What F1 contains

- `frontend/index.html` loads `frontend/app/main.js` and `styles.css` (served at `/app/`, `Cache-Control: no-cache`).
  The earlier static prototype moved to `design-explorations/CGS Lead Workspace static prototype.html`.
- Shell: top navigation (Home, Companies, Outreach, Qualification, Agents) with the Companies count, a person
  picker (no sign-in; the name goes in `X-User`), a "Synthetic data" / "Live data" badge, toasts, and loading, empty
  and error states. Desktop only: no phone or tablet layout (1024px minimum width).
- Home: a "To do" queue built only from backend records (alerts for the person, inbox items, stopped research,
  companies ready to decide, new candidates), then the person's next steps and leads by stage. No stat tiles.
- Companies: five tabs kept in the URL (New candidates, Being researched, Ready to decide, Accepted, Set aside).
  Research one or several candidates; skip with a reason and scope; stop, resume and retry research, or save a
  website and retry; read the brief (sourced facts kept apart from the unconfirmed hypothesis, flags, priority,
  contact, CRM duplicate check, other CGS work) and accept, send back with questions, or reject with a reason.
- Outreach, Qualification and Agents are read-only lists until F2-F4.

## Backend change found while building

Every write endpoint uses the `Actor` dependency, which commits the request's changes. With FastAPI 0.162 a `yield`
dependency's exit runs after the response is sent, so a read sent straight after a write could return the old data
(reproduced: skip a candidate, list candidates at once, the candidate was still listed). `Actor` is now
`Depends(ctx, scope="function")`: the commit happens before the response. The CRM sync that follows a change still
runs after the response, through a separate request-scoped dependency. After the fix the same check passed three
times out of three against the running server.

## Checks run

- `tests/v2/test_frontend.py` (new, 8 tests): page and modules served without caching; every relative import
  exists; skip, reject and accept reasons and scopes match `api/services.py`; colours only from `:root` tokens;
  pages build HTML only through the escaping template; a write is visible to the next read; Step 4 refuses a stale
  revision with 409.
- Full offline suite (`pytest tests --ignore=tests/research/test_app_and_contract.py
  --ignore=tests/research/test_five_company_workflow.py`): 531 passed, 1 failed. The failure was
  `tests/p3/test_handoff_http.py::test_handoff_and_contact_http_lifecycle` with a Windows `ConnectionAbortedError`
  (10053) while the dev server and browser were also running. It tests the earlier integrated server
  (`server.py`), which uses neither `api/` nor `frontend/`; run alone afterwards it passed twice.
- `tests/v2/test_frontend.py` also checks module syntax with Node, copying each file to `.mjs`: plain
  `node --check file.js` passed a file with a dangling `else` during this phase, so it is not relied on.
- Manual run in the app's browser against a separate demo database (`runtime/frontend-dev.sqlite3`, agents off,
  CRM and mail mock): person picker; Home queue; send back with questions (demo research answers at once and the
  page says so); accept with the duplicate-check confirmation; research two candidates from the selection bar;
  skip with a reason (shown under Set aside); stop and resume research; reject with "Other" (comment demanded) and
  then a listed reason (recorded correctly in `/api/feedback`).

## Not verified

- Live mode (real AI, real web, Gmail, Insightly) through the page. The page calls the same endpoints, but no live
  run was made in F1.
- Assistive technology beyond keyboard tab order and labels; the full accessibility pass is F5.
- Research polling against a long live research run (demo research finishes at once).
