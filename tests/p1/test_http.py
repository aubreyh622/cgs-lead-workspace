import io
import json
import threading
from dataclasses import replace
from http.client import HTTPConnection
from types import SimpleNamespace

import pytest

from backend.research_http import WorkspaceHandler
from backend.workspace_config import WorkspaceConfig
from backend.workspace_runtime import WorkspaceStores, make_workspace_server
from server import Store as QualificationStore
from p1_helpers import PreparedProvider, wait_terminal


class RequestSocket:
    """Exercise BaseHTTPRequestHandler parsing without a loopback socket."""
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()

    def makefile(self, mode, buffering=-1):
        return self.input

    def sendall(self, data):
        self.output.extend(data)


@pytest.fixture
def parser_api(build_service, environment, tmp_path):
    build, _ = build_service
    service = build()
    root, _ = environment
    store = QualificationStore(tmp_path / "qualification.sqlite3")
    config = WorkspaceConfig.load(root, {"GEMINI_MODEL": "gemini-test"})
    server = SimpleNamespace(server_port=8766, csrf="test-csrf", store=store,
                             research_service=service, workspace_config=config)

    def request(method, path, body=None, headers=None, raw_body=None):
        payload = raw_body if raw_body is not None else (
            json.dumps(body).encode() if body is not None else b"")
        fields = {"Host": "127.0.0.1:8766"}
        if method == "POST":
            fields.update({"Content-Length": str(len(payload)), "Content-Type": "application/json",
                           "X-CSRF-Token": "test-csrf"})
        fields.update(headers or {})
        message = (method + " " + path + " HTTP/1.0\r\n" +
                   "".join(k + ": " + v + "\r\n" for k, v in fields.items()) +
                   "\r\n").encode() + payload
        connection = RequestSocket(message)
        WorkspaceHandler(connection, ("127.0.0.1", 50000), server)
        head, data = bytes(connection.output).split(b"\r\n\r\n", 1)
        return int(head.split()[1]), json.loads(data)

    yield request, service, server
    service.shutdown()
    store.db.close()


def test_http_parser_runs_full_research_and_preserves_qualification(parser_api, request_body):
    request, service, _ = parser_api
    code, before = request("GET", "/api/bootstrap")
    assert code == 200 and before["csrf"] == "test-csrf"
    assert request("GET", "/api/health")[0] == 200
    code, theme = request("GET", "/api/research/theme")
    assert code == 200 and not theme["result"]["single_company_only"]
    assert theme["result"]["discovery_available"]
    assert not theme["result"]["orchestrator_implemented"]
    code, started = request("POST", "/api/research/runs", request_body)
    assert code == 202
    run_id = started["result"]["run_id"]
    assert wait_terminal(service, run_id)["status"] == "completed"
    code, result = request("GET", "/api/research/runs/" + run_id + "/results")
    assert code == 200 and len(result["result"]["company_cards"]) == 1
    code, replay = request("POST", "/api/research/runs", request_body)
    assert code == 200 and replay["result"]["run_id"] == run_id
    assert request("GET", "/api/research/runs")[1]["result"][0]["run_id"] == run_id
    assert request("GET", "/api/research/runs/" + run_id)[1]["result"]["status"] == "completed"
    code, after = request("GET", "/api/bootstrap")
    assert code == 200 and after["cases"] == before["cases"]
    assert after["mode"] == "demo"  # Research is independent of qualification's demo/live switch.
    assert "fixture-gemini-secret" not in json.dumps(result)


@pytest.mark.parametrize("headers", [
    {"X-CSRF-Token": ""}, {"Origin": "https://untrusted.example"},
    {"Host": "untrusted.example"},
])
def test_http_change_boundaries_are_enforced(parser_api, request_body, headers):
    request, service, _ = parser_api
    assert request("POST", "/api/research/runs", request_body, headers)[0] == 403
    assert service.list_runs() == []


