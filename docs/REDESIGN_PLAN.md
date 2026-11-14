# Workspace page redesign: plan (R0)

Survey of the redesign in `design/redesign/` against the current page (`frontend/`) and the v2 API, written before any
code changes. It maps every current page to its new screen, every design element to the endpoint and field that feeds
it (or marks it a **gap**), proposes answers to the open decisions, and adjusts the phase list.

Read with: `design/redesign/README.md`, `docs/FRONTEND.md`, `docs/BACKEND_V2.md`.

Conventions in this document:

- **API** names an endpoint and field that exists today.
- **Derived** means the page can compute it from existing data without inventing anything (the rule is stated).
- **Gap** means the data does not exist. The element is left out until the backend supplies it. Each gap has an id
  (G1, G2, ...) and is listed again in section 6.

## 1. Findings that change the plan

These came out of the survey and matter more than any single screen.

1. **Ready is more than six conditions.** `backend/rules.py calculate()` says Ready needs all six conditions **and** a
   floor on five BANTHE dimensions (B, A, N, T, E) **and** confirmed coverage of at least 80 **and** confirmed Q of at
   least 70 (no blocker). The design equates Ready with "6 of 6 conditions met". A lead can be at 6 of 6 and still not
   Ready. The `gaps` list from the rules engine names each missing piece (dimension pending or below minimum, condition,
   Q, H, revenue). See decision D3.
2. **The six conditions match one to one.** `data/rules.json conditionLabels` (`problem, fit, external, next,
   commercial, conflict`) are the design's six conditions with other wording. Only the labels change on screen.
3. **Revenue is entered in the workspace today.** Qualification has a revenue review form (`POST …/review` kind
   revenue), and the rules use it for priority. Rule 6 says no amounts on screen. Entering a value without ever seeing it
   is odd. See decision D5.
4. **The brief has no versions.** `Opportunity.brief` is one record (`GET /api/opportunities/{id}` → `brief`). There is
   nothing to compare, so "What changed since last time" is a gap (G1).
5. **The brief's priority is not six met/missing conditions.** `brief.priority` holds `tier, tierWhy, authority,
   authorityWhy, relationship, relationshipWhy, fit, fitWhy, confidence`. The design's six rows (Fit, Timing, Buyer
   access, Relationship, Company size, Revenue potential) with Met/Missing chips, and "N of 6 priority conditions met",
   do not exist (G2). `tier` is a revenue tier: it is not shown (rule 6).
6. **Research progress has no steps.** `research.progress` is set only by the seed and to 5 when done; a live run
   writes free-text `research.log` lines. "Step 3 of 4" with named steps is a gap (G3).
7. **Second approval exists and matches the design.** Per person (`PUT /api/people/{name}` `secondApprover`), the
   approver approves or returns with a note (`POST /api/outbound/{id}/second-approval`), and the writer may withdraw.
   The design's Settings > People switch and the Outreach queue step are fully supported. Only the Inbox quick action
   lacks the email id (G9).
8. **The Insightly mapping differs from the design's text.** `config/crm.json`: Awaiting approval is lead status
   **Active**, not "Qualification"; Nurture is lead status **Nurture**, not "Stays Active". Qualified converts the lead
   into an opportunity in pipeline "Opportunity Pipeline", stage "Qualification". The stage bar uses the real mapping
   (section 3.4).
9. **The exception path exists, without an approver name or length limit.** `POST /api/opportunities/{id}/packs` with
   `exception` (any non-empty text) submits a pack that is not Ready; Step 8 records "Approved with exception". Who may
   approve is the director list (`CGS_DIRECTORS`, not exposed to the page) or anyone but the owner. So "Ask **Alex** to
   approve" has no name to show (G10), and the 140-character limit is page-only (G11).

## 2. Current pages mapped to the new screens

| Current route and tab | New route | Design screen | Notes |
|---|---|---|---|
| `#/home` (to-do groups, next steps, pipeline) | `#/overview` | `Main.dc.html` | Pipeline and "Needs you" column; the full to-do list moves to Inbox. `#/home` redirects |
| (none) | `#/inbox` | `Inbox.dc.html` | New. Fed by `GET /api/todo` |
| `#/sources?tab=find` | `#/find?tab=searches` | `Find.dc.html` Searches | Ask box, add a company, automatic searches |
| `#/sources?tab=direction` | `#/find?tab=direction` | Find, Research direction | |
| `#/sources?tab=learned` | `#/find?tab=taught` | Find, What searches taught | `#/sources` redirects |
| `#/companies?tab=new` | `#/companies?tab=found` | Companies, Found | Old tab names redirect |
| `#/companies?tab=research` | `#/companies?tab=research` | Companies, Researching | |
| `#/companies?tab=review` | `#/companies?tab=decision` | Companies, Your decision | Full-page brief review |
| `#/companies?tab=accepted` | `#/companies?tab=identified` | Companies, Identified | Table and side panel |
| `#/companies?tab=aside` | `#/companies?tab=aside` | Companies, Set aside | Table and side panel |
| `#/outreach?tab=approve` | `#/outreach?tab=you` | Outreach, Needs you | Three-column review queue |
| (part of approve / waiting) | `#/outreach?tab=mate` | Outreach, With a teammate | New split, see 3.6 |
| (none) | `#/outreach?tab=agent` | Outreach, Agent drafting | New split |
| `#/outreach?tab=waiting` | `#/outreach?tab=wait` | Outreach, Waiting for a reply | |
| `#/outreach?tab=nurture` | `#/qualification?tab=parked` | Qualification, Parked | Design puts Parked under Qualification (D2) |
| `#/outreach?tab=talking` | `#/qualification?tab=active` | Qualification, Active | Already only a pointer today |
| `#/qualification?tab=active` | `#/qualification?tab=active` | Qualification, Active (reply handling) | |
| `#/qualification?tab=approval` | `#/qualification?tab=approval` | Qualification, Awaiting approval (pack review) | |
| `#/qualification?tab=qualification` | `#/qualification?tab=qualified` | Qualification, Qualified | |
| `#/qualification?tab=closed` | `#/companies?tab=aside` | Set aside | Closed leads join Set aside, with the reason (D2) |
| `#/agents` (cards, suggestions, records) | `#/agents` | `Agents.dc.html` Agent hub | Agent proposals move to Inbox; records stay as tabs |
| `#/agents?agent=…` | `#/agents?agent=…` | (no design) | Restyled, kept |
| `#/agents?tab=controls` permissions | `#/connectors?tab=permissions` | `Connectors.dc.html` | |
| (none) | `#/connectors?tab=systems` | Connectors, Connected systems | New |
| (none) | `#/library` | `Library.dc.html` | New |
| `#/settings` | `#/settings` | `Settings.dc.html` | Tabs already match: People, Scoring, Email standard, Outreach playbook |
| (none) | `#/record?id=…` | `Record.dc.html` | D4 |

