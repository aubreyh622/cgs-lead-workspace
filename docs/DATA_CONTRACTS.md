# Workspace data contracts v1.0

Pydantic definitions live in `backend/contracts.py`; request models live beside their services.
JSON schemas are exported to `schemas/workspace/`.

| Object | Main fields | Boundary |
| --- | --- | --- |
| ResearchTask | run/request/consultant IDs, knowledge version, Brief/Rubric snapshots, companies, limits, created_at | Server-built snapshot; no credentials |
| OpportunityBrief | Independent ID, title, optional team, hypotheses, alternatives, evidence IDs, full Lead snapshot | Multiple opportunities per company; unknown contacts allowed |
| ResearchFeedback | Opportunity, consultant, theme/version, outcome, reason, scope, comment, time, optional duration | Append-only; Other needs explanation; unknown duration is null |
| ThemeChangeProposal | Base version, selected feedback, scope, whitelisted patch, rationale, human decision, resulting version | Pending by default; AI never approves itself |
| HandoffReceipt | Opportunity/revision, status, case, reviewer, owner, next action, due date | Submission is not acceptance; initial contact status is not_contacted |

Evidence IDs must belong to the frozen research snapshot. Public facts, business hypotheses,
research priority and confirmed qualification evidence remain separate.

## Persistence

Research database: managed tasks, opportunities, feedback, manager reviews, proposals,
immutable approved direction versions and current direction heads.
Qualification database: cases, existing qualification work, handoff snapshots/receipts,
unique opportunity-to-case mappings and handoff request records.

Acceptance reads and locks the current research revision, then writes only the qualification
transaction. Case, receipt and deduplication records commit together. It does not require a
cross-database write transaction. Approved direction changes similarly commit proposal decision,
version and head in one research transaction.

## Versions and scope

ResearchTask and OpportunityBrief retain full snapshots; changing a head never rewrites old work.
`knowledge_version=brief-only:<brief_id>:<version>` explicitly identifies the available knowledge;
no CGS historical-case database has been connected.

Personal direction heads are full approved snapshots for one consultant. If no personal head
exists, the shared head applies. Later shared changes do not silently overwrite a personal head.
Theme- and CGS-scoped proposals both target the one configured theme's shared head.
The imported config and rubric files are not rewritten by approval.

## Service checks

- Tasks, opportunities, evidence references, feedback targets and handoff receipts must exist.
- Request IDs are persistent and operation-specific; changed input conflicts.
- Review revisions and proposal base versions reject stale changes.
- Only latest feedback in the requested scope and base version can justify approval.
- Consultant-scoped feedback must belong to the specified consultant.
- A direction patch can change only problem, signals, buyer_roles, exclusions, excluded_domains.
- Scoring weights, budgets, models, qualification fields and approval basis cannot be patched.
- Received cases start with null BANTHE/conditions/revenue. Human name/role confirmation is
  not a confirmation of buying authority.
- Local signatures are audit fields, not production authentication or role-based authorization.
