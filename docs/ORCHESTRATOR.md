# P4 workflow manager

The manager coordinates the existing research/review/handoff workflow through durable records,
workflow measures and approval-controlled direction changes. This is a bounded local supervisor,
not an autonomous sales operator or a trained company model. It does not send mail, write to CRM,
change qualification scores or launch paid research on its own.

## Use the feedback loop

1. In a research opportunity, record an outcome, reason, explanatory comment and **feedback scope**.
2. Scroll to **Workflow manager** on Lead research. Select consultant context and feedback scope,
   then **Load manager context**.
3. Inspect KPI definitions, sample counts and the effective proposal-base version.
4. Select eligible feedback (up to 20 latest records):
   - **Analyze feedback with AI** creates a background review, using one Gemini request.
   - **Create manual proposal…** lets a person write a restricted JSON patch and rationale.
     It is labeled manual and is never an automatic substitute for a failed AI request.
5. Read the saved summary, selected feedback, rationale and before/after diff.
6. **Approve / reject proposal…** requires a named reviewer and a decision rationale.
   Pending and rejected proposals do not change research settings.
7. Approval saves a new immutable direction version. Start the next company task with the intended
   consultant ID. The research form reads that consultant's effective theme; old tasks keep their snapshots.
8. Record feedback from that next version before proposing another change. Historical reviews remain
   available for audit, but superseded opinions and older-version feedback cannot authorize a new patch.

The current local baseline is mock. Approval records a local human decision while preserving the
existing approval_basis; it does not upgrade the theme to official CGS approval.

In the explicitly launched P5 offline demo, **Simulate feedback review** replaces the live model
action and is labeled simulated. The deterministic fixture proposes one initial integration
signal change and reports no further change while that signal is already current. It is an
integration demonstration, not evidence that a model learned CGS preferences. Use the demo
guide for a separate interactive manual-proposal cycle.

## Scope and persistent memory

- **opportunity** feedback stays with one opportunity; it is not eligible for direction proposals.
- **theme** feedback can support the configured theme's shared direction.
- **consultant** feedback can support only that same consultant's personal direction.
- **cgs** feedback is explicitly company-level feedback, applied to this prototype's one configured
  theme. It does not silently transfer to other themes or industries.

Personal heads are full approved snapshots. A consultant without a personal head uses the shared
head. Later shared approvals do not silently overwrite an existing personal snapshot. A subsequent
personal proposal is based on that consultant's current personal version.

Memory consists of structured feedback history, frozen model-review input/output, proposals,
decisions, immutable versions, parent links and active heads in SQLite. There is no vector database,
model fine-tuning, automatic preference inference or unbounded conversation memory.

## Allowed changes

The whitelist is `problem`, `signals`, `buyer_roles`, `exclusions`, `excluded_domains`.
The patch must change a value and produce a valid Brief. The server verifies selected feedback
existence, latest status, theme, base version and scope both when creating a proposal and approving it.
Weights, revenue assumptions, industry/geography, models, budgets, code, qualification fields and
approval basis are not patchable through this workflow.

Approval commits the decision, version and current head in one research-database transaction.
Concurrent approvals based on a superseded shared head conflict. Research task creation rechecks
the effective version in its own transaction so a concurrent approval cannot silently create a task
from a stale displayed version. Existing request replays still return their original task.

Approved signals/problem enter bounded public-source searches and the full approved Brief enters
model assessment. A selected domain exclusion blocks a new task before provider calls. Other
exclusions and buyer roles are interpreted by the existing evidence-based research assessment.
Searches retain the existing 500-character maximum. This prototype still researches one supplied
company per task; approving a broader topic does not implement unattended market discovery.

## KPI definitions

The cohort is the configured theme across all versions and consultants, including synthetic data.
Synthetic opportunity count is shown separately. Choosing proposal scope does not narrow the KPI cohort.

| Measure | Definition |
| --- | --- |
| Opportunities | Saved independent opportunity snapshots |
| Reviewed opportunities | Opportunities with at least one feedback record |
| Worth exploring rate | Latest Worth exploring outcomes / reviewed opportunities |
| Feedback records | All recorded feedback in this theme, including history |
| Median review time | Median of non-null durations on latest reviews; show sample count |
| Accepted handoffs | Accepted receipts in this theme |
| Recorded outreach | Linked cases with an explicitly recorded firstContact |
| Recorded replies | Those contacted cases with a recorded lastReply |
| Recorded response rate | Recorded replies / recorded outreach |

Zero denominator and missing durations remain null / Not measured. These are workflow indicators,
not validated lead quality, commercial conversion, model accuracy or proof of business value.
The UI also reports saved research tasks and paused tasks for operator follow-up.

## AI execution boundaries

AI analysis requires server-side GEMINI_API_KEY and GEMINI_MODEL; Tavily is not needed for a
feedback review. Qualification configuration remains separate. Only the selected feedback and
approved Brief are sent to this model call, not full client conversations or credentials.
Feedback text is treated as untrusted data, and local schemas enforce the allowed output.

One active feedback analysis, at most 20 records and one model call per request. A valid model
response may propose no change. Invalid output or provider failure saves a failed review and
does not apply a direction or create synthetic success. No automatic retry or model switch occurs.
Request IDs reserve work durably before the call; replay reads the same record. After a process
interruption, a 120-second expired lease becomes failed; inspect it before explicitly requesting a
new review, since an interrupted provider call may already have been charged.

The UI supports manual proposals without credentials, but real AI account connectivity and output
quality remain unverified in this workspace. Tests exercise the real adapter with controlled transport.

## API

Use the same Host/Origin/CSRF boundaries as RESEARCH_API.md.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | /api/research/orchestrator?consultant_id=...&scope=theme | Metrics, eligible feedback, saved reviews/proposals/versions |
| POST | /api/research/orchestrator/reviews | ReviewFeedback; 202 new, 200 replay |
| GET | /api/research/orchestrator/reviews/{id} | Saved status, model, reserved calls, input, result or error |
| POST | /api/research/orchestrator/proposals | CreateDirectionProposal; 201 new, 200 replay |
| POST | /api/research/orchestrator/proposals/{id}/decision | DecideDirectionProposal; 201 new, 200 replay |

ReviewFeedback fields: request_id, consultant_id, scope, feedback_ids, base_theme_version.
Manual proposal adds patch and rationale. Decision fields: request_id, status, reviewer, reason.
The server generates proposal IDs, timestamps and resulting versions. See exported JSON schemas.

Human names are local signatures, not production identity authentication. P5 pilot acceptance,
real CGS feedback quality, deployment and wider autonomous agent management remain future work.
