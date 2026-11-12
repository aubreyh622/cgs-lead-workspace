"""Shared by the three servers: how an error crosses the wire, and how a server starts."""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Callable

import anyio

SERVER_FLAG = "CGS_TOOLS_SERVER"  # set in a server's process: the connector factories then never hand out an MCP client
ERROR_KINDS = {"LLMError": "search", "CrmError": "crm", "DriveError": "drive"}


class ServerError(Exception):
    """A connector error, sent to the client as JSON so it can raise the same exception type there."""


def wire_error(e: Exception) -> ServerError:
    return ServerError(json.dumps({"type": type(e).__name__, "message": str(e), "kind": getattr(e, "kind", "")}))


async def run(fn: Callable[[], Any], errors: tuple[type[Exception], ...]) -> Any:
    """Run blocking connector code in a worker thread (so one slow call does not hold up the others) and turn its known
    errors into wire errors."""
    try:
        return await anyio.to_thread.run_sync(fn)
    except errors as e:
        raise wire_error(e) from None


def main(mcp) -> None:
    os.environ[SERVER_FLAG] = "1"  # the server calls the connector code itself, never another server
    p = argparse.ArgumentParser(description=f"MCP server {mcp.name}")
    p.add_argument("--http", type=int, metavar="PORT", help="serve streamable HTTP on this port instead of stdio")
    a = p.parse_args()
    if a.http:
        mcp.settings.host, mcp.settings.port = "0.0.0.0", a.http
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