def test_http_read_boundary_and_unknown_routes(parser_api):
    request, _, _ = parser_api
    assert request("GET", "/api/research/runs", headers={"Host": "untrusted.example"})[0] == 403
    assert request("GET", "/api/research/runs/missing")[0] == 404
    assert request("GET", "/api/research/unknown")[0] == 404
    assert request("POST", "/api/research/unknown", {})[0] == 404


@pytest.mark.parametrize("raw_body", [b"not-json", b"[]", b""])
def test_http_invalid_json_cannot_create_task(parser_api, raw_body):
    request, service, _ = parser_api
    assert request("POST", "/api/research/runs", raw_body=raw_body)[0] == 400
    assert service.list_runs() == []


def test_http_payload_limits_and_theme_conflicts(parser_api, request_body):
    request, service, _ = parser_api
    assert request("POST", "/api/research/runs", {}, {"Content-Length": "100001"})[0] == 400
    assert request("POST", "/api/research/runs", {**request_body, "model": "another"})[0] == 400
    assert request("POST", "/api/research/runs", {**request_body, "theme_version": "old"})[0] == 409
    assert not service.list_runs()


def test_http_missing_credentials_is_service_error(parser_api, request_body, monkeypatch):
    request, service, _ = parser_api
    from backend.research_service import ResearchService
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    service.provider_factory = ResearchService._provider
    code, error = request("POST", "/api/research/runs", request_body)
    assert code == 503 and "not configured" in error["error"]
    assert not service.list_runs()


def test_http_stop_resume_routes_preserve_task(parser_api, request_body):
    request, service, _ = parser_api
    first = PreparedProvider(blocked=True)
    second = PreparedProvider()
    providers = iter([first, second])
    service.provider_factory = lambda model: next(providers)
    code, reply = request("POST", "/api/research/runs", request_body)
    run_id = reply["result"]["run_id"]
    try:
        assert code == 202 and first.entered.wait(2)
        assert request("POST", "/api/research/runs/" + run_id + "/resume", {})[0] == 409
        code, stopped = request("POST", "/api/research/runs/" + run_id + "/stop", {})
        assert code == 202 and stopped["result"]["status"] == "stopping"
    finally:
        first.release.set()
    assert wait_terminal(service, run_id)["status"] == "paused"
    assert request("POST", "/api/research/runs/" + run_id + "/resume", {"override": True})[0] == 400
    assert request("POST", "/api/research/runs/" + run_id + "/resume", {})[0] == 202
    assert wait_terminal(service, run_id)["status"] == "completed"


@pytest.mark.loopback
def test_actual_loopback_http_roundtrip(environment, request_body):
    root, _ = environment
    config = WorkspaceConfig.load(root, {"GEMINI_MODEL": "gemini-test"})
    stores = WorkspaceStores.open(config)
    # Port zero is an internal test-only ephemeral port, never a public CLI setting.
    server = make_workspace_server(replace(config, port=0), stores,
                                   research_provider_factory=lambda model: PreparedProvider(model))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)

    def request(method, path, body=None, csrf=None):
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    try:
        code, bootstrap = request("GET", "/api/bootstrap")
        assert code == 200
        assert request("POST", "/api/research/runs", request_body)[0] == 403
        code, started = request("POST", "/api/research/runs", request_body, bootstrap["csrf"])
        assert code == 202
        run_id = started["result"]["run_id"]
        assert wait_terminal(server.research_service, run_id)["status"] == "completed"
        code, result = request("GET", "/api/research/runs/" + run_id + "/results")
        assert code == 200 and result["result"]["company_cards"][0]["research"]["evidence"]
        assert request("POST", "/api/research/runs", request_body, bootstrap["csrf"])[0] == 200
        assert request("GET", "/api/bootstrap")[1]["cases"] == bootstrap["cases"]
    finally:
        connection.close()
        server.shutdown()
        server.research_service.shutdown()
        server.server_close()
        stores.close()
