# Staged research discovery

The staged core turns an approved research direction into a persisted candidate queue, then
investigates one company at a time. Code advances the stages; model responses cannot finish
the run or bypass evidence validation. The existing known-company UI/API and orchestrator
remain separate from this core entry point.

## Stages and records

| Stage | Responsibility | Persisted output |
| --- | --- | --- |
| Signal search / snapshot load | Run two bounded signal queries or load explicit frozen discovery inputs | Search cache, original pages, reuse counts |
| Candidate extraction | Model nominates companies mentioned in the inputs | Company name, exact name excerpt, origin URL/type, investigation hypothesis |
| Candidate queue | Code deduplicates grounded nominations and selects the next company | Stable candidate ID, stage, status, attempts, issues, disposition |
| Official-site resolution | Search for that company; model selects only a returned URL | Proposed official domain and reason, or unresolved outcome |
| Company retrieval | Read the selected site and up to two targeted official search results | Candidate-local source IDs and web counts |
| Assessment | Model analyzes only that candidate's retrieved passage bank | Research record with model-selected quote IDs |
| Validation and save | Code binds actual source text and applies existing deterministic checks | Validated company card pending human review, or explicit issues |
| Repair or next candidate | One bounded source expansion and at most two assessment attempts | Saved, needs research, excluded, duplicate, or unprocessed queue entry |

Discovery hints are nomination material, never Research evidence. Every nomination must name
a company in an exact excerpt from its origin. Publishers and other non-manufacturers marked
by the model are excluded from the pilot queue. The code also rejects fabricated origins,
duplicate names, invented resolution URLs and common social/directory/news hosts as official
sites. Company eligibility and website ownership still require substantive source review;
these mechanical checks do not independently establish either.

Assessment uses the shared prepared-company prompt, existing `LiveProviders.research`,
`passage_bank`, `bind_passages` and strict discovery `Worker.record`. Model-authored quotations
are replaced with the selected stored passages. Another company's name/domain cannot be
substituted for the queued target. Invalid cards never enter the saved lead collection.
An unresolved discovery candidate stays in the queue instead of becoming an empty lead card.

## Entry point and compatibility

```python
from lead_agent.discovery import new_discovery_payload
from lead_agent.worker import Worker

payload = new_discovery_payload(
    brief, rubric, target=2, limits=limits, model=model,
    snapshot=discovery_snapshot,  # Omit to run fresh signal searches.
)
run_id = store.create_run(payload)
Worker(store, providers).execute(run_id)
```

A snapshot is a JSON-compatible object containing `search_cache`, `sources` and an optional
`source_run_id`. Cache entries use `url`, `title` and `discovery_hint`; sources use the existing
Source schema. The constructor validates and freezes these inputs under `discovery.snapshot`.
The worker loads copies into its active cache, preserving the frozen snapshot for comparison.

The new constructor explicitly selects `workflow="staged_discovery_v1"` with problem strategy.
`Worker.execute` dispatches that persisted version to `lead_agent/discovery.py`. Existing saved
runs without the version retain their previous execution behavior. Seed and baseline workflows
retain their contracts; unavailable known seed companies can still have explicit incomplete
placeholders. There is no automatic migration or silent replay of earlier runs.

The integrated UI/API currently starts known-company seed tasks. This task adds the research
core, not a new discovery HTTP route, UI screen, orchestration policy or recurring scheduler.

## Budgets, recovery and approvals

- Existing global limits, pre-call request reservations, active time, provider retry policy,
  rate pacing, leases and explicit Stop/resume apply to every stage.
- Candidate extraction and official-site selection have at most two schema/grounding attempts.
  Each candidate has at most two returned assessment attempts and one source expansion.
- Optional signal retrieval is skipped when no web allowance remains, allowing assessment of
  already retrieved pages. Missing company evidence remains an explicit queue outcome.
- Cached searches and pages can be reused at exhausted request budgets without another API call.
- Returned model output is saved before checking Stop. Resume processes that pending output
  instead of paying for it again. Candidate stage and spent budgets remain saved.
- A finished queue short of the target produces `incomplete`; provider/time/call limits can leave
  unfinished candidates visible. Hitting the evidence-valid target can leave remaining entries queued.
- Saved cards are unapproved research. No outreach, qualification handoff, consulting demand,
  CRM update or direction change is inferred or performed.

## Verification and interpretation

The new offline tests cover stage order, successful guarded save, source reuse, nomination
grounding, deduplication, publisher exclusion, official-site boundaries, unavailable pages,
bounded repair, wrong-company records, Stop/resume, budget exhaustion, frozen input preservation,
short-name boundaries and fresh discovery searches. Existing seed/integration tests remain relevant.

The authorized real comparison reuses the latest discovery snapshot and the same Gemini model.
Its new calls and time must be reported separately from the already-paid snapshot acquisition.
The previous run is a historical control. Targeted verification pages may have changed, and model
generation is not deterministic; this is a diagnostic continuation, not a statistically controlled
or equal-cost end-to-end A/B test. See [the verification record](VERIFICATION_STAGED_DISCOVERY.md)
for the actual outcome and a post-trial retry-counter correction verified offline.
