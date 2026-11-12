"""The workspace's side of the MCP servers (TOOLS_TRANSPORT=mcp).

Each client below has the same methods as the connector it stands for (api/search.py, api/toolkit/crm.py,
api/toolkit/drive.py) and the same errors, so the rest of the workspace, the toolkit gate included, cannot tell the
difference: the gate still checks permissions, meters every call and applies the daily limits.

One server process per server (started on first use, over stdio, with this process's environment), shared by every
thread. The MCP client is asynchronous; it runs on its own event loop thread and the methods here wait for the answer.
A server that stops is started again on the next call. Server logs: runtime/mcp-<server>.log.
"""
from __future__ import annotations

import asyncio
import atexit
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any

from cgs_tools import SERVERS
from cgs_tools.common import SERVER_FLAG

ROOT = Path(__file__).resolve().parents[1]
CALL_TIMEOUT = 180  # seconds; a web page or an Insightly call that takes longer is reported as failed


def wanted() -> bool:
    """TOOLS_TRANSPORT=mcp, and this process is not itself one of the servers."""
    return os.environ.get("TOOLS_TRANSPORT", "inprocess").strip().lower() == "mcp" and not os.environ.get(SERVER_FLAG)


class TransportError(Exception):
    """The server could not be started or reached, or it broke a call."""


class Connection:
    """One running server and an MCP session with it."""

    def __init__(self, server: str, env: dict[str, str]):
        self.server, self.module = server, SERVERS[server]
        self.env = env
        self.session = None
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name=f"mcp-{server}", daemon=True)
        self.thread.start()
        self._stop: asyncio.Event | None = None
        started = asyncio.run_coroutine_threadsafe(self._open(), self.loop)
        self._held = None
        try:
            started.result(60)
        except Exception as e:
            self.close()
            raise TransportError(f"The {server} tool server did not start: {e} (see runtime/mcp-{server}.log)") from None

    async def _open(self):
        ready = self.loop.create_future()
        self._held = self.loop.create_task(self._hold(ready))
        await ready

    async def _hold(self, ready: asyncio.Future):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        self._stop = asyncio.Event()
        log_dir = ROOT / "runtime"
        log_dir.mkdir(exist_ok=True)
        params = StdioServerParameters(command=sys.executable, args=["-m", self.module], cwd=str(ROOT),
                                       env={**os.environ, SERVER_FLAG: "1", **self.env})
        try:
            with open(log_dir / f"mcp-{self.server}.log", "a", encoding="utf-8") as log:
                async with stdio_client(params, errlog=log) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self.session = session
                        if not ready.done():
                            ready.set_result(True)
                        await self._stop.wait()
        except BaseException as e:  # noqa: BLE001 - reported to whoever waits for the start
            if not ready.done():
                ready.set_exception(e if isinstance(e, Exception) else RuntimeError(repr(e)))
        finally:
            self.session = None

    @property
    def alive(self) -> bool:
        return self.session is not None and self.thread.is_alive()

    def call(self, tool: str, args: dict) -> Any:
        """The tool's structured answer. A connector error comes back as the connector's own exception."""
        if not self.alive:
            raise TransportError(f"The {self.server} tool server is not running.")
        fut = asyncio.run_coroutine_threadsafe(
            self.session.call_tool(tool, args, read_timeout_seconds=timedelta(seconds=CALL_TIMEOUT)), self.loop)
        try:
            res = fut.result(CALL_TIMEOUT + 10)
        except Exception as e:  # noqa: BLE001 - a transport failure, not a connector error
            raise TransportError(f"The {self.server} tool server failed on {tool}: {type(e).__name__}: {e}") from None
        if res.isError:
            text = "".join(getattr(c, "text", "") for c in res.content)
            raise wire_exception(text)
        out = res.structuredContent
        if out is None:
            raise TransportError(f"The {self.server} tool {tool} gave no structured answer.")
        return out

    def close(self) -> None:
        if self._stop is not None:
            self.loop.call_soon_threadsafe(self._stop.set)
        if self._held is not None:
            try:
                asyncio.run_coroutine_threadsafe(asyncio.wait_for(asyncio.shield(self._held), 10), self.loop).result(12)
            except Exception:  # noqa: BLE001 - closing anyway
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


def wire_exception(text: str) -> Exception:
    """The connector's own exception from a server's error text (cgs_tools/common.py wire_error)."""
    start = text.find("{")
    try:
        data = json.loads(text[start:]) if start >= 0 else {}
    except ValueError:
        data = {}
    msg = data.get("message") or text
    kind = data.get("type", "")
    if kind == "LLMError":
        from api.llm import LLMError
        return LLMError(msg, data.get("kind") or "temporary")
    if kind == "CrmError":
        from api.toolkit.crm import CrmError
        return CrmError(msg)
    if kind == "DriveError":
        from api.toolkit.drive import DriveError
        return DriveError(msg)
    return TransportError(msg)


_lock = threading.Lock()
_open: dict[tuple, Connection] = {}


def connection(server: str, env: dict[str, str] | None = None) -> Connection:
    key = (server, tuple(sorted((env or {}).items())))
    with _lock:
        c = _open.get(key)
        if c is None or not c.alive:
            if c is not None:
                c.close()
            c = _open[key] = Connection(server, dict(env or {}))
        return c


@atexit.register
def close_all() -> None:
    with _lock:
        for c in _open.values():
            c.close()
        _open.clear()


