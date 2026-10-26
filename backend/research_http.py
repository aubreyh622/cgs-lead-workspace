"""Research routes layered onto the existing qualification HTTP handler."""
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from server import Handler, Conflict
from .research_service import ResearchServiceError


class WorkspaceHandler(Handler):
    def do_GET(self):
        path = urlsplit(self.path).path.strip("/").split("/")
        if path == ["api", "bootstrap"] and getattr(self.server, "offline_demo", False):
            if not self.valid_host():
                return self.send_json(403, {"error": "Local workspace access only."})
            data = self.server.store.bootstrap()
            data.update(csrf=self.server.csrf, offlineDemo=True)
            return self.send_json(200, data)
        if path == ["research.js"]:
            if not self.valid_host():
                return self.send_json(403, {"error": "Local workspace access only."})
            data = (Path(__file__).resolve().parents[1] / "dist/research.js").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            return self.wfile.write(data)
        if path[:2] != ["api", "research"]:
            return super().do_GET()
        if not self.valid_host():
            return self.send_json(403, {"error": "Local workspace access only."})
        try:
            service = self.server.research_service
            if path == ["api", "research", "theme"]:
                query = parse_qs(urlsplit(self.path).query)
                consultant = query.get("consultant_id", [None])[0]
                if consultant and len(consultant) > 160:
                    raise ValueError("Consultant ID is too long.")
                brief, rubric = service.theme(consultant)
                result = {
                    "brief": brief.model_dump(mode="json"),
                    "rubric_version": rubric.version,
                    "research": self.server.workspace_config.providers["research"],
                    "single_company_only": service.demo_companies is not None,
                    "discovery_available": service.demo_companies is None,
                    "discovery_limits": service.discovery_limits().model_dump(),
                    "limits": service.limits().model_dump(),
                    "orchestrator_implemented": hasattr(self.server, "orchestrator_service"),
                    "effective_consultant_id": consultant,
                    "demo_companies": [{"company": name, "domain": host} for host, name in (service.demo_companies or {}).items()],
                }
            elif path == ["api", "research", "orchestrator"]:
                query = parse_qs(urlsplit(self.path).query)
                result = self.server.orchestrator_service.dashboard(
                    query.get("consultant_id", ["local-consultant"])[0], query.get("scope", ["theme"])[0])
                result["configuration"] = self.server.workspace_config.providers["orchestrator"]
            elif len(path) == 5 and path[:4] == ["api", "research", "orchestrator", "reviews"]:
                result = self.server.orchestrator_service.get_review(path[4])
            elif path == ["api", "research", "runs"]:
                result = service.list_runs()
            elif path == ["api", "research", "opportunities"]:
                result = self.server.opportunity_service.list()
            elif path == ["api", "research", "handoffs"]:
                result = self.server.handoff_service.list()
            elif len(path) == 4 and path[:3] == ["api", "research", "handoffs"]:
                result = self.server.handoff_service.get(path[3])
            elif len(path) == 4 and path[:3] == ["api", "research", "opportunities"]:
                result = self.server.opportunity_service.get(path[3])
            elif len(path) == 4 and path[:3] == ["api", "research", "runs"]:
                result = service.status(path[3])
            elif len(path) == 5 and path[:3] == ["api", "research", "runs"] and path[4] == "results":
                result = service.results(path[3])
            else:
                return self.send_json(404, {"error": "Research API route not found."})
            self.send_json(200, {"ok": True, "result": result})
        except ResearchServiceError as exc:
            self.send_json(exc.status, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception:
            self.send_json(500, {"error": "Could not read research records."})

    def do_POST(self):
        path = urlsplit(self.path).path.strip("/").split("/")
        case_route = len(path) == 4 and path[:2] == ["api", "cases"] and path[3] in ("contact", "contacted")
        reset_route = path == ["api", "demo", "reset"]
        if path[:2] != ["api", "research"] and not case_route and not reset_route:
            return super().do_POST()
        if not self.valid_host():
            return self.send_json(403, {"error": "Local workspace access only."})
        origin = self.headers.get("Origin")
        allowed = ("http://127.0.0.1:" + str(self.server.server_port),
                   "http://localhost:" + str(self.server.server_port))
        if origin and origin not in allowed:
            return self.send_json(403, {"error": "Cross-origin changes are not allowed."})
        if self.headers.get("X-CSRF-Token") != self.server.csrf:
            return self.send_json(403, {"error": "Refresh the workspace before making changes."})
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 1 <= size <= 100000:
                raise ValueError("Request body is empty or too large.")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object.")
            service = self.server.research_service
            status = 202
            if path == ["api", "research", "orchestrator", "reviews"]:
                result, created = self.server.orchestrator_service.analyze(body)
                status = 202 if created else 200
            elif path == ["api", "research", "orchestrator", "proposals"]:
                result, created = self.server.orchestrator_service.create_proposal(body)
                status = 201 if created else 200
            elif len(path) == 6 and path[:4] == ["api", "research", "orchestrator", "proposals"] and path[5] == "decision":
                result, created = self.server.orchestrator_service.decide(path[4], body)
                status = 201 if created else 200
            elif case_route:
                handoffs = self.server.handoff_service
                result = handoffs.confirm_contact(path[2], body) if path[3] == "contact" else handoffs.record_contacted(path[2], body)
                status = 200
            elif reset_route:
                if body.get("confirm") != "RESET":
                    raise ValueError("Explicit reset confirmation is required.")
                with self.server.store.lock:
                    self.server.handoff_service.guard_reset()
                    self.server.store.reset()
                result, status = None, 200
            elif (len(path) == 5 and path[:3] == ["api", "research", "opportunities"] and path[4] == "handoff"):
                result, created = self.server.handoff_service.submit(path[3], body)
                status = 201 if created else 200
            elif (len(path) == 5 and path[:3] == ["api", "research", "handoffs"] and path[4] == "decision"):
                result, created = self.server.handoff_service.decide(path[3], body)
                status = 201 if created else 200
            elif path == ["api", "research", "runs"]:
                result, created = service.start(body)
                status = 202 if created else 200
            elif path == ["api", "research", "opportunities"]:
                result, created = self.server.opportunity_service.create(body)
                status = 201 if created else 200
            elif (len(path) == 5 and path[:3] == ["api", "research", "opportunities"]
                  and path[4] == "feedback"):
                result, created = self.server.opportunity_service.review(path[3], body)
                status = 201 if created else 200
            elif (len(path) == 5 and path[:3] == ["api", "research", "runs"]
                  and path[4] in ("stop", "resume")):
                if body:
                    raise ValueError("Stop/resume accepts an empty JSON object.")
                result = service.stop(path[3]) if path[4] == "stop" else service.resume(path[3])
            else:
                return self.send_json(404, {"error": "Research API route not found."})
            self.send_json(status, {"ok": True, "result": result})
        except ResearchServiceError as exc:
            self.send_json(exc.status, {"error": str(exc)})
        except Conflict as exc:
            self.send_json(409, {"error": str(exc)})
        except KeyError:
            self.send_json(404, {"error": "Qualification case not found."})
        except (ValueError, TypeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception:
            self.send_json(500, {"error": "Research operation failed; inspect the saved task before retrying."})
