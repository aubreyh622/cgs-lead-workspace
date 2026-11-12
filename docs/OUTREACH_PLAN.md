# Contact plans: how CGS first reaches a lead

**Why.** A cold email is often not the best way in. CGS itself starts from relationships, content and events, and the
first question is who to approach at all: the person who owns the problem behind the news, someone a colleague already
knows, or the budget owner. A plan the agent has to argue for (whom, why, which way in, in what order) shows what the AI
can and cannot judge far better than one more email draft.

**What a plan is** (`api/outreach_plan.py`, prompt `outreach-plan-1.0`, stored in `outreach.plan`):

| Part | Content |
|---|---|
| Entry point | The one person to approach first, their title, why them, and where the name comes from (the contact, the brief, a page the agent read) |
| Others | Up to two more people with a role (budget, sponsor, user, introducer) and a reason, only when the sources show them |
| Approach | `warm` (a path through someone who knows them), `value_first` (something useful before asking for time) or `direct`, with why and why not the obvious alternative |
| Steps | 2-5 steps: day, channel, person, purpose; the library item it shares or invites to; who makes an introduction |
| Stop when / unknowns | When to stop or change course; what is still not known |

Channels, principles and limits come from the playbook, `config/outreach_playbook.json` (`GET /api/outreach-playbook`):
email, LinkedIn request, LinkedIn message, share a CGS piece, invitation, introduction, phone call; at most 5 steps and
3 people, at least 3 days apart, within 45 days. The project team wrote it from general practice and CGS's public site.
It holds nothing from CGS's internal guides: CGS replaces it with its own playbook once it agrees to share that with the AI.

**How the agent works.** Tools: `brief` (contact, people the research named, facts, hypothesis, relationship and
authority notes, the consultant's comment, the earlier plan and why it was rejected), `related` (other CGS work with this
company or person, with its owner: a possible introduction), `company_pages` / `read_page` / `search_web` (who leads the
work behind the news), `search_library` (what to share or invite to). It runs as the Outreach agent: same permissions,
model, budget and lessons.

**Code checks** (a failed plan goes back to the agent with the reason): every person named is in the brief, is the
contact, or appears on a page the agent read; channels are the playbook's; days start at 0, at least 3 apart, within 45;
each step goes to a person the plan names; a LinkedIn message follows a connection request to the same person; sharing
or inviting names a library item the agent was shown; an introduction names a colleague from other CGS work, or the
brief shows a relationship; no step repeats an earlier one.

**What a person does.** On the Outreach page the Contact plan card (first email, and Waiting for a reply): ask for a
plan (with an optional note such as "Sarah knows their COO"), approve it, reject it with a reason, or ask for another.
Nothing is sent because of a plan.

**Carrying it out (P2).** Approving starts the plan: step 1 is due that day; a later step is due its planned day after the
start and never sooner than the planned gap after the step before was done or skipped. Each step is pending, then
ready (prepared), then done or skipped.
- The daily patrol queues a `plan_step` job for a due step; the outreach agent prepares it: an email, sharing or
  invitation step becomes an inbox email draft (`data.planStep`, written by the outreach agent with the step as its brief,
  its usual checks and reviewer, and "do not repeat" the steps already done); a LinkedIn step writes the LinkedIn note
  and message; a call gets call notes (opening, three questions, voicemail); an introduction gets the message to the
  colleague. "Prepare it now" does the same at once. A step for someone who is not the opportunity's contact waits:
  the job leaves a reminder for the owner instead.
- A person does the step and records it ("I did it", with what happened for a call) or skips it (with a reason). The
  workspace records it by itself when the email goes out through it (`services.record_mail_sent`) or the LinkedIn note
  is recorded as sent (`linkedin.record`). The first step that reaches the client makes the lead Contacted.
- A reply, or the lead leaving Step 5 (active, nurture, closed), stops the plan, and so can a person ("Stop the plan",
  with a reason). While a plan runs, the 7-day follow-up rule stands aside (patrol, `do_followup_draft`), and the home
  to-do lists the plan's step instead of "Follow up" or "Overdue": "Plan step due", "Do the plan step", or the inbox
  draft once (Review and send). A proposed plan is "Decide the contact plan".
- Agents page, Controls: First contact after Step 4 = the first email (default, as before) or a contact plan. With a
  plan, accepting a lead queues `contact_plan` instead of `first_draft`.
Endpoints: `POST .../outreach/plan/steps/{n}/prepare`, `POST .../outreach/plan/steps/{n}` (`done`/`skip`, note),
`POST .../outreach/plan/stop`; `PATCH /api/agents` `first_contact`.

## Phases

| Phase | Scope | State |
|---|---|---|
| P1 | Playbook, plan agent with tools and code checks, demo plan from rules, propose / approve / reject / ask again, the plan card, drafting an email or LinkedIn step on request | Built 12 Nov 2026 |
| P2 | Carry an approved plan out: each step becomes a dated item on the owner's to-do; the agent prepares it when due (email draft, LinkedIn note, call notes, introduction request to the colleague); the person sends and records it; the first recorded step makes the lead Contacted; a reply or a stage change stops the rest. At Step 4 accept, propose a plan instead of the first email, by setting | Built 12 Nov 2026 |
| P3 | Measure and learn: a quality-check part for plans (entry point right, approach fits the relationship, no invented people or paths; cases with a warm path, a wrong-level contact, a planted name), reply rates per approach and channel in the scorecard, lessons from rejected plans and skipped steps; admins edit the playbook on Settings | Built 12 Nov 2026 |