class _Client:
    server = ""
    error: type[Exception] = Exception

    def __init__(self, env: dict[str, str] | None = None):
        self._env = env or {}

    def _call(self, tool: str, **args) -> Any:
        try:
            return connection(self.server, self._env).call(tool, args)
        except TransportError as e:
            raise self.error(str(e)) from None


# ---------- cgs-search ----------
class _Search(_Client):
    server = "cgs-search"

    def __init__(self, name: str, as_of: date | None):
        from api.llm import LLMError
        self.error = lambda msg: LLMError(msg, "temporary")  # a server that cannot be reached is a passing problem
        super().__init__({"WEB_MODE": name})
        self.name, self.as_of = name, as_of

    def _day(self) -> str:
        return self.as_of.isoformat() if self.as_of else ""

    def search(self, query: str, start_date: date | None = None, news: bool = False) -> list[dict]:
        return self._call("web_search", query=query, start_date=start_date.isoformat() if start_date else "", news=news,
                          as_of=self._day())["results"]

    def read(self, url: str) -> dict | None:
        out = self._call("read_page", url=url, as_of=self._day())
        return out["page"] if out.get("found") else None


class _MockSearch(_Search):
    def pages_for(self, company: str, domain: str) -> list[dict]:
        return self._call("company_pages", company=company, domain=domain, as_of=self._day())["results"]


def SearchClient(name: str, as_of: date | None = None) -> _Search:
    """The synthetic web lists a company's own pages directly (pages_for), like MockSearch; a real web does not."""
    return (_MockSearch if name == "mock" else _Search)(name, as_of)


# ---------- cgs-insightly ----------
class CrmClient(_Client):
    server = "cgs-insightly"

    def __init__(self, provider: str):
        from api.toolkit.crm import CrmError
        self.error = CrmError
        super().__init__({"CRM_PROVIDER": provider})
        self.name = provider
        self._cfg: dict | None = None

    @property
    def cfg(self) -> dict:
        if self._cfg is None:
            self._cfg = self._call("crm_info")["config"]
        return self._cfg

    def ready(self) -> bool:
        return bool(self._call("crm_info")["ready"])

    def account(self) -> str:
        return self._call("crm_info")["account"]

    def check_setup(self) -> list[str]:
        return self._call("check_setup")["result"]

    def url(self, kind: str, rid) -> str:
        return self._call("record_url", kind=kind, record_id=str(rid))["text"]

    def matches(self, company: str = "", email: str = "") -> dict:
        return self._call("matches", company=company or "", email=email or "")

    def find_lead(self, workspace_id: str = "", email: str = "") -> dict | None:
        return self._call("find_lead", workspace_id=workspace_id or "", email=email or "")["lead"]

    def get_lead(self, lead_id: str) -> dict | None:
        return self._call("get_lead", lead_id=str(lead_id))["lead"]

    def get_deal(self, deal_id: str) -> dict | None:
        return self._call("get_deal", deal_id=str(deal_id))["deal"]

    def get_task(self, task_id: str) -> dict | None:
        return self._call("get_task", task_id=str(task_id))["task"]

    def save_lead(self, lead: dict, lead_id: str | None = None, skip=()) -> dict:
        return self._call("save_lead", lead=lead, lead_id=str(lead_id or ""), skip=list(skip))["lead"]

    def add_note(self, record: str, rid: str, title: str, text: str) -> str:
        return self._call("add_note", record=record, record_id=str(rid), title=title, text=text)["text"]

    def save_task(self, record: str, rid: str, title: str, due: str | None, owner: str | None = None, task_id: str | None = None,
                  done: bool = False) -> dict:
        return self._call("save_task", record=record, record_id=str(rid), title=title, due=due or "", owner=owner or "",
                          task_id=str(task_id or ""), done=done)["task"]

    def convert(self, lead_id: str, deal: dict) -> dict:
        return self._call("convert", lead_id=str(lead_id), deal=deal)


# ---------- cgs-drive ----------
class DriveClient(_Client):
    server = "cgs-drive"

    def __init__(self, provider: str):
        from api.toolkit.drive import DriveError
        self.error = DriveError
        super().__init__({"DRIVE_PROVIDER": provider})
        self.name = provider

    @staticmethod
    def _files(rows: list[dict]):
        from api.toolkit.drive import DriveFile
        return [DriveFile(**r) for r in rows]

    def ready(self) -> bool:
        return bool(self._call("drive_info")["ready"])

    def account(self) -> str:
        return self._call("drive_info")["account"]

    def walk(self, folders: list[str]):
        out = self._call("walk", folders=list(folders))
        return self._files(out["files"]), out["notes"]

    def folder(self, folder_id: str) -> str | None:
        return self._call("folder", folder_id=folder_id)["name"]

    def children(self, folder_id: str):
        return self._files(self._call("children", folder_id=folder_id)["files"])

    def text(self, f) -> str:
        return self._call("read_doc", file={"id": f.id, "name": f.name, "mime": f.mime, "modified": f.modified, "link": f.link,
                                            "path": f.path})["text"]

    def find_folder(self, name: str, parent: str = "") -> str | None:
        return self._call("find_folder", name=name, parent=parent)["id"]

    def make_folder(self, name: str, parent: str = "") -> str:
        return self._call("make_folder", name=name, parent=parent)["id"]

    def save(self, folder_id: str, name: str, body: str, kind: str = "doc", file_id: str = "") -> dict:
        return self._call("save_record", folder_id=folder_id, name=name, body=body, kind=kind, file_id=file_id or "")
