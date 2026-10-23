# Explicit research-to-qualification handoff

## Workflow

1. The latest opportunity review must say Worth exploring with sources checked.
2. **Submit for acceptance** creates a pending receipt, not a case.
3. **Review handoff…** exposes hypotheses, sources and local duplicate candidates.
4. Accept with a receiving reviewer, accountable owner, next action, date today or later,
   and an explicit relationship/duplicate-check conclusion. A public contact is optional.
   Return for research or reject with a reason; neither creates a case.
5. **Open qualification case** opens the new **Not contacted** case.
6. If contact is unknown, **Confirm contact…** records a verified name, role and source rationale.
7. Draft generation/editing/copying does not mean sent. After a person sends outreach through
   their normal channel, **Record first outreach…** moves the case to **Contacted**.
8. A saved client reply moves it to **Active** under the inherited policy. Qualification ratings
   and conditions still require separate evidence review.

No mail is sent. Human names are local audit signatures, not production identity authentication.

## Payload and evidence boundary

The frozen opportunity includes company/domain, optional team, independent opportunity ID,
hypotheses, alternatives, evidence, sources/dates, unknowns, public contacts, research priority,
theme/version and feedback history. The case displays this as **Research handoff background**.

The receipt saves handoff ID, source revision, reviewer/time, owner, next action and due date.
Acceptance creates an owned CGS task. This internal task is not a client-agreed next step.
BANTHE, all qualification conditions and expected CGS revenue start unknown. Confirmed evidence
starts empty. Public person/role evidence does not confirm buying authority.

## Deduplication, return and recovery

- One accepted case per opportunity_id. Repeated submissions or acceptances return the existing
  receipt/case and do not overwrite the original owner or create another case.
- Same request_id with changed input conflicts. Lost responses can be retried with the same input.
- Company/domain matches are warnings, not automatic merges. A distinct team/problem may create
  another case after an explicit relationship check. There is no Insightly relationship lookup.
- Changed research feedback blocks acceptance of an old revision. Return it, record new feedback
  and resubmit the newer revision. Prior returned/rejected receipts remain in history.
- Accepted source opportunities are frozen. New research or corrections use a separate opportunity.
- Case, receipt, unique mapping and request record commit in one qualification transaction.
  Research is read and locked, not written. An interrupted uncommitted acceptance rolls back;
  a committed acceptance is recovered through the durable request record.
- Sample reset is disabled while accepted links exist. If a case is deleted externally, its
  receipt remains with case_available=false; the app does not silently recreate it.
- Received cases use system dates for evidence, tasks and reminders. Historical samples retain
  their demo clock; demo/live analysis mode is a separate setting.

## API

Use workspace.py and the same Host/Origin/CSRF checks as RESEARCH_API.md.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | /api/research/opportunities/{id}/handoff | SubmitHandoff: request_id, revision |
| GET | /api/research/handoffs | Inbox and receipt history |
| GET | /api/research/handoffs/{id} | Receipt, frozen snapshot, duplicate candidates |
| POST | /api/research/handoffs/{id}/decision | DecideHandoff |
| POST | /api/cases/{id}/contact | ConfirmContact |
| POST | /api/cases/{id}/contacted | RecordContacted |

DecideHandoff accepts request_id, opportunity_revision, status, reviewer, reason, owner_id,
next_action, due_date, relationship_checked, relationship_note, contact_index. Accept requires
owner/action/date/relationship confirmation; return/reject require reason. Contact index must
refer to a saved public person, or null for unknown. Clients cannot assign case IDs or scores.

Initial writes return 201; idempotent handoff replays return 200. Contact changes use case revision.
The receipt's initial_contact_status permanently records not_contacted at acceptance;
subsequent case contactStatus is updated separately.
