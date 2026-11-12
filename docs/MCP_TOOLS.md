# The tools as MCP servers

CGS asked (2026-11-12) that the tools stay separate modules with clear inputs and outputs, ideally MCP servers, so they
carry over to Google ADK unchanged and only the orchestration layer is replaced later. They are in `cgs_tools/`.

## The three servers

| Server | Module | Tools (WRITE = changes CGS data) | Settings it reads |
|---|---|---|---|
| `cgs-search` | `cgs_tools/search_server.py` | `search_info`, `web_search`, `read_page`, `company_pages` | config/search.json, `WEB_MODE`, the search key |
| `cgs-insightly` | `cgs_tools/insightly_server.py` | `crm_info`, `check_setup`, `record_url`, `matches`, `find_lead`, `get_lead`, `get_deal`, `get_task`; WRITE `save_lead`, `add_note`, `save_task`, `convert` | config/crm.json, `CRM_PROVIDER`, `INSIGHTLY_API_KEY`, `INSIGHTLY_API_URL` |
| `cgs-drive` | `cgs_tools/drive_server.py` | `drive_info`, `walk`, `folder`, `children`, `read_doc`, `find_folder`; WRITE `make_folder`, `save_record` | `DRIVE_PROVIDER`, `DRIVE_AUTH` or `DRIVE_REFRESH_TOKEN` |

Every tool has a typed input schema and a typed output schema (pydantic models in each module), so a client gets
structured results, not text. An error from the outside system comes back as a tool error whose text is JSON
(`{"type": "CrmError", "message": ...}`); the workspace's client raises the same exception the in-process code raises.

Each server wraps the connector code the workspace already had (`api/search.py`, `api/toolkit/crm.py`,
`api/toolkit/drive.py`). A server holds no workspace state and never opens the workspace database
(tests/v2/test_mcp_tools.py checks its imports).

## What stays in the orchestration layer

These belong to the workspace, not to the servers, and are what an ADK port replaces or re-creates:

- **Who may use what**: the permission table and its ceiling (api/toolkit `CEILING`, switches, config/toolkit.json).
- **Daily limits and the usage log** (`connector_uses`): every call through a server is still metered by the gate.
- **Writes happen only after a person acts**: CRM writes run in api/crm_sync.py after a click; Drive record saves run
  in api/drive_records.py. No agent is ever given a WRITE tool. In ADK: `McpToolset(..., tool_filter=[read tools])`
  for agents, and `require_confirmation` if a write tool is ever given to one.
- **Answer checks** (a quote must be word for word, a page must have been read in this run): in the agents.

## Running them

```
python -m cgs_tools.search_server              # stdio (how the workspace starts them)
python -m cgs_tools.insightly_server --http 8932   # streamable HTTP at http://<host>:8932/mcp
```

The workspace uses them when `TOOLS_TRANSPORT=mcp` (default `inprocess`, which calls the same code directly). Then the
connector factories (`make_search`, `make_crm`, `make_drive`) hand out clients from `cgs_tools/client.py` with the
same methods. One server process per server, started on first use with the workspace's environment, shared by all
threads; a server that stops is started again on the next call. Logs: `runtime/mcp-<server>.log`.

On Cloud Run the simplest set-up keeps them as stdio child processes in the workspace's container (nothing to deploy
separately). To share them with other agents, deploy each as its own Cloud Run service with `--http $PORT`, require
IAM authentication, and point ADK's `StreamableHTTPConnectionParams` at it.

## From ADK

```python
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

search = McpToolset(connection_params=StdioConnectionParams(
    server_params=StdioServerParameters(command="python", args=["-m", "cgs_tools.search_server"])))
crm_reads = McpToolset(connection_params=StdioConnectionParams(
    server_params=StdioServerParameters(command="python", args=["-m", "cgs_tools.insightly_server"])),
    tool_filter=["matches", "find_lead", "get_lead", "get_deal", "get_task"])
agent = LlmAgent(name="research", model="gemini-...", instruction="...", tools=[search, crm_reads])
```

## Checks

- `python -m api.check_mcp`: each server starts and lists typed tools; an ADK `LlmAgent` calls `web_search` through
  ADK's own `McpToolset` and answers from it; the CRM writes are filtered out of what the agent sees. Runs on the
  synthetic web and mock CRM (no keys); `--live` uses the providers in .env.
- `tests/v2/test_mcp_tools.py`: the servers give the same answers as the in-process connectors (search, CRM lead
  life cycle including convert, Drive records), raise the same errors, and never import the workspace database.
- The full test suite also passes with `TOOLS_TRANSPORT=mcp`. Four files (test_crm, test_crm_readback, test_mock_web,
  test_undo) stay in-process there: they read and steer the mock CRM's own memory (`leads`, `edit`, `fail_next`),
  which under MCP lives in the server's process. Running them over MCP found two schema bugs (a lead without an
  email; a numeric rating), now fixed and covered in test_mcp_tools.py.

Not yet tried against the real Insightly and Google Drive through the servers (the Insightly trial account ended on
2026-11-12; Drive needs a new sign-in for writing). The server code calls exactly the methods the in-process path
already used live.
