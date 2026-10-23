from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import pytest
from pydantic import ValidationError

from backend.research_service import (ResearchService, ResearchServiceError,
                                     ResearchNotFound, ResearchUnavailable)
from lead_agent.providers import ProviderError
from p1_helpers import PreparedProvider, wait_terminal



def test_company_research_saves_exact_sources_config_and_usage(build_service, request_body):
    build, providers = build_service
    service = build()
    status, created = service.start(request_body)
    assert created
    final = wait_terminal(service, status["run_id"])
    assert final["status"] == "completed", final
    assert final["counters"] == {"searches": 1, "extractions": 2, "model_calls": 1}
    assert final["usage"]["promptTokenCount"] == 10
    task = service.store.task(status["run_id"])
    assert task.brief.version == final["theme_version"]
    assert task.knowledge_version.startswith("brief-only:")
    result = service.results(status["run_id"])
    card = result["company_cards"][0]
    assert card["research"]["people"] is None
    assert card["research"]["problem_hypothesis"]
    assert not card["validation_issues"]
    assert card["review"]["decision"] == "Ready for review"
    assert final["approval_basis"] == "mock"
    by_id = {s["source_id"]: s for s in card["sources"]}
    assert all(e["excerpt"] in by_id[e["source_id"]]["content"] for e in card["research"]["evidence"])
    for secret in ("fixture-gemini-secret", "fixture-tavily-secret"):
        assert secret not in json.dumps(result)
        assert secret not in json.dumps(service.store.run(status["run_id"]))


def test_start_is_nonblocking_and_duplicate_does_not_dispatch_twice(build_service, request_body):
    build, _ = build_service
    provider = PreparedProvider(blocked=True)
    service = build(lambda model: provider)
    try:
        first, created = service.start(request_body)
        assert created and provider.entered.wait(2)
        assert service.status(first["run_id"])["status"] == "running"
        second, created = service.start(request_body)
        assert not created and second["run_id"] == first["run_id"]
        assert len(service.store.jobs()) == 1 and provider.calls["search"] == 1
        with pytest.raises(ResearchServiceError, match="different input"):
            service.start({**request_body, "consultant_id": "someone-else"})
        with pytest.raises(ResearchServiceError, match="already running"):
            service.start({**request_body, "request_id": "intent-2"})
        assert len(service.store.jobs()) == 1
    finally:
        provider.release.set()
        wait_terminal(service, first["run_id"])


def test_concurrent_replays_share_one_job(build_service, request_body):
    build, _ = build_service
    provider = PreparedProvider(blocked=True)
    service = build(lambda model: provider)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: service.start(request_body), range(4)))
        assert len({r[0]["run_id"] for r in replies}) == 1
        assert sum(created for _, created in replies) == 1
        assert len(service.store.jobs()) == 1
    finally:
        provider.release.set()
        wait_terminal(service, replies[0][0]["run_id"])


def test_stop_and_resume_preserve_paid_work_and_budget(build_service, request_body):
    build, _ = build_service
    first_provider = PreparedProvider(blocked=True)
    resumed_provider = PreparedProvider()
    providers = iter([first_provider, resumed_provider])
    service = build(lambda model: next(providers))
    first, _ = service.start(request_body)
    try:
        assert first_provider.entered.wait(2)
        assert service.stop(first["run_id"])["status"] == "stopping"
    finally:
        first_provider.release.set()
    paused = wait_terminal(service, first["run_id"])
    assert paused["status"] == "paused" and paused["resume_available"]
    old_limits = paused["limits"]
    old_sources = service.store.run(first["run_id"])["payload"]["sources"]
    service.resume(first["run_id"])
    done = wait_terminal(service, first["run_id"])
    assert done["status"] == "completed"
    assert done["limits"] == old_limits
    assert done["counters"] == {"searches": 1, "extractions": 2, "model_calls": 1}
    assert resumed_provider.calls["search"] == 0
    assert old_sources[0] in service.store.run(first["run_id"])["payload"]["sources"]


def test_provider_failure_is_saved_without_fake_success(build_service, request_body):
    build, _ = build_service
    provider = PreparedProvider(failure=ProviderError("fixture-gemini-secret rejected", "auth"))
    service = build(lambda model: provider)
    run, _ = service.start(request_body)
    final = wait_terminal(service, run["run_id"])
    assert final["status"] == "paused"
    assert final["company_cards_saved"] == 0
    assert final["counters"]["searches"] == 1
    assert service.store.run(run["run_id"])["payload"]["sources"]
    assert "fixture-gemini-secret" not in json.dumps(final)
    assert "[REDACTED]" in json.dumps(final)


def test_missing_credentials_rejects_before_creating_job(environment, request_body, monkeypatch):
    root, store = environment
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    service = ResearchService(root, store, "gemini-test")
    with pytest.raises(ResearchUnavailable, match="not configured"):
        service.start(request_body)
    assert not store.jobs()
    service.shutdown()


@pytest.mark.parametrize("extra", [
    {"model": "another-model"}, {"limits": {"model_calls": 999}},
    {"api_key": "secret-should-not-be-reflected"}, {"companies": []},
])
def test_browser_cannot_supply_secrets_models_limits_or_batch(build_service, request_body, extra):
    build, _ = build_service
    service = build()
    with pytest.raises(ValidationError) as error:
        service.start({**request_body, **extra})
    assert "secret-should-not-be-reflected" not in str(error.value)
    assert not service.store.jobs()


