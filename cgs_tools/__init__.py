"""CGS tools as MCP servers: the outside systems the workspace reaches, each a separate module with typed inputs and outputs.

Three servers, one per outside system:
  cgs-search     the web (Google Search through Gemini, Tavily, or the synthetic web): web_search, read_page, company_pages
  cgs-insightly  the CRM: reads (matches, find_lead, get_lead, get_deal, get_task) and the writes the workspace makes after
                 a person acts (save_lead, add_note, save_task, convert)
  cgs-drive      Google Drive: reading the CGS library folders (walk, children, read_doc) and saving the workspace's
                 records (drafts, notes, the activity log) as Google Docs and Sheets in the CGS records folder

Each server wraps the connector code in api/toolkit and api/search.py and holds no workspace state: it never opens the
workspace database. What an agent may use, daily limits and the usage log stay in the workspace (api/toolkit, the gate),
which is part of the orchestration layer. So the servers can move to Google ADK unchanged (McpToolset), and only the
orchestration is replaced.

Run one:   python -m cgs_tools.search_server            (stdio, the way the workspace starts them)
           python -m cgs_tools.search_server --http 8931 (streamable HTTP, for a separate Cloud Run service)
The workspace uses them when TOOLS_TRANSPORT=mcp (cgs_tools/client.py); the default (inprocess) calls the same code
directly. tests/v2/test_mcp_tools.py checks both ways give the same answers. See docs/MCP_TOOLS.md.
"""

SERVERS = {
    "cgs-search": "cgs_tools.search_server",
    "cgs-insightly": "cgs_tools.insightly_server",
    "cgs-drive": "cgs_tools.drive_server",
}
