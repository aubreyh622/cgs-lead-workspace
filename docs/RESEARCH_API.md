# Research API

Start with `python workspace.py serve`; default URL: http://127.0.0.1:8766/ .
The existing qualification routes keep their original formats.

## Configuration and requests

Research requires server-side GEMINI_API_KEY, TAVILY_API_KEY and GEMINI_MODEL. Restart after changes.
GET `/api/research/theme?consultant_id=<id>` returns the effective approved Brief, rubric version,
limits and configuration-presence status. Presence does not prove connectivity or quality.
POST requests need JSON and `X-CSRF-Token`, obtained from GET `/api/bootstrap` field `csrf`.
Host and Origin must identify the local workspace.

P5's explicitly selected `demo.py` entry point serves the same routes with isolated databases
and synthetic providers, on port 8767 by default. Its bootstrap adds `offlineDemo: true`;
theme configuration adds `simulated: true` and `demo_companies` choices. Normal theme responses
have an empty `demo_companies` list. Run status includes `mode` (live or synthetic).
Offline requests accept only the exact listed fictional company/domain pairs, all cards retain
synthetic provenance, and qualification cannot switch to Live. No secrets or external calls
are involved. This mode is not an automatic response to main provider failure.

Example POST `/api/research/runs`:

```json
{
  "request_id": "research-local-001",
  "consultant_id": "consultant-local",
  "mode": "company",
  "research_version": "v2",
  "company": {"company": "Caterpillar", "domain": "caterpillar.com"}
}
```

For direction-led discovery, send `mode: "discovery"`, `research_version: "v2"`, and
optional `target` (1 or 2); omit `company`. The live UI defaults to discovery. Existing
clients that omit both new fields retain legacy known-company behavior. The synthetic
offline demo accepts only its named company fixtures.

Theme responses include `discovery_available` and `discovery_limits`. Run status includes
`workflow`, `research_mode`, `stage`, `candidate_queue`, and `briefs_ready`. Results include
`candidate_notes` with model-reviewed, source-bound facts from unfinished candidates, kept
distinct from reviewable `company_cards`.

Optional theme_id and theme_version must match the effective direction. Read the current theme
rather than hardcoding its version. Clients cannot override keys, model, limits or full snapshots.
The server rechecks the approved version inside task creation to catch concurrent approvals.
Approved domain exclusions are checked before a task is created.

## Routes

| Method | Path | Result |
| --- | --- | --- |
| GET | /api/research/theme | Effective Brief, rubric version, limits, provider presence |
| POST | /api/research/runs | Start bounded discovery or company research; 202 new / 200 replay |
| GET | /api/research/runs | Managed task list |
| GET | /api/research/runs/{id} | Status, counters, limits, recent events, error and resume availability |
| GET | /api/research/runs/{id}/results | Run status and company_cards with evidence/sources |
| POST | /api/research/runs/{id}/stop | Empty object; stop at next execution checkpoint |
| POST | /api/research/runs/{id}/resume | Empty object; explicitly resume paused/failed work |

Success: `{ "ok": true, "result": ... }`. Error: `{ "error": "..." }`.
Typical errors: 400 invalid input; 403 Host/Origin/CSRF; 404 missing managed object;
409 stale version, reused ID with changed input, busy or invalid transition; 503 missing configuration.
Background provider errors appear in task status and never generate fake success.

## Execution semantics

An accepted request is not a completed task. Research v2 completes only when the target
count of briefs is Ready for review; legacy passes may still have gaps. Same request_id + same input returns the existing task and
never starts another paid call. Use a new ID for an intentional new research run.
Stop cannot retract an external request already sent. Resume preserves saved sources, completed
steps, model, frozen direction and consumed counters/budgets. Completed/limit tasks cannot resume.
After a process interruption, wait for the 120-second lease to expire, then explicitly resume.
Reading status does not trigger research.

Research v2 limits: 10 searches, 12 extractions, 18 model calls, 22 reserved credits and 480 active seconds.
Legacy limits: 8 searches, 10 extractions, 6 model calls, 18 reserved Tavily credits, 300 active seconds,
4 model requests/minute. Provider billing is authoritative. Source/validation safeguards can stop
work earlier. Direction signals enter bounded searches (at most 500 characters) and the full
Brief enters model assessment. Existing task snapshots do not change after approval.

See RESEARCH_REVIEW.md, HANDOFF.md and ORCHESTRATOR.md for later workflow routes.
