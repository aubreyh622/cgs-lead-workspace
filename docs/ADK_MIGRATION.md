# Migrating the agents to Google ADK: options, recommendation, evaluation plan

Status: **option A built (2026-11-11).** The agents run on ADK by default (`api/adk_runtime.py`, `AGENT_RUNTIME`); the
original loop stays as `run_legacy`, the reference. Spike: [ADK_SPIKE_A0.md](ADK_SPIKE_A0.md). Evaluation and result:
[ADK_EVAL.md](ADK_EVAL.md). Two changes from the plan below came out of the spike: A1 wraps our own `api/llm.py` provider as
an ADK `BaseLlm` instead of LiteLLM (keeps the requests identical, and Claude prompt caching), and the existing fake AI
companies replace the planned `ScriptedLlm`. Because every agent goes through the one `R.run` seam, A1 and A2 landed
together. Each agent is still measured on its own in the evaluation.

Inputs read: `api/agent_runtime.py`, `api/agent_tools.py`, `api/llm.py`, `api/jobs.py`, `api/autonomy.py`,
`api/services.py` (`run_agent`), every `api/*_agent.py`, `api/manager_triage.py`, `api/toolkit/__init__.py`,
`api/models.py`, `api/check_quality.py`, `api/seed/quality_cases.json`, `tests/v2/agent_fakes.py`, and the
agents-cli 1.13 skills (`adk-code`, `eval`) installed in `.claude/skills/`.

---

## 1. What we have today

### 1.1 Layers

| Layer | Where | What it does |
|---|---|---|
| Agent loop | `api/agent_runtime.py` `run(spec, provider, task, may_call) -> Outcome` | Hand-written ReAct loop. One task string in, model turns and tool calls until the **finish tool** accepts an answer. |
| Agent definition | `AgentSpec(name, version, system, tools, finish, max_steps, max_tool_calls)` | Built per run by a factory such as `evidence_agent(c, o, note, asks) -> (spec, record)`. Tools are closures over the DB session `c.s` and a per-run `record` dict. |
| Tool | `Tool(name, description, parameters: JSON schema, run(args) -> JSON)` | `ToolError` sends a message back to the model, which may retry. About 50 tools across 11 specs. |
| Model access | `api/llm.py` `Provider.chat(system, messages, tools) -> Turn` | One neutral history format; Gemini, OpenAI and Claude adapters; usage meter; error kinds `auth/quota/temporary/config/invalid_output`. |
| Run record | `services.run_agent` → `AgentRun` | Model, prompt version, tokens, trace (every model turn and tool call), team-instruction revision. |
| Task board | `api/jobs.py`, `api/autonomy.py` | Durable `agent_jobs` queue: 19 job kinds, triggers `event/patrol/handoff/person/manager`, handoff depth ≤ 2 and ≤ 3 per opportunity per day, per-agent pause, daily AI budget, retries, restart recovery, `AGENT_LANES` worker threads. |
| Manager | `manager_triage.py`, `manager_agent.py`, `lessons*.py`, `scorecard.py` | Modes `off/shadow/on`. Triage answers other agents' requests, plans next actions and writes one owner message. Code fallback when it cannot finish. |
| Human gate | `InboxItem` + `autonomy.apply_item` | Agents only prepare. Payload hash, stage basis and 14-day expiry are checked when a person applies. Sending, CRM writes and score changes happen only after a click. Can take days. |
| Governance | `api/toolkit/__init__.py` | CEILING per agent (set in code), then switches set in the app, then `config/toolkit.json`. Every connector call is metered with daily limits. `CLIENT_DATA_TO_AI` gate (SoW §9). |
| Prompt add-ons | `lessons.block()` (approved lessons + team instructions) | Appended to the system prompt at build time; the revision is stamped on the run. |

### 1.2 State

No agent holds conversation state across runs. Each run starts from a task string, and **all durable state is
relational**: `Opportunity`, `Note`, `Proposal`, `InboxItem`, `AgentJob`, `AgentRun`, `OpportunityMemory`
(quote-checked commitments), `AgentLesson`, `TeamInstruction`, `ManagerDecision`, CRM links. These tables live in
SQLite locally and PostgreSQL on Cloud Run. Inside one run, the only state is the closure `record` (proposals kept or dropped, checklist,
asks) and the `Pages` object (pages found and read, which the quote checks use).

### 1.3 Control flow

