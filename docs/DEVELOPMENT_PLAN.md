# Local integration phases

The user authorizes one phase at a time. Complete the authorized phase, report, and stop.
P0-P5 implementation is complete. Follow-up tasks require their own user authorization.
The current user-authorized follow-up is a working research/manager redesign: small typed
research steps, substantive evidence review, targeted retrieval, integrated direction-led
discovery, feedback context and bounded real verification. Keep the existing providers and
human decisions. Report the completed work and measured limitations, then stop.
All implementation changes are confined to `cgs-lead-workspace`.

| Phase | Scope | Status |
| --- | --- | --- |
| P0 | Source baseline, contracts, separate stores and startup | Complete; approved |
| P1 | Bounded single-company research service and HTTP API | Complete; approved |
| P2 | Research UI, sources, opportunity snapshots and human feedback | Complete; approved |
| P3 | Explicit qualification acceptance, unknown contacts and deduplication | Complete; approved |
| P4 | Feedback manager, proposals, human decisions, versions and next-task use | Complete; approved |
| P5 | Small two-round integration pilot, acceptance and demonstration handover | Offline functional rehearsal complete; awaiting user review; real CGS pilot pending |

## P0 foundation

- Qualification baseline: xinyu1103lindawang-cpu/cgs-qualification-prototype,
  commit `3a3efbb79796ab87efe626f86c269ddf7006e3e8`; 16 blobs verified.
- Research baseline copied from the adjacent local project, without keys or running databases.
- Separate SQLite files avoid incompatible `runs` table definitions.
- Five initial contracts cover tasks, opportunities, feedback, proposals and handoff receipts.
- Startup/check scripts use port 8766. The qualification baseline includes seven synthetic cases.
- Historical checks: 12 P0 tests and 62 non-UI research tests; see VERIFICATION_P0.md.

## P1 research API

- Reuse Worker, LiveProviders, source binding and company cards.
- One company per task and one active research task; server-owned model, theme and limits.
- Background start, list, status, results, stop and explicit resume.
- Atomic task snapshot / request idempotency / execution lease reservation.
- Resume preserves progress and consumed budgets; interrupted work is not restarted automatically.
- Controlled transport checks cover the real adapter code, not a live account.

## P2 review workflow

- Lead research navigation, independent provider status, source quotes and dates.
- Facts, hypotheses, alternatives, contacts, gaps and research priority remain distinct.
- Human-created opportunity snapshots allow multiple problems or teams at one company.
- Append-only structured feedback records outcome, reason, scope, comment and optional duration.
- Durable request IDs and revisions prevent duplicate writes and stale review overwrites.

## P3 acceptance

- Worth exploring with sources checked can be submitted as pending acceptance.
- Accepted / needs research / rejected are explicit receiving decisions.
- Acceptance requires a reviewer, owner, next action, due date and relationship check.
- Unknown contacts are allowed; new cases start Not contacted with unknown qualification fields.
- Case, receipt, unique opportunity mapping and request record share one qualification transaction.
- Same-company opportunities are warned about, not automatically merged.
- Draft creation, recorded outreach and replies are separate actions; scores still require review.
- Received cases use system dates; historical sample clocks are preserved.
- Accepted source opportunities are frozen; destructive sample resets are blocked while linked cases exist.

## P4 bounded manager

- Persist model-review inputs/results, feedback-backed proposals and approved theme versions.
- Measure the existing workflow with explicit cohorts, denominators, unknowns and synthetic counts.
- Enforce feedback scope: opportunity-only feedback cannot justify a shared direction proposal;
  consultant feedback cannot alter another consultant's direction.
- AI makes at most one model call per review and can only propose whitelisted direction fields.
- Manual proposals are explicit alternatives, never an automatic fallback after a model failure.
- Approval rechecks feedback and base version, then atomically saves a new direction and decision.
- Personal approved snapshots apply only to that consultant; shared theme/CGS changes apply to the
  configured theme's shared direction. This prototype supports one configured theme.
- Future research saves and uses the effective direction in its search and model input.
- No scoring-weight changes, autonomous paid research, unsupervised approvals or P5 acceptance claims.

## P5 functional rehearsal and handover

- Run two companies through two rounds via real local HTTP, production worker/validation and stores.
- Use explicit deterministic provider adapters and isolated databases; no external calls or credentials.
- Verify source provenance, human review, return/reacceptance, duplicate warnings and stage boundaries.
- Verify pending proposal, named approval, restart persistence and actual next-task direction use.
- Provide Start Offline Demo.cmd, an unmistakable synthetic banner, fictional company selectors,
  reusable prepared state and a new-directory option that never resets unrelated files.
