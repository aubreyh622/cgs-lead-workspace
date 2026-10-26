# Research and human review

1. Start the integrated workspace and open **Lead research**.
2. Read the direction, version, approval basis and provider status; configure keys before live research.
3. Enter a consultant ID, company and public domain. Active tasks refresh every two seconds.
4. Inspect facts/quotes/dates, hypotheses, CGS relevance, alternatives, contacts and unknowns.
5. Choose **Save an opportunity…**, refine its title, optional team and hypotheses, and select evidence.
6. Choose **Record human review…** and record outcome, reason, feedback scope, comment and optional duration.
7. Refresh the page to verify persistence; expand review history for the full audit trail.

No research sample is loaded into the actual workspace automatically. Browser-test samples live
only in isolated test databases and are labeled synthetic. Provider failures never load a sample.

| Field | Meaning |
| --- | --- |
| Worth exploring | A research judgment to investigate; not proof of budget, authority or purchase intent |
| Research further | More evidence or problem validation needed |
| Defer | Pause; explain timing, ownership, duplication or mismatch as applicable |
| reason_code | relevant_signal / evidence_gap / service_mismatch / already_owned / timing / duplicate / other |
| scope | opportunity / theme / consultant / cgs; feedback scope is not itself permission to change settings |
| review_minutes | Optional; unknown stays null |
| source_support_checked | Human declaration of checking sources; required for Worth exploring |

Other requires a comment. IDs, theme context and timestamps are supplied or verified server-side.
An opportunity snapshot is not edited or deleted by subsequent research. More opportunities may
refer to the same company. Reviews append history and increment revision; stale writes conflict.
Accepted handoffs freeze the source opportunity; subsequent qualification work belongs in the case.

## API

| Method | Path | Input |
| --- | --- | --- |
| GET | /api/research/opportunities | All opportunity snapshots and feedback |
| GET | /api/research/opportunities/{id} | One opportunity, revision and history |
| POST | /api/research/opportunities | CreateOpportunity |
| POST | /api/research/opportunities/{id}/feedback | ReviewOpportunity |

CreateOpportunity: request_id, run_id, lead_id, title, optional team, problem_hypothesis,
cgs_relevance_hypothesis, alternative_explanation, evidence_ids.
ReviewOpportunity: request_id, revision, consultant_id, outcome, reason_code, scope, comment,
review_minutes, source_support_checked. See the exported schemas for exact validation.
New writes return 201; idempotent replays return 200. Same ID with changed input returns 409.
The browser keeps unacknowledged request IDs in localStorage so retrying a lost response is safe.
Reload does not automatically resend writes; inspect existing records before intentionally creating more.

The handoff summary reports not_requested / pending_acceptance / accepted / needs_research / rejected.
Only explicit acceptance creates a case; see HANDOFF.md.
For P4, only feedback scoped to theme, consultant or cgs can support a direction proposal.
Opportunity-only comments remain local review data; see ORCHESTRATOR.md.