```
event / patrol / person / handoff
        │
        ▼
 agent_jobs (queued) ──Worker lane──► run_job ──► HANDLERS[kind] ──► services.run_agent ──► R.run(spec, provider, task)
                                                     │                                         (model ⇄ tools ⇄ finish check)
                                                     ├── writes Proposal / Note.analysis / OpportunityMemory …
                                                     ├── J.hand_off / J.triage  (new jobs; manager routes when mode=on)
                                                     └── J.to_inbox(...)  ──► person applies or dismisses (days later)
```

There are **11 call sites of `R.run`** (autonomy ×3, services ×3, discovery, outreach, research ×3). No other code
reaches the loop. This single seam drives the recommendation below.

### 1.4 Behaviour the loop guarantees today (must survive any migration)

1. The finish tool validates the answer. A rejected answer goes back to the model as an error, and the run continues.
2. Brakes: `max_steps`, `max_tool_calls`, a budget check before **every** model request, and a "last step" note.
3. A cut-off turn (hit the length limit) is discarded entirely. Two in a row end the run.
4. Tool failures from outside services (`temporary`, `quota`) are tolerated up to 2, then the run ends.
5. `repair_args`: fixes the Claude Haiku habit of leaking `<parameter name=…>` text into an earlier argument.
6. `fit()`: a tool result is shortened to 6,000 characters and stays valid JSON.
7. Null optional arguments are dropped before the tool sees them.
8. A full trace is saved with the run, even when the run fails. The UI and the scorecard read it.
9. Tools run one at a time against one SQLAlchemy session.

---

## 2. How our concepts map to ADK

| Ours | ADK (google-adk 2.x, Python) |
|---|---|
| `R.run` loop | `Runner` + `LlmAgent` (one invocation per job, `InMemorySessionService`) |
| `AgentSpec.system` + `lessons.block()` | `instruction=` callable (InstructionProvider) that reads lessons and team text at invocation time |
| `Tool` with a JSON schema | Adapter `SpecTool(BaseTool)` that returns our schema from `_get_declaration()` and calls `tool.run(args)` in `run_async`. Converting to typed functions / `FunctionTool` comes later and is optional. |
| `ToolError` → `{"error": …}` | Same: the tool returns an error dict. |
| Finish tool | A tool that validates. When it accepts, it stores the result in `state["temp:result"]` and ends the invocation. |
| `max_steps` | `RunConfig(max_llm_calls=…)`. Add the last-step note in `before_model_callback`. |
| `max_tool_calls`, null-arg drop, `repair_args` | `before_tool_callback` (counter in `temp:` state; returns an error dict once over the limit) |
| `may_call()` budget | `before_model_callback` returns an `LlmResponse` that stops the run |
| `fit()` | `after_tool_callback` replaces the response |
| Cut-off handling | `after_model_callback` checks the finish reason (`MAX_TOKENS`) |
| Outside-service failures | `on_tool_error_callback` |
| Trace and usage → `AgentRun` | A `BasePlugin` that collects events and `usage_metadata` into today's trace shape |
| Gemini / Claude / OpenAI switch | Gemini native. Claude and OpenAI through `LiteLlm("anthropic/…")`. `AI_PROVIDER` keeps working. |
| `record` closure, `Pages` | Unchanged at first (closures still work). Later `tool_context.state["temp:…"]`. |
| Toolkit gate, metering | Unchanged (tools are still built through the gate). Optionally a runner-wide `ToolkitPlugin` audits every call (the `safety-plugins` recipe pattern). |
| Connectors (web search, Insightly, Google Drive) | **MCP servers** `cgs-search`, `cgs-insightly`, `cgs-drive` (cgs_tools/, docs/MCP_TOOLS.md). ADK uses them unchanged through `McpToolset` (stdio, or streamable HTTP on Cloud Run); `tool_filter` gives agents the read tools only, `require_confirmation` can guard writes. The workspace uses the same servers with `TOOLS_TRANSPORT=mcp`. |
| Task board, lanes, patrol, budget, restart recovery | No direct ADK equivalent. Keep them, or move to `Workflow` and Pub/Sub (option B). |
| Inbox approval (days, hash, expiry) | ADK HITL (`ResumabilityConfig`, `request_confirmation`, `RequestInput`) pauses a *session*. That is a poor fit for approvals that take days and must check staleness. Keep the inbox. |
| `OpportunityMemory` | Keep it relational and quote-checked. Memory Bank is a managed Google Cloud (Gemini platform) service: it is unstructured, and it raises SoW §9 questions. |
| `agent_fakes.play()` (Gemini wire format) | `ScriptedLlm(BaseLlm)` converts each `LlmRequest` into the same payload dict and calls `play()`. The fakes are reused unchanged. |

---

## 3. Target architecture options

