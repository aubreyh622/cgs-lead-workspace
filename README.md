# CGS Lead Workspace

A local integration of the CGS public-research workflow and the qualification prototype.
P0-P4 are approved. P5 adds a verified offline two-round rehearsal and operating handoff.
Follow-up development requires a separate user instruction; stop after the authorized task.

## Desktop interface preview

Open [Modern Sales Workspace v4](design-explorations/CGS%20Modern%20Sales%20Workspace%20v4.html)
in a desktop browser to explore the latest interaction prototype. It uses synthetic records
and demonstrates discovery, evidence review, approvals, agent work items and human handoff.
This standalone HTML preview is separate from the integrated Python application below;
its agent activity and research results are simulated, and session edits reset on refresh.

The repository includes application code, synthetic examples, tests and engineering documentation.
Local credentials, runtime databases and captured research runs are excluded. Client source
documents and meeting transcripts from the parent project folder are not part of this repository.

## Run locally

Use `Check Workspace.cmd` to inspect configuration without provider calls.
Use `Start CGS Workspace.cmd`, then open http://127.0.0.1:8766/ .
Keep the terminal running; Ctrl+C stops the backend.

For the prepared **offline synthetic demonstration**, use **Start Offline Demo.cmd**, then open
http://127.0.0.1:8767/ . No API keys are needed. The banner marks all demo output as fictional;
four research tasks, four opportunities, two received cases and an approved direction change
are prepared in separate databases under `runtime/offline-demo/`. Reopening preserves edits.
Follow [the demo guide](docs/DEMO_GUIDE.md) for the click path and a fresh-session command.

This machine borrows the existing Python 3.12 environment selected by `.local-python`.
No dependencies, keys or databases were copied into that environment. `.local-python`,
`.env` and `runtime/` are ignored. On a new machine, run `Setup.cmd` to create a project
virtual environment and install `requirements.lock`; fresh installation has not been verified.

## Available workflow

1. Open **Lead research**, enter a consultant ID, and discover from the research direction
   or explicitly investigate a known company. The live UI uses research v2.
2. Inspect public facts, quotes, dates, contacts, hypotheses, alternatives and gaps.
3. Save specific opportunities; one company may have several teams or problems.
4. Record human feedback, its scope, reason and optional review time.
5. Submit a worthwhile opportunity for explicit pipeline acceptance.
6. Accept with an owner, next action, due date and relationship check. Unknown contacts are allowed.
7. Work the received case from **Not contacted** through manually recorded outreach and replies.
8. Use **Workflow manager** to inspect metrics, analyze eligible feedback or create a manual proposal.
9. A named human approves or rejects the direction diff. Future tasks use the approved version;
   existing tasks and accepted handoffs retain their snapshots.

Research priority never becomes BANTHE evidence automatically. A draft does not mean sent.
A reply means Active under the inherited policy, not qualified. No email or CRM writes occur.

## Provider configuration

Research uses server-side `GEMINI_API_KEY`, `TAVILY_API_KEY` and `GEMINI_MODEL`.
AI feedback analysis uses Gemini only, with the same server-side Gemini settings.
Qualification uses its separate `OPENAI_API_KEY` / `OPENAI_MODEL` and demo/live setting.
Restart after changing `.env`. Configuration presence does not prove account connectivity.

Provider credentials stay in the ignored local `.env`. Isolated public-web trials test core
connectivity and execution; they do not establish lead quality or CGS acceptance. Offline tests
use explicit controlled providers. Failures never switch
to synthetic results. Manual direction proposals are labeled manual and still require approval.
Current settings preserve their **mock** approval basis; local approval does not imply CGS approval.
The explicitly selected offline entry point uses deterministic adapters; it is never a fallback
for main-workspace provider errors. It demonstrates functional integration, not lead quality.

## Layout and documentation

- `workspace.py`, `backend/workspace_*`: integrated startup, configuration and separate databases.
- `demo.py`, `backend/demo_*`: isolated, credential-free synthetic rehearsal and preparation evidence.
- `lead_agent/`: reused research worker, evidence validation and provider adapter.
- `backend/opportunity_service.py`, `handoff_service.py`: opportunities, feedback and acceptance.
- `backend/orchestrator_service.py`, `theme_store.py`: bounded manager and approved direction memory.
- `server.py`, `backend/agents.py`, `backend/rules.py`, `dist/`: inherited qualification app plus integration.
- `config/`: import baseline; approved direction versions are persisted in the research database.
- `app.py`: historical Streamlit test entry, not the integrated application.
- `docs/source_manifest.json`: upstream baselines and documented local adaptations.

Read [the phase plan](docs/DEVELOPMENT_PLAN.md), [data contracts](docs/DATA_CONTRACTS.md),
[research API](docs/RESEARCH_API.md), [review guide](docs/RESEARCH_REVIEW.md),
[handoff guide](docs/HANDOFF.md), and [orchestrator guide](docs/ORCHESTRATOR.md).
P5 results are in [functional acceptance](docs/PILOT_ACCEPTANCE.md) and
[verification](docs/VERIFICATION_P5.md). Use [operations handoff](docs/OPERATIONS_HANDOFF.md)
and the [real pilot checklist](docs/LIVE_PILOT_CHECKLIST.md) before a client trial.
Discovery execution constraints and trial evidence are documented in
[discovery verification](docs/VERIFICATION_DISCOVERY_GUARDS.md). The current integrated
direction-led workflow is documented in [Research v2](docs/RESEARCH_V2.md), with measured
results in [v2 verification](docs/VERIFICATION_RESEARCH_V2.md). CGS client acceptance remains unverified.

On this machine, `Start Live Research Results.cmd` opens isolated captured public-web
results on port 8768, including a Reckitt brief pending human review. It does not automatically
start research. Qualification retains its clearly marked sample cases. The original main
workspace databases and offline synthetic demo remain separate.

Legacy staged core runs use `lead_agent.discovery.new_discovery_payload` and the existing Worker.
They persist a candidate queue and advance through company verification, assessment and guarded
saving. See [staged discovery](docs/STAGED_DISCOVERY.md) for the entry point, stages and boundaries.

Maintained engineering documentation, comments and UI copy use English. Upstream originals in
`docs/upstream/` remain unmodified source archives, including the original Chinese demo guide.