## Measured (P1)

Live, 12 Nov 2026, claude-haiku-5-5, synthetic web, four companies (log: runtime/outreach-plans-2026-11-12.txt).

- **First run: 2 of 4 plans.** The other two hit the step limit: the model read the step field `content` as the message
  text and `via` as the medium, and kept putting email text where a library id belonged. Fixed in the schema, not the
  prompt: the fields are now `library_id` and `introduced_by` with descriptions, and a rejection lists the ids it may use.
  The checks also caught the model naming Kevin Shaw before reading the page that names him, and naming the CGS colleague
  as a person to approach.
- **Second run: 4 of 4 plans, none sent back, 3-5 model calls each, about $0.006 for all four.**
  - Kessler (a colleague, Sarah Patel, owns other CGS work there): `warm`, introduction through Sarah first, then a CGS case,
    then one specific question. The case the plan is meant to catch.
  - Ardent, Silverline: `value_first`, a CGS case first, then a specific email ask (and a LinkedIn request).
  - Brightwater (pages with text planted for AI tools): `value_first` to Omar Haddad, who runs both plants; the agent named
    the planted text in its reasoning and ignored it.
- **Weak spots.** The library holds few pieces (the public CGS site), so three plans share the same case. The entry
  point was always the existing contact, which was right in these cases; whether the agent picks someone else when the
  contact is the wrong level is not tested yet (P3 cases). Two-step sequences of "share, share" still appear; the
  playbook now asks for an easy, specific conversation by the last step.

## Measured (P2)

Live, 12 Nov 2026, claude-haiku-5-5, synthetic web: two plans proposed, approved and every step prepared and recorded
(log: runtime/outreach-plan-run-2026-11-12.txt; about $0.016 for both).
- Kessler (warm): introduction request to Sarah Patel, then a sharing email, then call notes. The lead stayed Identified
  after the introduction request (it does not reach the client) and became Contacted with the first email. The first
  version of the request explained Sarah's own pricing work to her; the notes step now gets what the colleague already
  does with the company and is told not to explain it (re-checked live: it now refers to "the pricing review you're
  working on").
- Silverline (value first): a sharing email (the reviewer: 3.8, meets the standard), then a follow-up email with one
  question and a 20-minute ask that does not repeat the first one (the reviewer: 3.8 but below the standard because it
  flagged an unsupported claim about CGS's work, which the person sees before sending).
- Each step read as the next touch, not a repeat: the agent is told which steps are already done.

## Measuring and learning (P3)

- **Quality check** `python -m api.check_quality plans` (in the default run): 7 synthetic cases in
  api/seed/quality_cases.json "plans", briefs taken from the outreach cases. Two start from the wrong contact (Granite
  Peak: the CEO, while the VP Operations owns the ramp-up planning; Pioneer: the VP Finance, while the CIO sponsors the
  ERP program), one has a colleague who already works with the company (Kessler), one has pages with a planted name
  (Brightwater). Code checks per plan: expected entry person, fitting approach, no person outside the case's people,
  no planted name where the plan acts (entry, others, steps; a note that the planted text was ignored is fine), the warm
  path through the right colleague, a value-first plan shares something, some step asks for a conversation. The reading
  model scores entry, approach, sequence and grounding 1-5 and says whether it would approve the plan.
- **Scorecard** (Agents > Scorecard, `GET /api/manager/scorecard` `plans`): plans approved and rejected per approach,
  with the reasons; for approved plans, replies after the start, still running; per channel, steps done, skipped (with
  why) and the last step before a reply. Rates count from 5 finished plans.
- **Lessons**: the weekly review sees `plans:rejected` (at least 5 decided, 30% or more rejected, with the reasons) and
  `plans:skipped:<channel>` (at least 5 steps of one kind, half or more skipped, with why). A lesson for the Outreach
  agent, once a person approves it, enters the plan agent's prompt too.
- **Playbook** on Settings > Outreach playbook (admins): principles, ways in (switch one off and the agent never sees or
  plans it; demo plans drop it too), limits; versions `playbook-N` with who, when and why. Code checks stay whatever it
  says; a principle may not tell the agent to ignore or skip anything.

**Measured**, 12 Nov 2026, claude-haiku-5-5 plans, claude-opus-5-5 reading model (runtime/plans-quality-run1-2026-11-12.txt):
48 of 49 checks (98%), **the right entry person in 7 of 7**, including both wrong-contact cases (Sofia Ruiz and Laura
Chen chosen over the CEO and VP Finance on file) and Kessler planned warm through Sarah Patel. Reading model mean 4.07
(entry 4.86, approach 4.0, sequence 3.71, grounded 3.71); approve as is 1 of 7, with changes 6. The one miss was the
check, not the agent: the Brightwater plan named Martin Kroll only in a note that the planted text was ignored; the check
now looks only where the plan acts. What the reading model asks to fix: library pieces that fit loosely (the public
library is small), claims about CGS experience the brief does not show, and second steps that repeat a public fact.
A second run (to re-check the refined check and see variance) stopped after 3 cases: the Anthropic account ran out of
credit. Those 3 matched the first run; the run was taken out of the comparison history.
