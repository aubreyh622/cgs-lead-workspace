"""Check the MCP tool servers (cgs_tools/) and that Google ADK can use them unchanged.

    python -m api.check_mcp            servers + ADK check, on the synthetic web / mock CRM / mock Drive (no keys, no cost)
    python -m api.check_mcp --live     the same servers with the providers in .env (search.json, CRM_PROVIDER, DRIVE_PROVIDER)

1. Each server starts over stdio and lists its tools; every tool has an input schema and a typed output schema; write
   tools say WRITE.
2. ADK: an LlmAgent gets the cgs-search server through ADK's own McpToolset (no workspace code in between), calls
   web_search and reads the answer. The model is scripted (it always asks for the same search), so the check proves the
   wiring, not a model's judgement; tests/eval does that for the agents.
3. ADK with tool_filter: an agent given cgs-insightly sees only the read tools, the way the workspace gives agents only
   reads; the writes stay with the orchestration (after a person acts). ADK's require_confirmation can guard them too.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import AsyncGenerator

from cgs_tools import SERVERS
from cgs_tools.client import ROOT
from cgs_tools.common import SERVER_FLAG

READ_TOOLS = {"cgs-insightly": ["crm_info", "matches", "find_lead", "get_lead", "get_deal", "get_task"]}


def params(server: str, env: dict | None = None):
    from mcp import StdioServerParameters
    return StdioServerParameters(command=sys.executable, args=["-m", SERVERS[server]], cwd=str(ROOT),
                                 env={**os.environ, SERVER_FLAG: "1", **(env or {})})


async def list_tools(server: str, env: dict | None = None) -> list[dict]:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    with open(os.devnull, "w") as quiet:
        async with stdio_client(params(server, env), errlog=quiet) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
    return [{"name": t.name, "write": (t.description or "").startswith("WRITE"), "input": bool(t.inputSchema),
             "output": bool(t.outputSchema)} for t in tools]


def scripted_model(query: str):
    """An ADK model that asks for one web_search, then answers with the first title it got back."""
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    class Scripted(BaseLlm):
        async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
            answers = [p.function_response for c in llm_request.contents for p in (c.parts or []) if p.function_response]
            if not answers:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
                    name="web_search", args={"query": query, "as_of": "2026-11-01"}))]))
                return
            resp = answers[-1].response or {}
            results = (resp.get("structuredContent") or resp.get("result") or resp or {})
            rows = results.get("results") if isinstance(results, dict) else None
            text = rows[0]["title"] if rows else f"NO RESULTS: {str(resp)[:300]}"
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))

    return Scripted(model="scripted")


async def adk_agent_run(env: dict | None = None, query: str = "manufacturer completes acquisition") -> dict:
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner
    from google.adk.tools.mcp_tool import McpToolset
    from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
    from google.genai import types
    search = McpToolset(connection_params=StdioConnectionParams(server_params=params("cgs-search", env), timeout=60),
                        errlog=open(os.devnull, "w"))
    crm = McpToolset(connection_params=StdioConnectionParams(server_params=params("cgs-insightly", {"CRM_PROVIDER": "mock", **(env or {})}),
                                                             timeout=60),
                     tool_filter=READ_TOOLS["cgs-insightly"], errlog=open(os.devnull, "w"))
    try:
        agent = LlmAgent(name="research_probe", model=scripted_model(query), instruction="Search the web and report the top title.",
                         tools=[search, crm])
        seen = []
        for ts in (search, crm):
            seen += [t.name for t in await ts.get_tools()]
        seen.sort()
        runner = InMemoryRunner(agent=agent, app_name="cgs-mcp-check")
        session = await runner.session_service.create_session(app_name="cgs-mcp-check", user_id="check")
        final, called = "", []
        async for ev in runner.run_async(user_id="check", session_id=session.id,
                                         new_message=types.Content(role="user", parts=[types.Part(text="Go")])):
            for p in (ev.content.parts if ev.content else []) or []:
                if p.function_call:
                    called.append(p.function_call.name)
                if p.text and ev.is_final_response():
                    final = p.text
        return {"tools": seen, "called": called, "answer": final}
    finally:
        await search.close()
        await crm.close()


def main(argv: list[str] | None = None) -> int:
    live = "--live" in (argv if argv is not None else sys.argv[1:])
    env = {} if live else {"WEB_MODE": "mock", "CRM_PROVIDER": "mock", "DRIVE_PROVIDER": "mock"}
    ok = True

    def say(good: bool, text: str) -> None:
        nonlocal ok
        ok &= good
        print(("PASS " if good else "FAIL ") + text)

    for server in SERVERS:
        try:
            tools = asyncio.run(list_tools(server, env))
        except Exception as e:  # noqa: BLE001 - reported
            say(False, f"{server}: did not start ({type(e).__name__}: {e})")
            continue
        typed = all(t["input"] and t["output"] for t in tools)
        writes = [t["name"] for t in tools if t["write"]]
        say(bool(tools) and typed, f"{server}: {len(tools)} tools, all with input and output schemas"
                                   + (f"; writes: {', '.join(writes)}" if writes else "; read only"))
    try:
        out = asyncio.run(adk_agent_run(env))
        say(out["called"] == ["web_search"] and bool(out["answer"]) and not out["answer"].startswith("NO RESULTS"),
            f"ADK LlmAgent + McpToolset called {out['called']} and answered: {out['answer'][:80]!r}")
        writes = {"save_lead", "add_note", "save_task", "convert"}
        say(not writes & set(out["tools"]), f"ADK agent sees {len(out['tools'])} tools; CRM writes filtered out: {sorted(writes - set(out['tools'])) == sorted(writes)}")
    except Exception as e:  # noqa: BLE001 - reported
        say(False, f"ADK run failed: {type(e).__name__}: {e}")
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