### Option A: ADK as the agent runtime; keep our control plane (recommended)

Replace only the inside of `R.run`. Add `api/adk_runtime.py` with the same signature,
`run(spec, provider, task, may_call) -> Outcome`. It builds an `LlmAgent` from the `AgentSpec` through `SpecTool`
adapters, adds the callbacks and the trace plugin from §2, and runs one invocation. `R.run` dispatches on
`AGENT_RUNTIME=legacy|adk`. The jobs, inbox, manager, toolkit, DB and prompts do not change, and neither do the 11 call sites.

```
agent_jobs ─► run_job ─► HANDLERS ─► run_agent ─► R.run ──AGENT_RUNTIME=adk──► adk_runtime.run
                                                                                 Runner(LlmAgent(
                                                                                   instruction=spec.system,
                                                                                   tools=[SpecTool(t) …, FinishTool],
                                                                                   model=Gemini | LiteLlm(claude)),
                                                                                   plugins=[TracePlugin, BrakesPlugin])
```

| Pros | Cons |
|---|---|
| Smallest diff (one new module, one switch). Every caller and the UI stay as they are. | Orchestration (jobs, handoffs, manager) is still our own code, not ADK. |
| Per-agent parity is measurable: the same task and fixture run on both runtimes. | Two runtimes exist side by side until legacy is retired. |
| Reversible at any time with one env var. The demo stays safe before handover. | `adk web` shows single agents, not the whole pipeline. |
| The 700+ offline tests run on both runtimes through `ScriptedLlm`. | Typed-function tools (the idiomatic ADK form) come later. |
| Gives CGS "agents on ADK" on Cloud Run with no change to the hosting design. | |

### Option B: ADK agents + ADK `Workflow` graphs for the event chains

Do option A first, then express the multi-step chains as `google.adk.workflow.Workflow` graphs. This is the
`ambient-expense-agent` recipe pattern: events drive a graph, business rules stay in code, and only the parts
that need judgement call a model. Example: `note_evidence` becomes
`load_note → evidence LlmAgent → code checks → route{asks? → person_lookup LlmAgent} → manager triage LlmAgent | fallback → to_inbox`.
Triggers move from the patrol and `agent_jobs` to Pub/Sub or Cloud Scheduler on Cloud Run. Durable sessions use
`DatabaseSessionService` on the existing PostgreSQL. The inbox stays the approval gate.

| Pros | Cons |
|---|---|
| Idiomatic ADK end to end. Each chain is visible in `adk web` and Cloud Trace. | The Workflow API is ADK 2.0, still new. |
| Handoffs become edges instead of rows in `agent_jobs`, which is easier to reason about. | Handoff depth/cap, per-agent pause, daily budget, lanes and restart recovery all need new homes (graph rules, retry config, plugins). |
| Fits the CGS hosting standard: Cloud Run, Pub/Sub, ADK as orchestration. | Much larger change. Most `test_autonomy` / `test_manager_triage` tests need rewriting. |
| Can be done one chain at a time after option A. | Parity is harder to prove: timing and ordering of work change. |

### Option C: full ADK multi-agent (not recommended)

The Manager becomes the root `LlmAgent` with sub-agents (`transfer_to_agent` / `AgentTool`). Session state and Memory
Bank replace the relational memory. ADK HITL replaces the inbox. Deployment moves to Agent Runtime.

| Pros | Cons |
|---|---|
| Most "ADK-native" picture for a demo. | A model decides routing. Today code guarantees depth caps, budgets and "agents never send". Those guarantees become prompt-dependent. |
| | Approvals that take days don't fit session pause/resume. The staleness and expiry checks would have to be rebuilt. |
| | Memory Bank and Agent Runtime send CGS-derived data to managed Google services (SoW §9), and the memory is unstructured, with no quote checks. |
| | Effectively a rewrite. Behaviour parity cannot be shown before the ~2026-11-20 handover. |

### Recommendation

**Option A now, with option B as the documented next step for CGS.** The `R.run` seam lets ADK replace the
part that is generic (the model and tool loop), while the parts that encode CGS's risk rules (the approval gate,
permissions, budgets, quote checks, the code fallback) stay deterministic and already tested. Option A is reversible
with one env var, it can be proven per agent with the evals in §5, and it does not change the hosting design that is
already on Cloud Run. Option B then becomes a choice CGS makes per chain, starting from a working ADK base.

---

## 4. Option A in phases

AGENTS.md rule: one phase at a time; report and stop after each.

