"""Traces and metrics to Google Cloud (api/telemetry.py, OTEL_TO_CLOUD): off unless asked, never with message text unless CGS
agreed, never stops the workspace, and ADK's spans really come from our agents (with our own model wrapper)."""
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("google.adk")

from api import telemetry as T  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def test_off_unless_asked(monkeypatch):
    monkeypatch.delenv("OTEL_TO_CLOUD", raising=False)
    assert T.setup_cloud_telemetry() == "off"


def test_refuses_while_message_text_would_be_sent(monkeypatch):
    monkeypatch.setenv("OTEL_TO_CLOUD", "on")
    monkeypatch.setenv(T.CONTENT_VAR, "true")
    monkeypatch.delenv("OTEL_CONTENT_TO_CLOUD", raising=False)
    assert T.setup_cloud_telemetry().startswith("not started") and "OTEL_CONTENT_TO_CLOUD" in T.STATE["status"]


def test_starts_traces_and_metrics_with_the_service_account(monkeypatch):
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    import google.adk.telemetry.google_cloud as G
    import google.adk.telemetry.setup as SU
    from google.adk.telemetry.setup import OTelHooks
    calls = {}
    monkeypatch.setenv("OTEL_TO_CLOUD", "on")
    monkeypatch.delenv(T.CONTENT_VAR, raising=False)
    monkeypatch.setattr(G, "get_gcp_exporters", lambda **kw: calls.setdefault("exporters", kw) and OTelHooks(span_processors=["span"]))
    monkeypatch.setattr(SU, "maybe_set_otel_providers", lambda hooks: calls.setdefault("set", hooks))
    assert T.setup_cloud_telemetry().startswith("on")
    assert calls["exporters"] == {"enable_cloud_tracing": True, "enable_cloud_metrics": True} and calls["set"]


def test_a_problem_is_reported_not_raised(monkeypatch):
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    import google.adk.telemetry.google_cloud as G
    from google.adk.telemetry.setup import OTelHooks
    monkeypatch.setenv("OTEL_TO_CLOUD", "on")
    monkeypatch.delenv(T.CONTENT_VAR, raising=False)
    monkeypatch.setattr(G, "get_gcp_exporters", lambda **kw: OTelHooks())
    assert "no Google Cloud project" in T.setup_cloud_telemetry()

    def boom(**kw):
        raise RuntimeError("no credentials")
    monkeypatch.setattr(G, "get_gcp_exporters", boom)
    assert T.setup_cloud_telemetry() == "not started: RuntimeError: no credentials"


PROBE = r'''
import sys
sys.path[:0] = [{root!r}, {tests!r}]
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
exp = InMemorySpanExporter(); tp = TracerProvider(); tp.add_span_processor(SimpleSpanProcessor(exp)); trace.set_tracer_provider(tp)
from api import adk_runtime as A
from test_adk_runtime import Script, gemini, call, spec
from api.llm import make_provider
ai = Script(gemini(call("echo", {{"word": "SECRET-NOTE-TEXT"}})), gemini(call("finish", {{"answer": "SECRET-ANSWER"}})))
A.run(spec(), make_provider("gemini", "m", key="k", transport=ai), "SECRET-TASK-TEXT")
for s in exp.get_finished_spans():
    print(s.name, "|", " ".join(f"{{k}}={{v}}" for k, v in (s.attributes or {{}}).items()))
'''


def test_adk_spans_come_from_our_agents_without_message_text():
    """In a process of its own: the OpenTelemetry tracer provider can be set only once per process."""
    code = PROBE.format(root=str(ROOT), tests=str(ROOT / "tests" / "v2"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    lines = out.stdout.splitlines()
    names = [l.split(" |")[0] for l in lines]
    assert "invoke_agent test_agent" in names and "execute_tool echo" in names and names.count("generate_content gemini:m") == 2
    assert any("gen_ai.usage.input_tokens=7" in l for l in lines)
    assert "SECRET" not in out.stdout  # no task, note or answer text in any span


def test_config_shows_gemini_backend_and_telemetry(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from api.main import create_app
    from api.settings import Settings
    monkeypatch.setenv("GEMINI_BACKEND", "ai_studio")
    cfg = TestClient(create_app(Settings(app_mode="demo", db_path=tmp_path / "ws.sqlite3"))).get("/api/config").json()
    assert cfg["gemini"] == "AI Studio (API key)" and "cloudTelemetry" in cfg