`links.js` changes with the new tabs: `pageFor(stage)` keeps the stage → page rule, `linkFor(o)` uses the new tab ids,
and `nurture` goes to Qualification Parked.

## 3. Every design element, with its source

### 3.1 Frame (every page)

| Element | Source |
|---|---|
| Side rail, groups Work / Agents / footer | Static, `shell.js` `NAV` |
| Rail collapse | `localStorage` key `cgs-rail-collapsed`, as in the prototype |
| Inbox badge | Derived: `GET /api/todo` `count`, the same number the Inbox page lists under All |
| Other rail badges (Companies, Outreach, Qualification) | Design has none; the current per-page counts move into each page's tabs. The rail keeps only the Inbox badge (one place for "needs you") |
| Search (Ctrl K): companies and people | Derived: `GET /api/opportunities` (company, contact, stage) and `GET /api/candidates?status=new`, filtered in the page; pages are static |
| "Synthetic data" chip | `GET /api/config` `demoData` (as `modeBadge` today) |
| User block (name, role) | `ctx.me`, `GET /api/me` `admin`; role from `GET /api/people` |
| Activity button and drawer | `updates.js` history (`GET /api/updates`), "N new" = `updates.unread()`. Replaces the bell |
| "Mark all read" | `updates.markRead()` |
| "See the past week" | The drawer already holds a week; the link scrolls it. No new data |
| Toast with Undo | `undo.js` (10 seconds), styled as the design's dark toast |
| Problem banners (AI limit, Gmail off, research failed) | `GET /api/todo` `health` and `problem` rows; R8 |

### 3.2 Overview (`Main.dc.html`)

| Element | Source |
|---|---|
| Subtitle "Monday 26 October. Nine things need you, and two agents are working." | `ctx.today`; `todo.count`; running agent jobs from the dock state |
| Team avatars | `GET /api/users` |
| Find companies button | Link |
| Outreach results: reply rate, "7 replies to 22 emails", the 4 weeks before | **Gap G4.** No endpoint counts emails sent and replies per period. The scorecard (`GET /api/manager/scorecard`) has a reply rate per contact-plan approach only |
| Reply rate by route (warm / cold) | **Partly**: scorecard `plans` per approach (`warm`, `value_first`, `direct`) with `replyRate`. Plans only, not single emails. Show it labelled "Contact plans by approach" or leave out (G4) |
| What replies said (3 want to meet, 2 not now …) | Derived: `replyLabel` on opportunities in Active, Awaiting approval, Nurture. Period filter is a gap; count current leads instead |
| Pipeline by stage | Derived: `GET /api/candidates?status=new` (Found), `GET /api/opportunities` grouped by stage. Bars show count only |
| Needs you column (2 cards + "N more in Inbox") | `GET /api/todo` rows, first two by group order |
| Agents working column | `GET /api/agents` `research.running`, `GET /api/agents/jobs?status=running`. "Step 3 of 4" is G3 |
| Waiting on others column | Derived: emails in second approval written by me (`outbound` status `approval`, `by` = me; **needs a read**, see G9), opportunities in Contacted/plan running with no reply |
| Done this week | Derived: opportunities that reached Qualified this week (`decisions` step 8 Approved, needs `GET /api/opportunities/{id}`) — **partly gap G5**: no list endpoint for "settled this week" |

### 3.3 Inbox (`Inbox.dc.html`)

`GET /api/todo` rows carry `group, label, title, sub, opportunity, open, ref, act, key`. Tab mapping:

