# Development plan implementation status

## Current milestone after review

The first implementation built the surrounding workflow before validating live research.
The revised priority is one company → five real evidence-backed company cards. This is
**implemented and tested with controlled responses; live connection probes passed and
research validation is in progress**.
It is not a completed real-company pilot.

- Main screen: one company or editable five-company batch; readable findings and review.
- No raw JSON configuration or synthetic-pilot loading on the main screen.
- Verified business events can support an investigation hypothesis without an admitted problem.
- Scope is manufacturers with US operations; headquarters can be outside the US.
- Ordinary gaps, missing buyers and one-page evidence are distinct from invalid evidence.
- Each company is processed in sequence, with bounded allowances; unavailable cases remain explicit.
- Research cards can be enriched, preserving prior records/reviews and resetting decisions.
- Private API entry and separate model/search/retrieval diagnostics are ready.
- Model/search/retrieval probes passed with user-entered keys; live testing exposed and
  corrected official-URL routing and free-tier pacing issues.
- Real source quality, five autonomous cards and real cost/latency still require validation.

The user authorized selecting the first capability and using mock governance decisions
while demonstrating a working identification component. The implementation follows that
scope. It uses the existing Python stack rather than introducing a separate TypeScript
service. The original prototype remains in its prior folder.

| Plan step | Implemented and inspectable | Remaining validation |
| --- | --- | --- |
| Problem brief and handoff | Versioned Pydantic/JSON schemas, selected capability, five seed domains, editable brief, anchored rubric, explicit mock approval/ownership | CGS agreement and labelled calibration examples |
| Five known companies | Sequential company research, stored full source context, updateable cards, API diagnostics and simpler review; controlled five-company adapter test | Live model/search smoke test and five autonomous evidence-backed cards |
| Discovery and buyers | Problem-based research loop, 1–30 company targets, exclusions, normalized-domain IDs, nullable current-role evidence | Live 20–30 company discovery and role-accuracy review |
| Prioritization and handoff | Code-computed configurable scoring, coverage, research queue, review actions, JSON/CSV, immutable outbox, local mock consumer with retry deduplication | Teammate contract/transport agreement and CGS rubric calibration |
| Evaluation and stabilization | Shared-budget baseline path, held-out evaluation reporting, bounded retries, request limits, persisted resume and worker lease; 25-record synthetic workflow demonstration | Separate real evaluation batch and measured relevance/time/cost comparison |

The technical prototype can be demonstrated without API keys. Real lead quality and
autonomous provider behavior are not yet validated. No confidential data connector,
outreach, CRM write, cloud deployment or downstream qualification is implemented.

## Prototype decisions

- Capability: AI and digital transformation strategy and first-mile planning, supported
  by CGS's public [services](https://www.cgsadvisors.com/strategic-transformation/) and
  [transformation approach](https://www.cgsadvisors.com/respond-to-disruption/).
- Initial segment: manufacturers with United States operations, regardless of headquarters. This bounded test segment can be changed.
- Signals: adoption/coordination gaps, transformation leadership, and organizational
  changes around merger/divestiture or new products. A signal creates a hypothesis;
  it does not establish buying intent, budget or desire for external support.
- IT, digital and strategy leaders are initial buyer-role hypotheses. Company size and
  authority can inform later calibration without replacing problem evidence or implying
  a deal-value formula. Account potential stays separate from identification priority.
- Missing contacts go to the mock downstream enrichment owner. No email is fabricated.
- Mock approvals and pipeline owner are conspicuously labelled in config, UI and records.
- The new meeting transcripts were read as project context. Their text is not embedded
  in the code or sent to research providers.

## Example artifacts

- `examples/seed_companies.csv`: five selected known-company inputs.
- `examples/public_company_cards.json` and `.csv`: manual public research snapshots.
- `examples/synthetic_pilot.csv`: 25 fictional records with varied evidence/readiness.
- `examples/approved_mock_leads.json`: reviewed fictional handoff records.
- `examples/mock_consumer_receipt.json`: local intake and duplicate-retry result.
- `examples/synthetic_pilot_evaluation.json`: technical fixture metrics only.
