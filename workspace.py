"""Local workspace entry point with P5 offline functional rehearsal."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import logging
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parent


def main():
    if sys.version_info < (3, 12):
        raise SystemExit("Python 3.12 or newer is required.")
    parser = argparse.ArgumentParser(description="CGS local integration workspace")
    parser.add_argument("command", choices=("check", "serve"), nargs="?", default="check")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    from backend.workspace_config import WorkspaceConfig
    from backend.workspace_runtime import WorkspaceStores, make_workspace_server
    config = WorkspaceConfig.load(ROOT)
    if args.port is not None:
        config = replace(config, port=args.port)
        config.validate()
    stores = WorkspaceStores.open(config)
    if args.command == "check":
        try:
            print(json.dumps({
                "phase": "P5",
                "python": ".".join(map(str, sys.version_info[:3])),
                "research_db": str(config.research_db),
                "qualification_db": str(config.qualification_db),
                "research_runs": len(stores.research.runs()),
                "qualification_cases": len(stores.qualification.bootstrap()["cases"]),
                "providers": config.providers,
                "research_http_routes": True,
                "research_ui": True,
                "opportunity_review": True,
                "qualification_handoff": True,
                "orchestrator_implemented": True,
                "offline_demo_available": True,
            }, indent=2))
        finally:
            stores.close()
        return
    try:
        server = make_workspace_server(config, stores)
    except Exception:
        stores.close()
        raise
    stop = threading.Event()

    def monitor():
        while not stop.is_set():
            try:
                stores.qualification.scan_reminders()
            except Exception:
                logging.exception("Qualification reminder scan failed.")
            stop.wait(30)

    worker = threading.Thread(target=monitor, daemon=True)
    worker.start()
    print(f"CGS local workspace: http://127.0.0.1:{config.port}/", flush=True)
    print("P5: integrated workflow available. Use Start Offline Demo.cmd for the isolated synthetic rehearsal.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        worker.join(timeout=2)
        server.orchestrator_service.shutdown()
        server.research_service.shutdown()
        server.server_close()
        stores.close()


if __name__ == "__main__":
    main()