| Design tab | Todo rows |
|---|---|
| Approvals | `Approve email` (second approval), `Approve or return` (pack), `Answer the approver` |
| Company decisions | `Accept or reject` (review), `Decide the contact plan`, `Decide: nurture?` |
| Agent proposals | group `steer` (candidates, direction change, lesson) and group `check` (new facts, contact found, evidence, next actions, manager note) |
| (not in the design) | group `problem` and the remaining `waiting`/`decide` rows (client replied, overdue, follow up, plan step, send for approval, review and send) |
| Done | **Gap G6** |

The design has no tab for problems, client replies or due steps, yet the Inbox must list everything that waits for the
person (Counts rule: the rail badge equals the list). Proposal: All lists every row; the three typed tabs filter; a
fourth filter "Other" is not added, these rows show only under All. The rail badge and the All count are `todo.count`.

Quick actions per row type:

| Row | Quick actions in the design | What the API allows |
|---|---|---|
| Approve email (second approval) | Approve and send, Return to X | `POST /api/outbound/{id}/second-approval` — but the row has no `ref` with the email id (**G9**). Return needs a note: the form opens in the detail panel |
| Approve or return (pack) | Ask X to resubmit, Approve with the changed estimate | `POST /api/packs/{id}/decision` needs the pack id and a role. Not on the row (**G9**). Proposal: no quick action, only "Open the pack" (one place to decide) |
| Accept or reject (brief) | Send back as suggested, Accept and plan contact, Reject | Accept needs owner, the duplicate confirmation and a reason; reject needs a reason. Proposal: quick action only for the agent's suggestion when it needs no form (Send back with no questions); otherwise "Read the full brief" |
| Candidates | Pick companies (link), Skip this round | Link only; "Skip this round" has no endpoint (**G7**) |
| Direction change | Apply, Dismiss | `POST /api/direction/proposals/{id}/{approve,reject}`. The row has no `ref` with the proposal id (**G9**) |
| Lesson | Approve lesson, Reject | `POST /api/lessons/{id}/decision`. No `ref` with the lesson id (**G9**) |
| Agent inbox items (`ref.type` inbox) | Done / Not useful | `POST /api/inbox/{id}` with `action` and `payload_hash` (works today) |
| Failed email (`ref.type` email) | Checked | `POST /api/outbound/{id}/checked` (works today) |

Keys J/K/E: page-side only. Undo after a quick action: only where the backend has an undo (Step 4 accept/reject,
email send window). Other quick actions show a plain toast without Undo.

### 3.4 Companies stage bar and the Insightly line

| Stage | Count source | Insightly line (from `config/crm.json`) |
|---|---|---|
| Found | `GET /api/candidates?status=new` | Not in Insightly |
| Researching | opportunities `stage=researching` | Not in Insightly |
| Your decision | `stage=review` | Lead created on Accept |
| Identified | `stage=identified` (+ `contacted` in the table, see below) | Insightly: Identified |
| Set aside | candidates `status=skipped` + opportunities `rejected`, `closed` | Insightly: Disqualified (rejected and closed leads); skipped candidates were never in Insightly |

The design's Identified table also lists leads already contacted ("Contacted, day 3 of 12"). Proposal: Identified tab
lists `identified` and `contacted` with the stage as a chip (Insightly: Identified / Contacted), so the count equals the
rows.

Qualification and Outreach use tabs, with these Insightly lines where the design shows one:

| Tab | Insightly |
|---|---|
| Active | Insightly: Active |
| Awaiting approval | Insightly: Active (becomes an opportunity on approval) |
| Qualified | Insightly: opportunity, stage Qualification |
| Parked | Insightly: Nurture |

### 3.5 Companies screens

**Found** (table with bulk actions)

| Element | Source |
|---|---|
| Company, sub line | `candidate.company`, `domain`. Industry and city: **gap G8** (not on candidates) |
| Why it matched + source | `candidate.why` / `signal`, `signalDate`, `sourceUrl` (through `safeUrl`), `quote` |
| From search | `candidate.round` ("Round 4"). The search's name: **G8** |
| Fit (Strong / Good / Partial) | **G8**. Left out |
| Found (date or "New today") | `round` only. Date: **G8** |
| Bulk Request briefs | `POST /api/candidates/{id}/pick` per row |
| Bulk Set aside | `POST /api/candidates/{id}/skip` per row; needs one reason and scope from `options.js SKIP_REASONS` / `SCOPES` for the batch |
| Side panel: why it matched, fit checks, "what a brief adds" | Why: as above. Fit checks: **G8**. The last block is static copy |

**Researching**

| Element | Source |
|---|---|
| Now (current activity) | Last `research.log` line; `research.status`, `pauseReason`, `error` |
| Progress "Step N of 4" and the four named steps | **G3**. Proposal until then: the log lines as a list, newest last, with the status dot |
| Started | **Gap** (`ResearchTask.started_at` exists in the model but is not in the view; small backend addition, G3) |
| Try again / Stop research / Resume | `POST /api/opportunities/{id}/research/{retry,stop,resume}`; the site form for a missing domain stays |

**Your decision** (full-page brief review)