def test_stale_theme_cannot_start_job(build_service, request_body):
    build, _ = build_service
    service = build()
    with pytest.raises(ResearchServiceError, match="theme changed"):
        service.start({**request_body, "theme_version": "outdated"})
    assert not service.store.jobs()


def test_completed_and_limit_tasks_cannot_be_resumed_or_have_budgets_reset(build_service, request_body):
    build, _ = build_service
    service = build()
    first, _ = service.start(request_body)
    final = wait_terminal(service, first["run_id"])
    with pytest.raises(ResearchServiceError):
        service.resume(first["run_id"])
    assert service.stop(first["run_id"])["status"] == "completed"
    with service.store.db() as db:
        db.execute("UPDATE runs SET status='limit' WHERE id=?", (first["run_id"],))
    with pytest.raises(ResearchServiceError):
        service.resume(first["run_id"])
    assert service.status(first["run_id"])["counters"] == final["counters"]


def test_request_replay_survives_restart_and_theme_changes(build_service, request_body):
    build, _ = build_service
    service = build()
    first, _ = service.start(request_body)
    original = wait_terminal(service, first["run_id"])
    service.shutdown()
    path = service.root / "config/problem_brief.json"
    data = json.loads(path.read_text())
    data["version"] = "next-version"
    path.write_text(json.dumps(data), encoding="utf-8")
    restarted = build(lambda model: pytest.fail("Replay must not construct a provider."))
    replay, created = restarted.start(request_body)
    assert not created and replay["run_id"] == first["run_id"]
    assert replay["theme_version"] == original["theme_version"]
    assert restarted.results(first["run_id"])["company_cards"]


def test_expired_worker_is_recoverable_without_automatic_calls(build_service, request_body):
    build, _ = build_service
    service = build()
    first, _ = service.start(request_body)
    original = wait_terminal(service, first["run_id"])
    service.shutdown()
    with service.store.db() as db:
        db.execute("UPDATE runs SET status='running',lease=0,token='orphan' WHERE id=?", (first["run_id"],))
    restarted = build(lambda model: pytest.fail("Recovery must not call a provider automatically."))
    recovered = restarted.status(first["run_id"])
    assert recovered["status"] == "paused" and recovered["resume_available"]
    assert recovered["counters"] == original["counters"]
    assert restarted.results(first["run_id"])["company_cards"]


def test_unexpired_lease_is_not_stolen(build_service, request_body):
    build, _ = build_service
    service = build()
    first, _ = service.start(request_body)
    wait_terminal(service, first["run_id"])
    service.shutdown()
    with service.store.db() as db:
        db.execute("UPDATE runs SET status='running',lease=?,token='other-worker' WHERE id=?",
                   (time.time() + 120, first["run_id"]))
    restarted = build()
    assert restarted.status(first["run_id"])["status"] == "running"
    with pytest.raises(ResearchServiceError):
        restarted.resume(first["run_id"])


def test_unknown_and_legacy_runs_are_not_exposed(build_service):
    build, _ = build_service
    service = build()
    legacy = service.store.create_run({"legacy": True})
    assert service.list_runs() == []
    for run_id in ("missing", legacy):
        with pytest.raises(ResearchNotFound):
            service.results(run_id)


def test_dispatch_failure_can_be_retried_explicitly(build_service, request_body, monkeypatch):
    build, _ = build_service
    service = build()
    with monkeypatch.context() as m:
        m.setattr(threading.Thread, "start", lambda self: (_ for _ in ()).throw(RuntimeError("cannot start")))
        with pytest.raises(ResearchUnavailable):
            service.start(request_body)
    saved = service.list_runs()[0]
    assert saved["status"] == "failed"
    replay, created = service.start(request_body)
    assert not created and replay["status"] == "failed"
    service.resume(saved["run_id"])
    assert wait_terminal(service, saved["run_id"])["status"] == "completed"


def test_shutdown_blocks_new_calls(build_service, request_body):
    build, _ = build_service
    service = build()
    service.shutdown()
    with pytest.raises(ResearchUnavailable):
        service.start(request_body)


def test_real_provider_adapters_run_through_service_with_controlled_http(build_service, request_body, monkeypatch):
    import httpx
    from lead_agent.models import Action
    from lead_agent.providers import LiveProviders

    build, _ = build_service
    fixture_provider = PreparedProvider()
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(request.url.path)
        if request.url.path == "/search":
            assert request.headers["authorization"] == "Bearer fixture-tavily-secret"
            return httpx.Response(200, json=fixture_provider.search(body["query"]))
        if request.url.path == "/extract":
            return httpx.Response(200, json=fixture_provider.read_page(body["urls"][0]))
        assert "generateContent" in request.url.path
        assert request.headers["x-goog-api-key"] == "fixture-gemini-secret"
        prompt = body["systemInstruction"]["parts"][0]["text"]
        record, usage = fixture_provider.research(prompt, [])
        action = Action(tool="record_lead", arguments_json=json.dumps({"research": json.loads(record)}),
                        reason="Controlled transport")
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": action.model_dump_json()}]}}],
            "usageMetadata": usage,
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        service = build(lambda model: LiveProviders("fixture-gemini-secret", "fixture-tavily-secret", model))
        started, _ = service.start(request_body)
        done = wait_terminal(service, started["run_id"])
        service.shutdown()
    assert done["status"] == "completed", done
    assert done["counters"] == {"searches": 1, "extractions": 2, "model_calls": 1}
    assert len(requests) == 4 and any("generateContent" in path for path in requests)
    assert not service.results(started["run_id"])["company_cards"][0]["validation_issues"]
