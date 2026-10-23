# Offline integrated demonstration

Allow about 10 minutes. The purpose is to show inspectable research output, explicit human
handoffs and controlled feedback memory inside the existing qualification interface.
The research inputs are known companies. Autonomous company discovery across North America
and an always-running research scheduler are not implemented in this scope.

## Start and expected state

1. Double-click **Start Offline Demo.cmd** in the project folder. Keep its terminal open.
2. After the ready message, open **http://127.0.0.1:8767/**.
3. Confirm the amber **OFFLINE SYNTHETIC DEMO** banner. No API keys are needed.
4. Open **Lead research**. A fresh session contains four completed tasks, four opportunities,
   three handoff receipts, two direction versions and one completed simulated feedback review.

The first startup prepares two rounds through the actual localhost API. Subsequent startups
preserve this session and all interactive changes. Preparation uses isolated databases under
`runtime/offline-demo/`, never the main workspace databases on port 8766.
For a fresh rehearsal, stop the demo and use a new empty directory:

```powershell
python -B demo.py --workspace-dir runtime/offline-demo-session-2 --port 8767
```

Use the configured project Python if `python` is not on PATH. Do not point this command at
an existing project or main data directory. Nonempty unrelated or incomplete directories
are refused without being reset.

## Walkthrough

| Time | Click path | Point to demonstrate |
| --- | --- | --- |
| 0-2 min | Lead research → latest Synthetic Factory 2 task → observed facts and source excerpts | A brief separates dates and exact quotes from problem/relevance hypotheses, alternatives, unknowns and missing contacts. `.example` URLs are fictional fixtures and do not open real source pages. |
| 2-4 min | Research opportunities → Round 2 acquisition opportunity → review history; then Handoff inbox | The first receiving decision returned the opportunity. A revised review led to a new receipt. Same company can have a different team/opportunity; the receiving reviewer checks relationships. |
| 4-5 min | Accepted Round 2 opportunity → Open qualification case | Contact is unknown; stage is Not contacted. Owner, due date and next action are recorded. Research background is available, while confirmed evidence and qualification scores remain empty. |
| 5-6 min | Lead research → accepted Round 1 opportunity → Open qualification case | Fictional outreach and reply records made this case Active. Drafting alone did not contact anyone. Active still has no confirmed qualification evidence. |
| 6-8 min | Lead research → Workflow manager → approved proposal → before/after diff and approved direction memory | Feedback supports a narrow proposal, human approval creates a version, and round 2 tasks use it. Compare a saved round 1 task's original version. |
| 8-10 min | Research a company → Use Synthetic Factory 1 → Start research | The UI runs another real worker task against offline adapters. It uses the current approved version and produces another explicitly synthetic card. |

## Continue an interactive feedback loop

1. On the new card, click **Save an opportunity…**. Give it a distinct fictional title/team.
2. **Record human review…**: use `Demo consultant`, select a decision/reason and **This research
   theme** scope. Worth exploring requires checking the cited facts. Leave review time empty
   unless a real duration was observed; do not enter a guessed zero.
3. In Workflow manager, set **Manager consultant context** to `Demo consultant`, scope to
   **Shared research theme**, then click **Load manager context**.
4. Click **Simulate feedback review**. On the seeded integration direction, the fixture reports
   no justified further change. This is deterministic behavior, not a live model's judgment.
5. To demonstrate a new pending/approved cycle, click **Create manual proposal…**, using:

```json
{"signals": ["Integration sequencing and data governance planning"]}
```

6. Enter a rationale tied to the selected synthetic feedback and save. Confirm the direction
   still has the old version. Review the proposal diff, then **Approve / reject proposal…**.
7. Enter a named approving reviewer and decision rationale. Save the decision. A new version
   appears; approval remains mock. Research Synthetic Factory 2 and verify that task's version.

Manual proposals remain labeled manual. The simulated analyst only knows its fixed fixture
scenario; use the described sequence for a predictable demonstration. A new approved version
needs new feedback on new-version opportunities before another shared change can be proposed.
Accepted opportunities are frozen, so use a newly saved opportunity for new feedback.

## Describe the output accurately

The demonstrated output is an evidence-linked company card, a human-reviewed opportunity,
an accountable receiving receipt/case, a workflow dashboard and a human-approved direction
history. It is not a verified prospect list, an inferred budget, a sent email or a sales forecast.
The orchestrator is a bounded workflow manager: it summarizes feedback and proposes allowed
changes. It does not retrain model weights, invent an ICP, or manage undeveloped agents.

Stop with Ctrl+C in the demo terminal. See `OPERATIONS_HANDOFF.md` for recovery and storage,
`PILOT_ACCEPTANCE.md` for results, and `LIVE_PILOT_CHECKLIST.md` for the real trial still needed.