| Element | Source |
|---|---|
| Queue (Ready to decide) | `GET /api/opportunities?stage=review` |
| Queue sub line "Agent: send back" | `brief.recommendation.decision` (accept / return / reject) |
| What changed since last time | **G1**. Left out |
| Name, sub, domain link | `company.name`, `company.industry`, `company.hq`, `company.domain` (through `safeUrl`) |
| Research agent suggests: title and reason | `brief.recommendation.decision`, `.reason`, `.basis`; `brief.suggested` |
| Confidence | `brief.priority.confidence` |
| "N of 6 priority conditions met", Missing … | **G2**. Proposal: show the four real priority cells (Authority, Relationship, Fit, and revenue tier left out) as chips with their words, no count |
| Issues chip ("1 conflict, 3 unknowns") | Derived: `brief.flags.length`, `brief.unknowns.length` |
| Accept / Send back / Reject, suggested one primary | `POST /api/opportunities/{id}/step4`; the suggested button from `recommendation.decision` |
| Accept form: owner, rating, next step, duplicate tick | As today's accept form (owner from `/api/users`, `ACCEPT_REASONS`, the duplicate confirmation that follows `GET /api/opportunities/{id}/crm`) |
| Send back form: ticked questions + note | `step4` return with the ticked `brief.unknowns` and flags |
| Reject form: reason, "Just this company / Companies like this" | `REJECT_REASONS` from `options.js` (not the design's list; the backend validates them), scope → `opportunity` / `theme` |
| Settled band with Next brief and Undo | `POST …/step4/undo` within the 10-second window |
| Fold: What we don't know (tickable) | `brief.unknowns`, `brief.flags` |
| Fold: The case | `brief.hypothesis`, `brief.relevance`, `brief.alternative` |
| Fold: Sources and quotes, grouped | `brief.facts` (fact, quote, source, date, url). Grouping by "The trigger / The buyer / The company": only if facts carry a category; otherwise one list (check in R2) |
| Fold: Who to contact | `brief.contacts` (up to 3, with source; LinkedIn search link as today) |
| Fold: Checks | Insightly duplicate check (`GET …/crm`), other CGS work (`GET …/related`), fits the search (`brief.flags`) |
| Fold: Priority conditions | **G2**; interim as above |
| Open all / Fold all, J/K/A/S/R | Page-side; fold state kept per brief in module state |
| `open=<id>` preselects | URL param (today `id=`; both accepted) |

**Identified** and **Set aside** tables with side panel

| Element | Source |
|---|---|
| Identified: contact, owner, "Contacted, day 3 of 12" | `contact`, `owner`, `stage`; plan position from `outreach.plan` (`steps[].day`, `current`). Day count today: derived from plan start and `ctx.today`, only when a plan runs |
| Set aside: reason, by, when | Candidates: `skipReason`, `skipScope` (by and when: **gap**, not on the candidate view). Opportunities: last `decisions` entry (step 4 Rejected or step 8 Closed: `by`, `on`, `note`) |
| Bring back to Found | Candidates: `POST /api/candidates/{id}/unskip`. Rejected opportunities: no endpoint (**G12**) |

### 3.6 Outreach

| Element | Source |
|---|---|
| Mine / Whole team | `team=1` param (exists) |
| Needs you | Agent drafts for me (`GET /api/inbox?owner=` kind `draft`), emails waiting for my second approval (G9 for listing them; today they come through todo rows), plan decisions, plan steps due, plans that ended with no reply |
| With a teammate | Derived: my emails waiting for someone's second approval (`outreach.waiting` with status `approval`), and in team view the drafts owned by others |
| Agent drafting | Derived: running plans whose next step is not prepared yet (`outreach.plan` `current` step, `prepared` empty) |
| Waiting for a reply | `stage=contacted` with no reply |
| Table columns: next step, when, route, plan dots, status | Plan from `outreach.plan.steps` (`day`, `channel`, `status`); route from plan `approach`; when from step `due` |
| Queue: why this company, from the brief | `brief.hypothesis` (first sentence) |
| Earlier messages to the contact | Plan steps done (`steps[].doneOn`, channel, purpose) and `outreach.sentOn` / `subject`. Message text per step: partly |
| Draft: From, To, Subject, body | Inbox item `data` (subject, body, to), sender from `GET …/outreach/email?to=` |
| Click-to-edit (own drafts), word count | `PUT /api/inbox/{id}/draft` with `payload_hash` (exists); word count page-side |
| Restore the agent's draft | `data.original` on the inbox item (exists); restoring is a `PUT …/draft` with the original text |
| Source phrases highlighted in the draft | **Gap G13.** Drafts carry no phrase-to-source map. Facts exist in the brief, but matching them to draft text would be a guess. Left out |
| CGS email standard, score "of 5", checks | Inbox item `data.review`: per-criterion scores (Opening, Insight, Specific, Ask, Voice), weighted mean, fixes; standard from `GET /api/email-rubric`. The design's five yes/no checks (Purpose in line one, One clear ask, Current facts cited, Length, Prices or promises) are **not** what the reviewer returns. Proposal: show the real criteria with their 1-5 scores, the weighted mean "of 5", and Length (page-side, against `rubric.length`). "Not reviewed" when the review failed |
| Where this sits in the plan, "Day 3 of 12", if no reply … | `outreach.plan.steps` (`day`, `channel`, `status`, `due`), `current`. Plan length = last step's `day`. Derived, no gap when a plan runs; leads without a plan show the single first email |
| Approver line ("Alex approves after you") | `GET /api/people` `secondApprover` for the writer |
| Actions: Send to X for approval / Approve and send / Return with a comment / Ask the agent to revise (Shorter, Warmer, More direct) | Approve: `POST /api/inbox/{id}` `action: send` with fingerprint check (exists); second approval endpoint; revise: **check in R3** whether the inbox draft can be sent back to the agent with a note (the plan has `.../plan/decision` with "ask for another"; a single draft redo endpoint is not confirmed, G14) |
| Plan ended: Try another contact / Try again in 3 months / Set aside | **G15.** No endpoints for "another contact" or "restart in 3 months". Set aside = nurture or close. Interim: Park (nurture with a review date) and Close |
| Skip this step / Pause the plan | `POST …/plan/steps/{n}` (record done or skipped) and `POST …/plan/stop` (needs a reason). "Pause" is stop: wording "Stop the plan" unless a pause endpoint is added |
| Gmail disconnected flags | `GET /api/todo` `health`, `GET /api/people` mailbox state (R8) |

### 3.7 Qualification

**Active** (reply handling on one screen)

| Element | Source |
|---|---|
| Conversations queue | `stage=active` (mine or team) |
| "N of 6 conditions met. Missing …" | `conditions` (six keys) and `assessment.gaps` |
| Latest reply, when | `outreach.replyText`, `replyOn`, or the newest note of type Email |
| Earlier in this conversation | Plan steps done, `notes` (call notes), `outreach` sent |
| Add call notes | `POST …/notes` (exists; file upload of .vtt/.srt stays) |
| 1. What the reply means | `replyLabel`; changing it: check in R4 which endpoint sets the label (G16 if none) |
| Not now → Park until date | `POST …/nurture` with review date |
| Not interested → Set aside | `POST …/close` with a reason from `CLOSE_REASONS` |
| 2. The six conditions with state | `conditions` (true / false / null) + `proposals` (`kind` condition, `key`, `status` pending) + `evidence` (`by`, `how`) |
| Confirm / Not quite with reason (Too weak, Wrong person, Out of date) | `POST /api/proposals/{id}` accept / reject with a reason (reasons are free text; the three chips fill it) |
| Hover a proposal highlights the phrase in the reply | Derived: `proposal.quote` is checked to appear in the note text (`BACKEND_V2.md`: a quote not in the notes is thrown away), so the page can find it by exact match and highlight it. If the quote is not found verbatim, no highlight |
| Ask about this in the reply | Derived: the rules' question per gap (`assessment.gaps[].question`) appended to the reply text |
| 3. Your reply (drafted by the Outreach agent) | Inbox item kind `draft` for this opportunity (follow-up) if one exists; otherwise an empty box. Sending: `POST /api/inbox/{id}` `send`, or `POST …/outreach/email` |
| Redraft | **G14** as above |
| Send the pack for approval (6 of 6) | `POST …/packs` when `assessment.readiness == "Ready"` (not merely 6 of 6, see D3) |
| Exception: "Ask X to approve with N conditions missing" + one-line reason | `POST …/packs` with `exception`. Approver name **G10**; 140 characters page-side (**G11**) |
| Not my lead: read-only text | `owner` ≠ `ctx.me` |

**Awaiting approval** (pack review)

| Element | Source |
|---|---|
| Packs queue | `stage=approval` |
| Conditions, Service, Next step | Pack `snapshot` (conditions, need.service, next step) |
| Revenue estimate "Changed 24 Oct, kept in Insightly" | Pack `staleReason` and `activity`; the amount is never shown. Link: `crm.leadUrl` / `opportunityUrl` through `safeUrl` |
| "Changed since X sent it: N things" | Pack `status` stale + `staleReason`. The count of changes and the exact time: **partly** (one reason string). Show the reason |
| Six conditions with proof, source, who confirmed | `evidence` (quote, how, by, on) per condition key |
| Approve / Ask X to resubmit / Return with a comment | `POST /api/packs/{id}/decision` approve / return (with `questions`). An out-of-date pack cannot be approved (backend rule): the Approve button is replaced by the resubmit request, which is the design's "Ask Alex to resubmit" |
| Role (Lead owner / Another consultant / Director) | `APPROVER_ROLES` in `options.js`; not in the design, kept as a small select |

**Qualified**: `stage=qualification`, service from `need.service`, date from the step 8 decision, link to the CRM
opportunity. "Revenue estimate: Kept in Insightly".

**Parked**: `stage=nurture`, reply quote, review date (`snoozedUntil`), owner; Resume now (`POST …/nurture/reconnect`),
Change the date (`POST …/nurture/review`), Not a fit (`POST …/close`).

### 3.8 Find

| Element | Source |
|---|---|
| Ask box with count | `POST /api/candidates/recommend` (`count` 1-10). The design offers 5 / 10 / 20; the backend caps at 10, so the select offers 5 and 10 |
| "Here is how the Discovery agent reads that" confirmation | **Gap G17**: no dry-run endpoint that returns the interpretation and cost. Interim: one confirm line with the text and the count |
| Run and repeat every Monday | `POST /api/discovery/searches` (exists) |
| Already have a company in mind? | `POST /api/opportunities` (exists) |
| Searches table: schedule, count, last run, result, Run now, switch | `GET/PATCH /api/discovery/searches` (frequencies from `sources.js FREQS`, which the backend validates; the design's "Every day at 07:00" wording maps onto them). "Run now" per search: check in R6 |
| AI requests used today | `GET /api/agents` budget fields |
| Research direction, versions, proposal card | `GET /api/direction` (current, history), pending proposals |
| What searches taught: signal, found, accepted, rate | `GET /api/discovery/memory` (signals and searches that led to accepted leads). The "What the agent does with it" column: **partly**, only if memory says it |

### 3.9 Agent hub, Library, Connectors, Settings

| Screen | Supported | Gap |
|---|---|---|
| Agent hub: agents by stage with switches, state, what each does now | `GET /api/agents`, `POST /api/agents/pause`, `PATCH /api/agents` (all), `team.js agentState` | The per-stage metric ("Briefs you accepted 8 of 10"): scorecard has acceptance per kind of work, mapping to check in R6 |
| Agent hub: manager agent card, AI requests today, proposals count | `GET /api/agents`, manager mode, budget; proposals = todo steer rows | "Actions followed 9 of 11": `GET /api/manager/compare` partly |
| Agent hub: Workflows tab (6 cards with switches and run counts) | — | **G18**: no workflow objects or per-workflow switches. Left out |
| Library: search what agents can quote | `GET /api/knowledge?q=` (passages, `librarySize`, status) | |
| Library: totals by source, Drive documents | `librarySize`; `GET /api/drive` (Drive documents) | Documents table for non-Drive sources, passages per document, "quoted in emails", per-document "agents may quote" switch: **G19** |
| Library: Sync from Drive | `POST /api/drive/sync` | |
| Connectors: connected systems, status, last used | `GET /api/toolkit`, `GET /api/toolkit/usage`, `GET /api/crm/state`, `GET /api/people` (Gmail) | |
| Connectors: agent permissions matrix | `GET /api/toolkit/permissions`, `PUT …/{agent}/{connector}` with reason, history and undo | The design's graded levels ("Search only", "Read and quote in drafts") are on/off in the backend. Show on / off / not built / forbidden as the backend states them |
| Settings: People, second approval per person, Act as | `GET /api/people`, `PUT /api/people/{name}` | |
| Settings: Scoring (admin only) | `GET/PUT /api/scoring` | |
| Settings: Email standard, Outreach playbook | `GET/PUT /api/email-rubric`, `/api/outreach-playbook` | |
| Help | — | No page. Proposal: link to `docs/DEMO_GUIDE.md` served as a static page, or leave out (G20) |

### 3.10 Company record (`Record.dc.html`)

All of it is fed by `GET /api/opportunities/{id}` (brief, notes, proposals, evidence, packs, decisions, activity,
outreach with plan) plus `GET …/crm`. Journey bar: stage order and the Insightly line from 3.4. Next step band: links to
the review screen for the stage (`links.js`), no decisions. Tabs: Brief, Outreach, Conversation, Qualification,
Activity. Gaps inherited: G1, G2, G13.

## 4. Decisions

Accepted on 13 Nov 2026 as proposed below (D3: the Ready rules stay as they are; at 6 of 6 but not Ready the page says
"The evidence is not strong enough yet for approval" and names the dimensions in words). R1 and R2 are built.

**D1. Where the header items go.**

| Item | Proposal |
|---|---|
| Person picker | Rail footer, in the design's user block (avatar, name, role). For an admin the block is a button that opens a small "Act as" list; while acting as someone else the block has an orange ring and says "Acting as Sam Reyes". Collapsed rail: avatar only, with the full text in its title |
| CRM sync status | Rail footer, one line above the user block: dot + "Insightly synced 15:02" (or "3 waiting", "1 failed"), linking to Connectors. Collapsed rail: the dot only. A failed sync also shows as a problem row in Inbox (it already does) |
| Bell (notification history) | Becomes the design's Activity button and drawer, bottom right. Same data and unread count |
| Agent dock | Next to Activity, bottom right: a pill "2 agents working" that opens the "Agents now" panel. Same counts as today. (Alternative: a section at the top of the Activity drawer, which saves a button but hides the running count) |
| Synthetic / Live data badge | Rail footer chip, as in the design |

**D2. Nurture and Closed.**
Rename Nurture to **Parked** (label only; backend stays `nurture`, Insightly stays "Nurture") and move its list from
Outreach to Qualification, as the design does. Closed leads join **Set aside** under Companies with their close
reason, since the design has no Closed tab.

**D3. Wording when the gap is not a condition.**
The rules can say "not Ready" at 6 of 6 for three reasons: a BANTHE dimension is unconfirmed or below its minimum,
confirmed coverage is under the threshold, or confirmed Q is under the threshold. Proposal, never showing a number:

- Dimension gaps are named in words: "Budget: not confirmed yet", "Authority: not strong enough yet". These are the
  rules' own gap lines without the letters.
- A gap that is only Q or coverage reads: **"The evidence is not strong enough yet for approval. Confirm more about
  budget, authority or timing."**, listing the dimensions with the lowest or no confirmed score.
- The headline stays "N of 6 conditions met"; under it, when all six are met but the rules do not say Ready: "All six
  conditions are met. The evidence is not strong enough yet for approval."
- The exception button counts what is missing: "Ask for approval with 2 things missing" when the gaps are not all
  conditions.

**D4. The company record.**
Build it, as R7. The search palette, the Overview cards and the pack review link to "Open full record", and every
field it shows already comes from `GET /api/opportunities/{id}`. It makes no decisions, so it adds no new write paths.

**D5. Revenue entry (new).**
The page lets a person enter the CGS revenue estimate today, and the rules use it for priority. Proposal: keep the
entry form in Qualification but never redisplay the amount: after saving it reads "Revenue estimate recorded, changed
<date>". If CGS keeps the estimate only in Insightly, the form goes and the rules read it from the CRM read-back (a
backend change, not in this plan).

**D6. Backend additions (new; your agreement needed before any is made).**
None is required to start R1-R4. These small read-side additions would close gaps without inventing data:

| Id | Addition | Closes |
|---|---|---|
| B-a | Add `ref` with the record id to todo rows for second-approval emails, packs, direction proposals and lessons | G9 (Inbox quick actions) |
| B-b | `started_at` and a fixed step number on research tasks (live research reports which of its stages it is in) | G3 |
| B-c | Expose the director list (or the approver for a pack) in `GET /api/config` | G10 |
| B-d | `exception` limited to 140 characters on the server | G11 |
| B-e | Keep earlier brief versions (or a "changed" summary when a returned brief comes back) | G1 |

## 5. Phases, adjusted

Each phase ends with the checks in section 7 and a report; the next one starts on your go.

**R1. Tokens and the frame.**
Geist (Google Fonts, system fallback) and the design's colours as tokens in the `:root` block of `styles.css`; no
colour literals elsewhere. The side rail (collapse in `localStorage`, Inbox badge = `todo.count`), the D1 placements,
the bottom-right Activity and agents cluster, page headers, cards, chips, buttons, tabs, the segmented control, the
stage bar component, toasts. New routes with redirects from the old ones (`home` → `overview`, `sources` → `find`)
so links in notifications and tests keep working. Every page keeps its current content, restyled. Stage labels
change in `STAGE_LABELS` (and Nurture per D2). `docs/FRONTEND.md` notes the new look.

**R2. Companies.** As specified, with the interim choices above for G1, G2, G3, G8. Closed leads in Set aside (D2).

**R3. Outreach.** Tabs Needs you / With a teammate / Agent drafting / Waiting for a reply; the three-column queue with
the real email-standard scores; plan position from `outreach.plan`. Nurture leaves Outreach (D2). G13, G14, G15 left
out or with the interim actions. *Built:* five stages (Parked stays in Outreach until R4 moves it); G13 turned out to be
supported; G14 only for the first email.

**R4. Qualification.** Active reply handling, pack review, Qualified, Parked; D3 wording; exception with a page-side
140-character limit; revenue per D5. *Built:* Parked moved from Outreach; G16 confirmed (read-only label); new gap G21
(no sending once Active); below 1280px the Active right column sits under the reply.

**R5. Overview and Inbox.** Inbox from `/api/todo` with the tab mapping in 3.3; quick actions only where 3.3 says the
API allows them (more after B-a, if agreed). Overview columns; outreach results limited to what the scorecard has (G4).
*Built:* G9 partly closed without a backend change: the row keys already carry the email, direction proposal and
lesson ids, so those rows have quick actions; packs and briefs still open their page.

**R6. Find, Agent hub, Library, Connectors, Settings.** Only what section 3.8-3.9 marks supported. *Built:* the
Agent hub stage metric uses the scorecard's results used (and confirmed evidence for Qualify); Connectors takes the
permissions and CRM tabs from the old Agents records; Library shows totals, search and Drive documents (G19 stays);
Find's confirm step repeats the request (G17 stays); Workflows left out (G18); Help left out (G20).

**R7. Company record** (D4). *Built:* `#/record?id=…`, read only; Search opens it for companies in the workflow,
every side panel and the pack review link to it ("Open full record"); Overview cards keep opening the stage page, whose
side panel has the link. G1 (brief versions) stays: the Brief tab shows the one brief and the date it was frozen.

**R8. Problem states and wrap-up.** Banners for AI limit, research failed, Gmail disconnected, with the affected rows
flagged (from `todo.health` and problem rows). Then the search for scores, revenue amounts and old labels on screen;
`docs/FRONTEND.md`, `tests/v2/test_frontend.py`, `tools/a11y_audit.py` (new routes); the end-to-end test. *Built:* banners
under the page title from the last `GET /api/todo` (`shell.js problems`): AI limit on Companies, Find, Outreach and
Qualification (Not now for the day); Gmail signed out on Outreach, Qualification and the record, with Outreach rows
flagged; research stopped on Companies. The sweep renamed the last old names in copy (the bell, Ready to decide, New
candidates, Sources page, To approve) and widened the amount mask to the raw estimate a revenue change records.
Settings > Scoring keeps "out of 100": it sets the rules' thresholds, it is not a company's score. The audit checks
the banners with stubbed problems. New gap G22.

## 6. Gaps

| Id | What the design shows | Why it is missing | Until then |
|---|---|---|---|
| G1 | What changed since last time | One brief per opportunity, no versions | Left out |
| G2 | N of 6 priority conditions, Met/Missing | `brief.priority` has four cells with words, not six met/missing | The real cells, no count |
| G3 | Research Step N of 4, Started | `progress` is not updated by live runs; `started_at` not in the view | Log lines |
| G4 | Reply rate, replies per period, by route | No counts per period; scorecard has per-plan-approach rates only | Plan approaches only, or left out |
| G5 | Done this week (Overview) | No list of what was settled this week | Qualified this week only, derived |
| G6 | Inbox Done tab with Undo | Todo lists only open items; settled items are not kept as one list | Agent items done/dismissed (`GET /api/inbox?status=done`) + this browser's own actions; Undo only inside undo windows |
| G7 | Skip this round of candidates | No endpoint | Left out |
| G8 | Candidate industry, city, fit grade, search name, found date | Not on candidates | Left out; round number shown |
| G9 | Ids for quick actions on several todo rows | Rows lack `ref`; **R5:** the row `key` carries the id for second-approval emails, direction proposals and lessons | Quick actions for those; packs and briefs open their page |
| G10 | Approver's name in "Ask Alex to approve" | Directors not exposed | "Ask for approval with N missing" |
| G11 | 140-character reason, enforced | Server accepts any length | `maxlength` in the page |
| G12 | Bring a rejected opportunity back | No endpoint | Only skipped candidates come back |
| G13 | Source phrases highlighted in drafts | **Closed in R3:** drafts carry citation marks `[[fact|phrase]]` tied to the brief's facts | Highlighted and numbered |
| G14 | Ask the agent to revise a draft (Shorter, Warmer…), Redraft | Supported for the first email (`POST .../outreach/generate` with a comment); an inbox follow-up draft has no redo endpoint | First email: Ask the agent to revise; follow-ups: Edit or Dismiss |
| G15 | Try another contact, Try again in 3 months | No endpoints | Park and Close |
| G16 | Changing what the reply means | **Checked in R4:** no endpoint sets `reply_label` | Read-only label; Not now and Not interested are actions (park, set aside) |
| G17 | How the Discovery agent reads a request, and its cost | No dry run | Simple confirm |
| G18 | Workflows tab | No workflow objects | Left out |
| G19 | Library document table, passages per document, quoted count, per-document switch | Only search and totals | Search and Drive list |
| G20 | Help page | None | Left out or a static guide |
| G21 | Send the reply from the workspace once the client has replied ("Send reply", Redraft) | Email preview and send accept only identified, contacted and nurture leads; the Outreach agent drafts only before the first reply | The reply is written on the page (with the condition questions) and copied into the person's own email |
| G22 | Gmail disconnected for one named person, Tell Alex | Mail health is one flag for the workspace mailbox; no per-person state, no notify endpoint | One banner for the workspace, no person named |
| G23 | Inbox, company decision: the agent's suggestion, three facts, and Send back / Set aside / Accept / Reject as quick actions | **Closed 14 Nov:** the item shows the Research agent's suggestion (the decision screen's wording), why now, contact, sources and open questions from `GET /api/opportunities/{id}` | The choice itself stays on the decision screen: Accept needs an owner, a rating and a next step |
| G24 | Pack review: Approve with the new estimate | The backend refuses to approve a pack that is out of date | Ask for it again, or return it |
| G25 | Find: Up to 20, example requests ("Try"), a schedule per search (daily, Monday, monthly, manual) with an On switch | Up to 20 and per-search schedules are not offered by the page; **Try examples added 14 Nov**, one per signal of the approved direction | As built |
| G26 | Qualification, Active: only the six conditions | **Closed 14 Nov:** values read as the rules' own words (`rules.anchors`), never a number; the minimum reads \"At least: …\". The Ready rules still use the numbers behind them | Lead screens show no dimension number |
| G27 | Overview cards with owner and age; Settings, Add a person | To-do rows carry no date or owner; people come from the server's configuration | Cards without the footer; no Add a person |

## 7. Checks for every phase

- `python -m pytest tests/v2/test_frontend.py`
- `python -m pytest tests/e2e` (needs Playwright; reported as skipped without it)
- From R1, with the server running: `python tools/a11y_audit.py http://127.0.0.1:8800 "Alex Morgan"` (new routes added
  to the audit as they appear)
- The demo (`Start Workspace v2.cmd`, http://127.0.0.1:8800/), clicked through at 1024px and 1440px and compared with
  the design source.

Known test impact: `tests/v2/test_frontend.py` and the end-to-end test name current tab ids and labels ("Ready to
decide", "To approve", "Nurture" and others). Each phase updates the tests for what it renames, so the checks stay
meaningful; the redirects keep old links working.