| Phase | Work | Exit check |
|---|---|---|
| **A0 Spike** (½ day) | Install `google-adk` in a **separate venv** first. Check for conflicts with the pinned `fastapi 0.162` / `starlette 1.10` / `pydantic 2.14`, and check Python 3.14 support. If anything conflicts, ship `requirements-adk.txt` as an optional extra. Confirm against the installed source: (a) how a tool ends the invocation, (b) whether ADK runs several function calls from one turn **concurrently**. If it does, serialize them: our tools share one SQLAlchemy session, see §1.4 item 9. (c) `LiteLlm` + Claude tool calling, (d) `MAX_TOKENS` finish reason on Claude via LiteLLM. | Short spike note with answers to (a)–(d) |
| **A1 Runtime + Evidence agent** (1–2 days) | `api/adk_runtime.py` (SpecTool, FinishTool, BrakesPlugin, TracePlugin), `AGENT_RUNTIME` switch in `R.run`, `ScriptedLlm` for tests, `adk_app/evidence/` for agents-cli, eval harness (§5) and the Evidence dataset | All offline tests pass with `AGENT_RUNTIME=adk`. Evidence eval passes the parity gate against the legacy baseline. |
| **A2 Other agents** | Research (brief, person lookup, refresh), Outreach, Discovery, Direction, Manager (review, triage, next actions, lessons): each with its own dataset | Each agent passes its own parity gate. `Check Workflow` and `Check Orchestrator` pass live with `AGENT_RUNTIME=adk`. |
| **A3 Default flip** | Make `adk` the default. Keep `legacy` for one release. Update `BACKEND_V2.md` and the handover docs. | Full eval run plus hold-out set green. |
| **B (CGS decision)** | First chain as a `Workflow`: `note_evidence → person_lookup → triage` | Workflow eval (multi-turn trajectory) and the `test_autonomy` equivalents |

Keep the model fixed while migrating (`AI_PROVIDER=claude`, `AI_MODEL=claude-haiku-5-5`, as now), so the evals
measure the runtime and nothing else. Moving to Gemini is a separate, later eval with the same datasets (§5.6).

---

## 5. Evaluation plan with `agents-cli eval`

agents-cli 1.13.0 is installed (`~/.local/bin/agents-cli`). `google-adk` is not yet in any venv.

### 5.1 Layout

```
adk_app/                       # ADK apps that agents-cli serves (`adk api_server`); App name == directory name
  evidence/agent.py            # root_agent = eval wrapper around the Evidence agent (see 5.2)
  research/  outreach/  discovery/  manager_triage/ …
tests/eval/
  eval_config.yaml             # metrics_to_run, custom_metrics (local), thresholds
  metrics/                     # custom_function_file modules: evidence.py, research.py, outreach.py, discovery.py, common.py
  datasets/                    # generated from api/seed/quality_cases.json + ai_exam.json
    evidence.json  research.json  outreach.json  discovery.json  triage.json
    holdout.json               # never used while iterating
tools/
  make_evalsets.py             # seed cases -> EvaluationDataset (extra fields: fixture, expect, forbid, signal, people …)
  export_legacy_traces.py      # AgentRun.trace (legacy runtime) -> agents-cli trace format, for `eval grade --traces`
```

### 5.2 How a case reaches the agent

`eval generate` only sends a prompt over `/run_sse`, but our agents need an opportunity, notes and a synthetic web.
The prompt therefore carries the case id together with the normal task text
(`{"case": "ardent-full", "task": "<EA.task(o)>"}`). In the eval wrapper, a `before_agent_callback` creates a throwaway
SQLite workspace from the demo seed and that case's fixture (`mock_web.json` simulations, notes), with mail and CRM off.
It then builds the real `AgentSpec` through the same factory production uses. Accepted finish results are emitted
as the final text (JSON), so `{response}` is the agent's answer, and every `propose` / `add_fact` / `nominate` call
appears in `{agent_data}`. Everything is synthetic, so SoW §9 is respected.

### 5.3 Metrics (local `custom_function_file`, no Google Cloud project needed)

These port `api/check_quality.py` so that today's numbers and the eval numbers measure the same thing.