- Preserve actual check evidence, an English demo guide, operating handoff and a real pilot checklist.
- Distinguish 27 functional checks from live-provider quality, client acceptance and commercial value.
- Existing P4 verification remains historical; P5 results are in VERIFICATION_P5.md.

## Authorized discovery follow-up

- Keep the existing Gemini model; do not upgrade or silently switch providers.
- Enforce discovery, page retrieval, deterministic evidence validation and saving in that order.
- Reject invalid discovery proposals before SQLite writes; retain gaps in run feedback.
- Preserve seed research placeholders, cache reuse, stop/resume and existing budgets.
- Run offline regressions, then one isolated public-web trial with the previous small-run limits.
- Record elapsed time, actual provider attempts, usage, validation failures and the final shortfall.
- This task does not connect problem discovery to the integrated HTTP/UI or approve any leads.

## Authorized staged discovery follow-up

This section records the completed prior authorization; the current authorization is above.

- Add explicit stages and a persisted candidate queue in the existing research core.
- Ground nominations in discovery inputs; resolve official URLs and retrieve company pages.
- Reuse prepared research prompts, code-bound quotations and strict validation before saving.
- Preserve budgets, source caching, Stop/resume, seed tasks and human approval boundaries.
- Use the previous search snapshot with the unchanged model for one bounded real continuation.
- Separate reused inputs from new calls/time and report all candidate dispositions and failures.
- Do not claim an equal-cost A/B comparison, lead usefulness or CGS acceptance from this pilot.
- Stop after reporting; no additional live round, model upgrade or UI integration is authorized here.

## Frontend phases (v2 workspace UI)

Authorized by the user on 2026-11-05 ("start writing the frontend code"); F2 authorized the same day ("开始"), F3 too ("做F3"), F4 ("开始F4"), F5 ("开始F5") and F6 ("Yes, start F6"). The UI replaces the static
prototype at `frontend/index.html` with pages that read and write the v2 API (`api/`). Each phase is
authorized on its own; F1 approval does not authorize F2.

- Design: `DESIGN.md` (warm neutrals, one orange accent, Inter 400/500, top navigation by kind of work)
  and the "Version 2" screens of the CGS Lead Workspace design canvas, with the 2026-11-04 Hallmark audit
  fixes (Home leads with the queue, no stat tiles; tokens only; no middle-dot strings; field borders 3:1;
  loading, empty and error states for every list).
- Desktop only (user decision, 2026-11-05): no phone or tablet layout. The page has a 1024px minimum width;
  the Hallmark audit's mobile items (375px nav menu, 320px overflow) do not apply.
- Stack: no build step. ES modules and one stylesheet in `frontend/app/`, served by FastAPI at `/app/`.
  Every value from the API is escaped before it reaches the page. Tabs and selections live in the URL.
- No login yet: the person picks their name; every write sends it in `X-User`.
- The page shows only what the backend records. It never invents counts, contacts or suggestions.

| Phase | Scope | Status |
| --- | --- | --- |
| F1 | Shell (navigation, person picker, mode banner, toasts), API client, Home queue, Companies: Step 1 candidates (research, skip with reason), Steps 2-3 research status (stop, resume, retry, website), Step 4 brief review (accept, return with questions, reject with reason), accepted and set-aside lists | Complete; awaiting user review (VERIFICATION_F1.md) |
| F2 | Outreach: agent drafts from the inbox, email preview and approve-and-send, record sent, follow-up, reply and nurture, confirm contact | Complete; awaiting user review (VERIFICATION_F2.md) |
| F3 | Qualification: notes and evidence proposals (accept, edit, reject), manual review, rules result, next actions and tasks, pack submission, Step 8 approval and handover | Complete; awaiting user review (VERIFICATION_F3.md) |
| F4 | Sources (direction, problems, proposals, discovery memory, add a lead, recommend) and Agents (team, task board, runs with traces, permissions, lessons, scorecard, CRM status) | Complete; awaiting user review (VERIFICATION_F4.md) |
| F5 | Accessibility pass, end-to-end browser checks of the whole workflow, documentation, retire the old prototype launchers | Complete; awaiting user review (VERIFICATION_F5.md) |
| F6 | Design fidelity: bring the pages back to the Version 2 design where the backend already has the data. Agents as six agent cards with status, suggestions from agents and an agent detail page; the agent dock on every page; Qualification list with evidence and next columns, accept all, the manager's memory and an approval card; Companies, Outreach and Home details from the design. Items the backend cannot supply yet are listed, not faked | Complete; awaiting user review (VERIFICATION_F6.md) |
