# ADK migration: A0 spike result (2026-11-11)

Phase A0 of [ADK_MIGRATION.md](ADK_MIGRATION.md). Goal: answer the open questions before any runtime code is written.
No code in the workspace changed. The probes are throwaway scripts (a scripted model with no network, plus one live probe
on Claude with an invented company).

## Environment

| Question | Answer |
|---|---|
| Install | `google-adk 2.14.0` + `litellm 1.103.7` install into a **separate venv** (`C:\dev\cgs-adk-venv`, Python 3.14.3) with `requirements.lock` as constraints. **No pinned package changes** (fastapi 0.162, starlette 1.10, pydantic 2.14, SQLAlchemy 2.1.6 stay); `pip check` clean. |
| Python 3.14 | Imports and runs. |
| Our offline suite with ADK installed | see "Test suite" below |

So ADK can be an optional extra (`requirements-adk.txt`, installed with `-c requirements.lock`). The main lock does not
need to change until the default flips (A3).

## The four open questions

**(a) How a tool ends the run.** Set `tool_context.actions.skip_summarization = True` in the finish tool when it accepts.
`Event.is_final_response()` is then true and the flow stops, with no further model call. This is public API.
If the finish tool rejects, it returns `{"error": ...}`, the model sees it and tries again.
Verified: reject → retry → accept, 2 model calls, the third scripted turn never requested.
ADK's own `finish_task` / task mode is private (`_AgentMode.TASK`) and built for delegated sub-agents with a fixed tool name,
so it does not fit our named finish tools (`finish`, `submit_draft`).
`temp:` state is dropped at the end of the run, so the accepted result is kept on the adapter object, not in session state.

**(b) Concurrency.** ADK runs the function calls of one model turn as asyncio tasks, gathered together. A tool whose
`run_async` calls our synchronous `tool.run(args)` without awaiting runs to completion before the next task starts:
verified with two calls in one turn, no overlap, same thread. Threads are used only if
`RunConfig.tool_thread_pool_config` is set, and we will not set it. Claude does put several calls in one turn
(seen live: two `read_page` calls at once), so the adapter needs a test that guards this: our tools share one
SQLAlchemy session and must stay serial.

**(c) Claude through LiteLLM.** Works end to end. Our JSON schemas (enum, `additionalProperties: false`) are accepted
through `parameters_json_schema`. Tool calls, the finish tool and usage tokens all come back. **But** LiteLLM does not
send our `cache_control` markers, so prompt caching (system prompt and tool list) is lost. It also bypasses
`api/llm.py` (error kinds, `METER`, `repair_args` evidence, the Gemini thought signatures kept in `Turn.raw`).

**(d) Cut-off on Claude.** `max_output_tokens` → `finish_reason = MAX_TOKENS`, visible in `after_model_callback`. ✔

## The brakes in ADK terms (all verified with the scripted model)

| Guarantee (ADK_MIGRATION §1.4) | ADK hook | Result |
|---|---|---|
| 1 finish validates, rejection goes back | finish `BaseTool` + `skip_summarization` | ✔ |
| 2 step limit | `RunConfig(max_llm_calls=N)` raises `LlmCallsLimitExceededError` | ✔ map to `incomplete` |
| 2 tool-call limit | `before_tool_callback` returning a dict skips the tool | ✔ (note: `after_tool_callback` still runs on that dict) |
| 2 budget before every request | `before_model_callback` returning an `LlmResponse` | ✔ no model call, run ends |
| 3 cut-off discarded | `after_model_callback` sees `MAX_TOKENS` and can replace the turn | ✔ (see difference 1) |
| 4 outside failures tolerated | `on_tool_error_callback` returns `{"error": ...}` | ✔ (without it the exception ends the run) |
| 5 `repair_args`, 7 null-arg drop | `before_tool_callback` edits `args` in place | ✔ |
| 6 `fit()` | `after_tool_callback` returns the shortened dict | ✔ |
| 8 trace | event stream + callbacks | ✔ (built in A1) |
| 9 tools serial | see (b) | ✔ |

## Three differences A1 must handle

1. **A text-only turn ends an ADK run.** Our loop answers it with "Use a tool to continue, or call finish" (or the
   cut-off note) and goes on. A1 handles this with an outer loop: when an invocation ends without an accepted finish
   and steps remain, send that note as the next user message **in the same session**. ADK keeps the history, and the
   adapter counts steps across invocations.
2. **ADK adds a line to the system prompt** ("You are an agent. Your internal name is …"). For byte-identical
   prompts (needed for parity), `before_model_callback` sets `llm_request.config.system_instruction` to our exact
   system text. Verified live.
3. **The "last step" note** is added to the request in `before_model_callback` on the second-to-last step.

## Decision for A1: wrap our provider, don't use LiteLLM yet

Add a `ProviderLlm(BaseLlm)` that turns ADK's `LlmRequest` into our neutral `chat(system, messages, tools)` call and our
`Turn` back into an `LlmResponse`. The `Turn.raw` for each model turn is kept by function-call id, so Gemini thought
signatures and Claude content blocks go back unchanged. Why:

- **Parity:** the model sees exactly the same bytes as on the legacy runtime, so any eval difference comes from the loop.
- **Cost:** Claude prompt caching stays on; `METER`, `AgentRun` tokens and the quality-check pricing keep working.
- **Tests:** `agent_fakes.play()` works at our transport level, so the whole offline suite runs on the ADK runtime
  **without** a new scripted model.
- `AI_PROVIDER` gemini/openai/claude keeps working unchanged.

ADK's native `Gemini` model (and `LiteLlm` for others) becomes a later, separate step, measured with the same evals.
That step suits the Gemini default flip, because the native model brings ADK's Gemini features (context caching, grounding).

## Test suite

With ADK and LiteLLM installed in the separate venv, the full offline suite gives **703 passed, 1 skipped** (2 min 55 s,
same command as in the README). ADK's extra packages do not disturb the workspace.