| Agent | Metric | Read from the trace | Threshold |
|---|---|---|---|
| all | `finished` | finish accepted, not `incomplete` | ≥ legacy |
| all | `steps`, `tool_calls`, `tokens` | event counts, `usage_metadata` | report only; flag > legacy +25 % |
| Evidence | `overclaims` (scored 1 when the case has none) | accepted `propose` vs `forbid`, and score above `max` | **= 1.0 (hard)** |
| Evidence | `expected_recall` | accepted `propose` within `expect` ranges | ≥ legacy − 0.05 |
| Evidence | `quote_verbatim` | every accepted quote is in the fixture notes | = 1.0 |
| Evidence | `checklist_valid` | finish `checked` covers the 11 keys | = 1.0 |
| Research | `signal`, `dated`, `contact_ok`, `no_invented_names`, `not_rejected` | kept facts, contacts, recommendation | each ≥ legacy; `no_invented_names` = 1.0 |
| Outreach | `code_checks_passed`, `resubmits` | `submit_draft` responses | ≥ legacy |
| Outreach | `rubric_hook/insight/specific/ask/voice` (1–5) | LLM judge in `metrics/outreach.py`, same prompt and `QUALITY_JUDGE_MODEL` as `check_quality` (kept fixed between runs) | ≥ legacy − 0.3 |
| Outreach | `unsupported_claim` | the same judge | ≤ legacy |
| Discovery | `fit_precision`, `not_a_fit_nominated` | `nominate` calls vs `not_a_fit` | `not_a_fit` = 0 (hard) |
| Triage | `fallback_free`, `answered_all_requests`, `message_present` | `answer_request`, `message_owner` | ≥ legacy |

Optional managed metrics, only if CGS provides a Google Cloud project, and only on synthetic traces:
`multi_turn_tool_use_quality`, `multi_turn_trajectory_quality`, `hallucination` (Research and Outreach). They use the
Eval Service at the `global` endpoint, so traces go to Google. That is acceptable only because the data is synthetic.

### 5.4 Parity procedure (per agent)

1. **Legacy baseline:** run the cases on `AGENT_RUNTIME=legacy`. `tools/export_legacy_traces.py` writes the traces, then
   `agents-cli eval grade --traces artifacts/legacy_traces/evidence --config tests/eval/eval_config.yaml` → `results_legacy.json`.
2. **ADK candidate:** `agents-cli eval run --dataset tests/eval/datasets/evidence.json --config tests/eval/eval_config.yaml` → `results_adk.json`.
3. **Compare:** `agents-cli eval compare results_legacy.json results_adk.json`. No hard metric may drop, and soft metrics stay within the tolerances above.
4. **Variance:** 3 runs on each side, compared by mean. Pin `temperature=0` where the provider allows.
   A case that flips between runs is reported as flaky. Do not delete it.
5. **Hold-out:** about 25 % of cases (5 Evidence, 2 Research, 2 Outreach) go in `holdout.json`. Grade them once, at the A3 gate.
6. The exit code is the gate (`RESULT: PASS|FAIL`). Paste the score table into the phase report.

### 5.5 Deterministic layer (no model)

- `pytest` with `AGENT_RUNTIME=adk` and `ScriptedLlm` replaying `agent_fakes.play()`. The full offline suite must pass unchanged.
- New tests for each guarantee in §1.4: rejected finish then retry, step limit, tool-call limit, budget stop before a
  model call, cut-off discarded, 2 tolerated outside failures, `repair_args`, `fit()`, null-arg drop, trace saved on failure,
  tools never run concurrently.
- The live checks `Check Agents`, `Check Workflow` and `Check Orchestrator` (16 checks) are run again with `AGENT_RUNTIME=adk`.

### 5.6 After the migration

The same datasets serve the Claude → Gemini default flip that is planned before handover:
`AI_PROVIDER=gemini agents-cli eval run …` compared with the Claude ADK results. This becomes CGS's regression suite in CI
(`agents-cli eval run --fail-under …`) for every prompt, tool or model change.

---

## 6. Risks and open questions

| Risk | Mitigation |
|---|---|
| ADK dependency pins clash with ours (starlette/fastapi/pydantic), or Python 3.14 is unsupported | A0 spike in a separate venv; optional `requirements-adk.txt`; the Docker image can pin Python 3.12 |
| ADK runs a turn's function calls concurrently, and our tools share one DB session | Serialize in the adapter (a lock in `SpecTool`), or keep tools sync and verify in A0 |
| The Claude via LiteLLM path behaves differently from our Claude adapter (cache headers, `<parameter>` leakage, stop reasons) | Keep `repair_args`; the parity evals run on Claude; a fallback is a custom `BaseLlm` wrapping our `ClaudeProvider` |
| The async Runner inside threaded worker lanes | One `asyncio.run` per job inside the lane thread; covered by `test_lanes` |
| Eval cost | Start with `--limit`-sized subsets while iterating; full runs only at phase gates |
| Whether CGS requires ADK, and which Google Cloud project to use (emailed 2026-11-10, no reply yet) | Option A is useful either way and costs little to stop |
