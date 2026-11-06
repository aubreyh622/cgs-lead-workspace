# Static demo (GitHub Pages)

A click-through demo of the workspace page that runs in the browser without a server, for sending to people who
want to try it. It shows the real page on the synthetic demo data. No API, AI or mail call is made: the agents'
work was recorded from the real backend in demo mode.

## How it works

1. `record.py` starts the v2 backend in its own process on a fresh synthetic database (`APP_MODE=demo`, mock
   mailbox, mock CRM, no knowledge folder, network blocked by the test offline guard). It drives the page in
   Chromium through the story and records every API answer after each step:
   pick Corvane Industrial → research → accept (with a 10-second undo window) → the outreach agent drafts →
   approve and send → Elena Fischer replies → record the reply → the evidence agent reads it → close Meridian's two
   gaps → send the pack → Sarah Patel approves.
   The first state records every page and item. Later states record the story's pages and its items (Corvane,
   Meridian). The site answers anything else from an earlier state.
2. `build.py` copies `frontend/`, swaps `app/api.js` for `site/api.js` (answers come from the recording) and adds
   `site/demo/`:
   - `runtime.js`: the recorded states and the steps between them. A click the story recorded moves to the next
     state. Undo goes back one state. Changes the backend made on its own (an undo window closing, an agent
     finishing, a client reply) happen after a few seconds. Recorded times move with the visitor, and a typed email
     address or reply replaces the recorded one. Other changes answer "this change is not saved".
   - `fx.js`: research steps slide in, the agent's draft types itself out, changed counts pop, and a card shows the
     agent at work.
   - `tour.js`: the guide at the bottom left. It shows the next step, takes the visitor to its page, rings the button
     and can fill in the form.
   - `clock.js`: the page lives on the day of the recording, with time running from the first visit.
   The build stops if the recording holds a local path, a real mail domain, CGS web text or a key-like string.

## Rebuild

```
.venv/Scripts/python.exe -B tools/static_demo/record.py
.venv/Scripts/python.exe -B tools/static_demo/build.py --out ../cgs-workspace-demo
```

Recording takes a few minutes. Record again after a frontend or backend change that alters the story. Then build,
open the output with any static file server (for example `python -m http.server` in the output folder) and commit
the output folder to the demo repository.

The state lives in the browser tab (sessionStorage). "Restart the demo" in the guide starts again.
