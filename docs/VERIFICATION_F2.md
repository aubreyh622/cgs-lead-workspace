# Frontend phase F2 verification

Date: 2026-11-05. Scope: the Outreach page (Step 5 and the first reply), as listed under "Frontend phases" in
`DEVELOPMENT_PLAN.md`. Desktop only.

## What F2 contains

`frontend/app/pages/outreach.js`, linked from the navigation (with a count of items to approve) and from Home.

- Tabs in the URL: To approve, Waiting for a reply, Nurture, In conversation. "Show the whole team" switches from
  the person's own leads to everyone's.
- To approve lists new inbox drafts from the outreach agent (first, follow-up, reconnect), client replies the mail
  rules found, and accepted leads with no first-email draft waiting.
- First email (Step 5 draft): read the draft with its citations numbered and their sources listed; approve and send
  (the person sees the exact text and enters the address; the page re-reads the preview and stops if the text changed,
  and the backend checks the same fingerprint); put it in Gmail drafts; comment and revise (the outreach agent writes
  again with the comment); edit it by hand; record a message sent outside the workspace; move to nurture.
- Agent draft from the inbox: approve and send (with the draft's payload hash and the address), use it as the Step 5
  draft (first email) or accept it to send yourself (follow-up), or dismiss.
- Client reply from the inbox: record it (the lead becomes Active) or mark it "Not a reply" (out-of-office).
- Contact: shows whether a person confirmed it; confirm or correct the name and role with where it was checked.
- Waiting for a reply: what was sent and when, the follow-up date, a link to a waiting follow-up draft; record a
  reply, record a follow-up, move to nurture. Nurture: review date and reason; record a reply.
- Other CGS work with the same company or person is shown before sending.
- When email is off, the page says so and offers recording what was sent instead of sending.

No backend change in F2.

## Checks run

- `tests/v2/test_frontend.py` now has 10 tests. New: the To approve list is built from new drafts and replies plus
  leads without a first-email draft (run with Node against the real module); the HTML template escapes values and
  `safeUrl` drops `javascript:` links.
- Full offline suite (same command as F1): 535 passed, 0 failed.
- Manual run in the app's browser, demo database, agents off (jobs run by hand with `POST /api/agents/run`), mail
  provider mock, CRM off:
  - Approve and send the agent's first email to Ostrander (mock mailbox): lead moved to Waiting for a reply.
  - Halvorsen without a draft: asked the agent for one (demo rules), edited the subject, confirmed the contact with
    a note, recorded a LinkedIn message as sent.
  - Bluegate: recorded a follow-up, then recorded a reply; the lead moved to In conversation and the reply was
    labelled "meet".
  - Ostrander: moved to nurture with a reason and review date; shown under Nurture.
  - Check email now: "Checked 1 thread."
  - Arbor: accepted through the API, the demo outreach agent drafted the first email, Home linked to it, and
    "Use it as my draft" made it the Step 5 draft.

## Not verified

- Sending through real Gmail, and the "Put it in my Gmail drafts" button (the mock mailbox was used; the button was
  not clicked).
- A client reply found by the mail rules (the mock mailbox produced none), so "Record the reply" and "Not a reply"
  on an inbox reply item were not clicked. They call the same inbox endpoint as the tested draft actions.
- Comment and revise with the live outreach agent (demo rules ignore the comment's content).
- Live mode and Insightly logging by BCC.
