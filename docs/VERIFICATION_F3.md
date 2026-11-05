# Frontend phase F3 verification

Date: 2026-11-05. Scope: the Qualification page (Steps 6-8), as listed under "Frontend phases" in
`DEVELOPMENT_PLAN.md`. Desktop only.

## What F3 contains

`frontend/app/pages/qualification.js`, linked from the navigation (count: packs others sent for approval, plus the
person's own leads the rules call Ready) and from Home (the same two kinds of item).

- Tabs in the URL: In conversation, Awaiting approval, Qualified, Closed. Awaiting approval always shows the whole
  team, because the approver is usually not the owner. Each list item shows the six conditions as a small bar.
- Readiness from the rules engine (`/api/rules`, the assessment on each opportunity): readiness, qualification
  score range, evidence coverage, priority, and every gap with the question to ask. A blocker can be cleared with a
  reason. The page states that no AI calculates these numbers.
- Evidence to review: each proposal from the evidence agent with its value, the client quote, why, and the current
  value. Accept, edit (choose another value) or reject, with a reason where the backend needs one; replacing a value
  a person already confirmed asks for a reason before the request is sent.
- Add notes from a call, email or phone call, with or without asking the evidence agent; the page reports how many
  proposals were made, how many were dropped for a quote not found in the notes, and who the research agent was asked
  to find.
- Manual review of each BANTHE score (anchor text for every value; History also allows 2.5), each condition (with an
  optional blocker when not met), and the CGS revenue estimate, each with the evidence or reason and the revision the
  person read.
- Client need statement, editable; the page notes that editing it does not confirm Need or fit.
- Next actions from the manager agent, turned into agreed tasks (title, side, who, due); tasks ticked done or
  reopened, overdue ones marked.
- Step 7: send the pack when the rules say Ready, or request an exception with a reason; a warning when proposals
  are still waiting.
- Step 8: the frozen pack (scores, conditions, revenue, problem), exception and out-of-date warnings; approve (with an
  optional new owner, which waits for that person to accept), send back with questions, move to nurture, or close
  with a reason. The role list follows the backend's rule (the owner acts as Lead owner, others as another
  consultant or Director of Operations). The new owner can accept or decline a handover; the owner can resubmit an
  out-of-date pack. Approve is disabled for an out-of-date pack and while a handover waits.
- History: confirmed evidence, decided proposals, notes, decisions and activity.

No backend change in F3.

## Checks run

- `tests/v2/test_frontend.py` now has 12 tests. New: close reasons, approver roles, note kinds and need fields match
  the backend; the Qualification count (run with Node against the real module).
- Full offline suite (same command as F1): 537 passed, 0 failed. The frontend tests were run again after the last
  two fixes (12 passed).
- Manual run in the app's browser, demo database (evidence and next actions from the demo rules), agents off:
  - Alex, Meridian: added call notes; the evidence agent proposed two items and asked the research agent to find the
    CFO. Accepted one, rejected one with a reason. Reviewed Authority to 4 (the page showed "Q +16") and the funding
    condition to Met; the rules then said Ready. Sent the pack.
  - Sarah: saw the pack (navigation count 2), approved it with Daniel Brooks as the new owner; the page showed that
    the approval waits for Daniel.
  - Daniel: accepted the handover; Meridian moved to Qualified with Daniel as owner and the Step 8 decision recorded.
  - Alex, Bluegate: asked for next actions (three), made one a task, ticked it done.
  - Alex, Northstar: the out-of-date pack showed the warning with Approve disabled; resubmitted it.
  - Sarah, Northstar: sent it back with a question; it left Awaiting approval.
  - Alex, Bluegate: set the revenue estimate and the need statement; reviewed Budget to 4; added a note whose
    proposal (Budget 2) conflicted with it: the page asked for a reason before accepting, and the score moved 4 to 2;
    edited another proposal (Need 2 to 3) and accepted that version.
- Found and fixed while testing: the revenue fields had min=1 with step=1000, so the browser only accepted 1, 1001,
  2001 and so on and silently blocked 200000. They now take any amount. The revenue confirmation is written by the
  page, because the backend's change text shows a raw Python dictionary.

## Not verified

- Live evidence and next-action agents (the demo rules ignore most of the note's meaning).
- Nurture and close at Step 8, declining a handover and clearing a blocker were built but not clicked. They call
  endpoints the backend tests already cover.
- `CGS_DIRECTORS` restrictions (none set in the demo).
