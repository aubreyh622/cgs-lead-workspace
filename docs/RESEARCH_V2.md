# Research v2 and the bounded workflow manager

This is a local early-funnel research system, not an autonomous sales representative.
The integrated Lead research screen defaults to discovery from the effective research
direction. Known-company investigation remains an explicit alternative. New UI requests
use `research_v2`; older API clients and saved runs retain their legacy behavior.

## Execution architecture

The existing Python worker owns state transitions, request budgets, durable SQLite state,
provider pacing, stop/resume and execution leases. A framework migration is not required
for these guarantees. The research steps use small provider-native JSON schemas instead
of a Research record embedded inside an action JSON string.

```text
Effective direction and consultant context
  -> bounded signal searches
  -> grounded nominations and durable candidate queue
  -> official-site resolution
  -> event and cohort page retrieval, including literal article links
  -> typed fact extraction
  -> exact quotation and date checks
  -> separate model review of claim support and cohort
  -> one targeted evidence repair, if useful and affordable
  -> opportunity interpretation with restricted evidence IDs
  -> code-assembled Research record and existing final validation
  -> brief ready for HUMAN review
  -> opportunity, scoped feedback, explicit qualification acceptance
  -> manager feedback analysis and proposed direction change
  -> human-approved version used by the next research task
```

No source page, nomination, model action or manager response can approve a lead, initiate
outreach, write to a CRM, or change the direction without the existing human decision.

## Evidence contract

- Discovery hints nominate companies; they are never factual evidence in a brief.
- Official URLs must come from resolution results. Retrieval accepts searched URLs or
  literal source links; code does not invent article paths.
- Rank event articles and manufacturing information ahead of old annual reports and legal pages.
- Extract exact prose excerpts. Reject image/link/navigation-only content before reasoning.
- Invalid optional dates become unknown; they do not erase otherwise grounded company facts.
  Known stale events are excluded. A reviewable brief requires at least one dated recent event.
- The separate evidence review checks each claim, its target company, category and date context.
  US corporate presence alone does not establish US manufacturing operations.
- Semantic model review is fallible, especially when the same model performs both steps.
  It adds a check, not a guarantee. Source verification by the consultant remains mandatory.
- The final interpretation uses admitted facts and restricted dated-signal IDs. Code supplies
  source IDs, exact quotes, identity, review state and the final record structure.
- Buyer names and emails are not invented. The brief supplies an investigation route when
  no independently verified contact is available. Public events do not establish demand,
  budget, authority, access or a buying deadline.

## Recovery and budgets

Model outputs are checkpointed before they are consumed. Format repair stays in the same
stage; it does not trigger an unrelated search. Evidence repair is bounded and stops if
no new pages were retrieved. Failed or incomplete candidates retain their reasons in the queue.

The new integrated path caps each run at 18 model attempts, 10 searches, 12 page reads,
22 reserved Tavily credits and 480 active seconds, with four model requests per minute.
The target is at most two reviewable briefs. Provider failures and retries consume the
existing global budgets. Reaching a limit is not success and does not authorize a new run.

## Manager and memory

The manager reads only selected, current, scope-appropriate feedback, the corresponding
opportunity evidence/hypotheses/gaps, and metrics for that selected sample. Its output is
a flat structured suggestion which ordinary code converts to a validated direction patch.
It can change only the allowed direction fields. Scope, current version, feedback IDs and
human approval are rechecked before applying a version.

Persistent feedback and approved direction versions are application memory, not model
fine-tuning. A small selected feedback sample does not establish improved conversion.
The manager does not schedule unbounded autonomous research or manufacture consultant feedback.

## Verification boundaries

Controlled tests cover execution and adverse cases. They cannot establish lead usefulness.
Real trials save isolated databases, HTTP attempt/timing logs and public research outputs
under `runtime/live-tests/`. Historical failed trials are preserved. Final live results and
manual source review are recorded separately in `VERIFICATION_RESEARCH_V2.md`.
