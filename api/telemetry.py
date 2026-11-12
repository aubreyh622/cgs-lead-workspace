"""Traces and metrics of the agents, sent to Google Cloud Trace and Cloud Monitoring (OTEL_TO_CLOUD=on).

ADK records OpenTelemetry spans for every agent run (invoke_agent), every model request (generate_content, with the model
and token counts) and every tool call (execute_tool), and metrics such as gen_ai.client.token.usage and
gen_ai.execute_tool.duration. This also works with our own model wrapper (api/adk_runtime.py ProviderLlm):
tests/v2/test_telemetry.py checks it. With OTEL_TO_CLOUD=on they go to CGS's Google Cloud project through
telemetry.googleapis.com, signed by the service account the workspace runs as. That account needs the roles Cloud Trace Agent
(roles/cloudtrace.agent) and Monitoring Metric Writer (roles/monitoring.metricWriter).

What is sent: names, timings, model names, token counts, tool names. NOT the text of prompts, notes, emails or answers: ADK
leaves message content out unless OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT is set, and this module refuses to
start the export while it is set, unless OTEL_CONTENT_TO_CLOUD=allowed says CGS agreed to it.

The agents' own trace (model turns, tool calls and results, shown on the Agents page) stays in the database as before.
This is the operations view: how long runs take, where they fail, what they cost, over days.
Needs the packages in requirements-cloud.txt (opentelemetry-exporter-otlp-proto-http). Local runs leave it off.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)
CONTENT_VAR = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
STATE = {"status": "off"}


def _content_capture_on() -> bool:
    value = os.environ.get(CONTENT_VAR, "").strip().lower()
    return value not in ("", "false", "0", "no_content")


def setup_cloud_telemetry() -> str:
    """Start sending traces and metrics to Google Cloud if OTEL_TO_CLOUD=on. Returns a one-line status. Never stops the
    workspace from starting: a problem is reported in the status and the workspace runs without the export."""
    if os.environ.get("OTEL_TO_CLOUD", "off").strip().lower() not in ("on", "true", "1"):
        STATE["status"] = "off"
        return STATE["status"]
    if _content_capture_on() and os.environ.get("OTEL_CONTENT_TO_CLOUD", "").strip().lower() != "allowed":
        STATE["status"] = (f"not started: {CONTENT_VAR} would send the text of prompts and answers to Google Cloud. "
                           "Unset it, or set OTEL_CONTENT_TO_CLOUD=allowed if CGS agreed.")
        return STATE["status"]
    try:
        import opentelemetry.exporter.otlp.proto.http.trace_exporter  # noqa: F401 - ADK skips export silently without it
    except ImportError:
        STATE["status"] = "not started: install requirements-cloud.txt (opentelemetry-exporter-otlp-proto-http)"
        return STATE["status"]
    os.environ.setdefault("OTEL_SERVICE_NAME", "cgs-workspace")
    try:
        from google.adk.telemetry.google_cloud import get_gcp_exporters
        from google.adk.telemetry.setup import maybe_set_otel_providers
        hooks = get_gcp_exporters(enable_cloud_tracing=True, enable_cloud_metrics=True)
        if not hooks.span_processors:
            STATE["status"] = "not started: no Google Cloud project found for the credentials (set GOOGLE_CLOUD_PROJECT)"
            return STATE["status"]
        maybe_set_otel_providers([hooks])
    except Exception as e:  # noqa: BLE001 - credentials, network, packages: report, keep the workspace running
        log.warning("Cloud telemetry not started: %s", e)
        STATE["status"] = f"not started: {type(e).__name__}: {e}"[:300]
        return STATE["status"]
    STATE["status"] = "on: traces to Cloud Trace, metrics to Cloud Monitoring (no message text)"
    return STATE["status"]
