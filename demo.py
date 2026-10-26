"""Explicit isolated offline demo; never reads .env or calls external providers."""
import argparse
import json
from pathlib import Path

from backend.demo_workflow import PROJECT_ROOT, close_demo, demo_server, prepare_demo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-dir", type=Path, default=PROJECT_ROOT/"runtime/offline-demo")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("Use port 0 for an ephemeral test port, or 1-65535.")
    report = prepare_demo(args.workspace_dir)
    if args.prepare_only:
        print(json.dumps({"offline_synthetic":True,"passed":report["passed"],"checks":len(report["checks"]),
                          "evidence":str(args.workspace_dir.resolve()/"pilot-evidence.json")}))
        return
    server, stores = demo_server(args.workspace_dir, args.port)
    print(json.dumps({"status":"ready","url":f"http://127.0.0.1:{server.server_port}/",
                      "offline_synthetic":True,"workspace_dir":str(args.workspace_dir.resolve())}),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        close_demo(server,stores)


if __name__ == "__main__":
    main()
