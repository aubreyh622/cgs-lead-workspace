from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from p4_helpers import ControlledAnalysis, feedback, wait_review
from p1_helpers import PreparedProvider, wait_terminal
from backend.orchestrator_service import OrchestratorService, FeedbackModel
from backend.research_service import ResearchServiceError, ResearchUnavailable
from backend.theme_store import ThemeStore
from lead_agent.providers import ProviderError

DECISION = {"request_id": "approve-1", "status": "approved", "reviewer": "human-reviewer", "reason": "The selected feedback supports this limited change."}


def test_manual_proposal_approval_versions_and_restart(manager_environment, proposal_body):
    root, research, _, _, themes, provider, manager, _ = manager_environment
    baseline = themes.current().model_dump(mode="json")
    files = {p.name:p.read_bytes() for p in (root/"config").glob('*.json')}
    proposed, created = manager.create_proposal(proposal_body)
    key = proposed["proposal"]["proposal_id"]
    assert created and proposed["proposal"]["status"] == "pending_review"
    assert themes.current().version == baseline["version"] and provider.calls == 0
    assert proposed["diff"]["signals"]["after"] == proposal_body["patch"]["signals"]
    assert manager.create_proposal(proposal_body) == (proposed, False)
    approved, changed = manager.decide(key, DECISION)
    assert changed and approved["proposal"]["status"] == "approved"
    updated = themes.current()
    assert updated.version != baseline["version"] and updated.signals == proposal_body["patch"]["signals"]
    assert updated.approval_basis == "mock" and updated.approved_by == "human-reviewer"
    assert manager.decide(key, DECISION) == (approved, False)
    assert len(themes.versions()) == 2
    assert ThemeStore(root, research.store).current() == updated
    assert all((root/"config"/name).read_bytes() == content for name,content in files.items())
    assert {k:updated.model_dump(mode="json")[k] for k in ("industry","geography","capability")} == {k:baseline[k] for k in ("industry","geography","capability")}


@pytest.mark.parametrize("patch", [{"weights":{"B":100}}, {"model":"another"}, {"industry":"Retail"},
    {"approval_basis":"cgs"}, {"limits":{"model_calls":99}}, {}, {"problem":"short"}])
def test_disallowed_proposal_fields_cannot_be_saved(manager_environment, proposal_body, patch):
    with pytest.raises(ValueError):
        manager_environment[6].create_proposal({**proposal_body,"patch":patch})
    assert len(manager_environment[4].versions()) == 1


@pytest.mark.parametrize("change", [{"feedback_ids":["invented"]}, {"scope":"cgs"}, {"base_theme_version":"old"},
    {"feedback_ids":[]}, {"rationale":" "}, {"scope":"opportunity"}])
def test_missing_or_wrong_context_is_rejected(manager_environment, proposal_body, change):
    with pytest.raises((ValueError,ResearchServiceError)):
        manager_environment[6].create_proposal({**proposal_body,**change})


def test_personal_scope_does_not_change_other_consultants_or_global_direction(manager_environment):
    manager, themes = manager_environment[6], manager_environment[4]
    f = feedback(manager_environment,"consultant")
    body = {"request_id":"personal","consultant_id":"consultant-a","scope":"consultant","feedback_ids":[f["feedback_id"]],
        "base_theme_version":f["theme_version"],"patch":{"buyer_roles":["Integration programme leader"]},"rationale":"A personal research preference."}
    with pytest.raises(ValueError):
        manager.create_proposal({**body,"consultant_id":"consultant-b"})
    proposed,_ = manager.create_proposal(body)
    manager.decide(proposed["proposal"]["proposal_id"],DECISION)
    assert themes.current("consultant-a").buyer_roles == body["patch"]["buyer_roles"]
    assert themes.current("consultant-b").version == f["theme_version"] == themes.current().version
    research=manager_environment[1]
    for consultant,version in (("consultant-b",f["theme_version"]),("consultant-a",themes.current("consultant-a").version)):
        run,_=research.start({"request_id":"personal-research-"+consultant,"consultant_id":consultant,
            "company":{"company":"Synthetic Factory 1","domain":"synthetic-factory-1.example"}})
        assert wait_terminal(research,run['run_id'])['status']=='completed'
        assert run['theme_version']==version


def test_stale_and_rejected_proposals_do_not_overwrite_current_direction(manager_environment, proposal_body):
    manager, themes = manager_environment[6], manager_environment[4]
    first,_=manager.create_proposal(proposal_body)
    second,_=manager.create_proposal({**proposal_body,"request_id":"second","patch":{"signals":["Another focused signal"]}})
    manager.decide(first["proposal"]["proposal_id"],DECISION)
    current=themes.current()
    with pytest.raises(ResearchServiceError):
        manager.decide(second["proposal"]["proposal_id"],{**DECISION,"request_id":"stale-approval"})
    rejected,_=manager.decide(second["proposal"]["proposal_id"],{**DECISION,"request_id":"reject","status":"rejected"})
    assert rejected["proposal"]["resulting_theme_version"] is None and themes.current()==current


def test_superseded_feedback_cannot_justify_approval(manager_environment, proposal_body):
    manager, opportunities = manager_environment[6], manager_environment[2]
    proposed,_=manager.create_proposal(proposal_body)
    f=proposed["feedback_snapshot"][0]
    opportunities.review(f["opportunity_id"],{"request_id":"newer-feedback","revision":2,"consultant_id":"consultant-a",
        "outcome":"defer","reason_code":"timing","scope":"theme"})
    with pytest.raises(ResearchServiceError):
        manager.decide(proposed["proposal"]["proposal_id"],DECISION)


def test_concurrent_approvals_and_atomic_rollback(manager_environment, proposal_body, monkeypatch):
    manager, themes = manager_environment[6], manager_environment[4]
    proposed,_=manager.create_proposal(proposal_body)
    key=proposed["proposal"]["proposal_id"]
    original=manager._remember
    with monkeypatch.context() as patcher:
        patcher.setattr(manager,"_remember",lambda *a: (_ for _ in ()).throw(RuntimeError("injected")))
        with pytest.raises(RuntimeError): manager.decide(key,DECISION)
    assert len(themes.versions())==1
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies=list(executor.map(lambda _:manager.decide(key,DECISION),range(2)))
    assert sorted(r[1] for r in replies)==[False,True] and len(themes.versions())==2


def test_ai_review_is_bounded_idempotent_and_only_creates_a_pending_proposal(manager_environment, proposal_body):
    manager, provider, themes = manager_environment[6], manager_environment[5], manager_environment[4]
    body={k:v for k,v in proposal_body.items() if k not in ("patch","rationale")}
    review,created=manager.analyze(body)
    result=wait_review(manager,review["id"])
    assert created and result["status"]=="completed" and result["calls"]==provider.calls==1
    assert manager.analyze(body)[1] is False and provider.calls==1
    assert result["result"]["proposal_id"] and len(themes.versions())==1
    assert manager.dashboard("consultant-a","theme")["proposals"][0]["proposal"]["status"]=="pending_review"


@pytest.mark.parametrize("patch,status", [({},"completed"),({"weights":{"B":99}},"failed")])
def test_no_change_and_invalid_model_output_do_not_change_themes(manager_environment, proposal_body, patch, status):
    manager=manager_environment[6]
    provider=ControlledAnalysis(patch=patch)
    manager.provider_factory=lambda model:provider
    body={k:v for k,v in proposal_body.items() if k not in ("patch","rationale")}
    review,_=manager.analyze(body)
    result=wait_review(manager,review["id"])
    assert result["status"]==status and len(manager_environment[4].versions())==1
    assert not manager.dashboard("consultant-a","theme")["proposals"]


def test_provider_failure_redacts_and_does_not_retry(manager_environment, proposal_body):
    manager=manager_environment[6]
    provider=ControlledAnalysis(failure=ProviderError("Rejected controlled-gemini-secret","temporary"))
    manager.provider_factory=lambda model:provider
    body={k:v for k,v in proposal_body.items() if k not in ("patch","rationale")}
    review,_=manager.analyze(body)
    result=wait_review(manager,review["id"])
    assert result["status"]=="failed" and "controlled-gemini-secret" not in json.dumps(result)
    assert provider.calls==1 and manager.analyze(body)[1] is False


def test_missing_credentials_does_not_create_a_fake_review(manager_environment, proposal_body):
    manager=manager_environment[6]
    manager.provider_factory=lambda model:FeedbackModel("","",model)
    body={k:v for k,v in proposal_body.items() if k not in ("patch","rationale")}
    with pytest.raises(ResearchUnavailable): manager.analyze(body)
    assert manager.dashboard("consultant-a","theme")["reviews"]==[]


def test_next_research_uses_approved_version_in_search_and_model_input(manager_environment, proposal_body):
    _,research,_,_,themes,_,manager,old_lead=manager_environment
    proposed,_=manager.create_proposal(proposal_body)
    manager.decide(proposed["proposal"]["proposal_id"],DECISION)
    queries,contexts=[],[]
    class Capture(PreparedProvider):
        def search(self,query,**kwargs):
            queries.append(query);return super().search(query,**kwargs)
        def research(self,prompt,*args,**kwargs):
            contexts.append(prompt);return super().research(prompt,*args,**kwargs)
    research.provider_factory=lambda model:Capture(model)
    run,_=research.start({"request_id":"next-round","consultant_id":"consultant-a","company":{
        "company":"Synthetic Factory 1","domain":"synthetic-factory-1.example"}})
    assert wait_terminal(research,run["run_id"])["status"]=="completed"
    assert run["theme_version"]==themes.current().version
    signal=proposal_body["patch"]["signals"][0]
    assert any(signal in q for q in queries) and any(signal in p for p in contexts)
    assert research.store.task(old_lead.run_id).brief.version==proposal_body["base_theme_version"]


def test_kpi_counts_latest_reviews_and_preserves_unknown_durations(manager_environment):
    manager=manager_environment[6]
    first=manager.dashboard("consultant-a","theme")["kpis"]
    assert first["review_minutes_median"] is None and first["worth_exploring_rate"] is None
    assert first["recorded_response_rate"] is None
    feedback(manager_environment,minutes=None)
    feedback(manager_environment,request_suffix="2",minutes=6)
    kpi=manager.dashboard("consultant-a","theme")["kpis"]
    assert kpi["reviewed_opportunities"]==2 and kpi["feedback_records"]==2
    assert kpi["review_minutes_median"]==6 and kpi["review_minutes_samples"]==1
    assert kpi["worth_exploring_rate"]==0 and kpi["synthetic_opportunities"]==2


def test_real_gemini_adapter_with_controlled_transport(manager_environment, proposal_body, monkeypatch):
    import httpx
    manager=manager_environment[6]
    calls=[]
    def respond(request):
        calls.append(request)
        data=json.loads(request.content)
        assert "untrusted DATA" in data["systemInstruction"]["parts"][0]["text"]
        assert request.headers["x-goog-api-key"]=="controlled-key"
        assert 'patch_json' not in data['generationConfig']['responseJsonSchema']['properties']
        context = json.loads(data['contents'][0]['parts'][0]['text'])
        assert context['selected_feedback_metrics']['sample_size'] == len(proposal_body['feedback_ids'])
        assert context['research_context'][0]['evidence']
        result={"summary":"Focused signals requested.",
            'problem':None, 'signals':None, 'buyer_roles':None, 'exclusions':None, 'excluded_domains':None,
            **proposal_body['patch'],
            "rationale":"Selected theme feedback supports a focused signal.","feedback_ids":proposal_body["feedback_ids"]}
        return httpx.Response(200,json={"candidates":[{"content":{"parts":[{"text":json.dumps(result)}]}}],"usageMetadata":{"promptTokenCount":12}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(httpx,"post",client.post)
        manager.provider_factory=lambda model:FeedbackModel("controlled-key","",model)
        review,_=manager.analyze({k:v for k,v in proposal_body.items() if k not in ("patch","rationale")})
        assert wait_review(manager,review["id"])["status"]=="completed"
    assert len(calls)==1 and "controlled-key" not in json.dumps(manager.get_review(review["id"]))


def test_review_busy_and_interrupted_work_never_restarts_automatically(manager_environment, proposal_body):
    manager=manager_environment[6]
    provider=ControlledAnalysis(blocked=True)
    manager.provider_factory=lambda model:provider
    body={k:v for k,v in proposal_body.items() if k not in ("patch","rationale")}
    try:
        review,_=manager.analyze(body)
        assert provider.entered.wait(2)
        with pytest.raises(ResearchServiceError): manager.analyze({**body,"request_id":"second-call"})
        assert manager.analyze(body)[1] is False
    finally:
        provider.release.set()
    assert wait_review(manager,review["id"])["status"]=="completed" and provider.calls==1
    # Simulate a previous process dying after a reserved model call.
    with manager.store.db() as db:
        db.execute("UPDATE workspace_manager_reviews SET status='running',result=NULL,lease=0 WHERE id=?",(review["id"],))
    manager._reconcile()
    assert manager.get_review(review["id"])["status"]=="failed" and provider.calls==1


def test_approval_race_is_checked_inside_new_research_transaction(manager_environment, proposal_body, monkeypatch):
    _,research,_,_,themes,_,manager,_=manager_environment
    proposed,_=manager.create_proposal(proposal_body)
    original=research.store.create_job
    def raced(*args,**kwargs):
        manager.decide(proposed["proposal"]["proposal_id"],DECISION)
        return original(*args,**kwargs)
    monkeypatch.setattr(research.store,'create_job',raced)
    with pytest.raises(ResearchServiceError):
        research.start({"request_id":"racing-task","consultant_id":"consultant-a","company":{
            "company":"Synthetic Factory 1","domain":"synthetic-factory-1.example"}})
    assert len(research.list_runs())==1  # Only the historical synthetic sample.


def test_cgs_scope_and_excluded_domain_are_enforced(manager_environment):
    manager, research=manager_environment[6],manager_environment[1]
    f=feedback(manager_environment,'cgs')
    proposed,_=manager.create_proposal({"request_id":"exclude","consultant_id":"consultant-a","scope":"cgs",
        "feedback_ids":[f['feedback_id']],"base_theme_version":f['theme_version'],
        "patch":{"excluded_domains":["synthetic-factory-1.example"]},"rationale":"A CGS-scope exclusion based on feedback."})
    manager.decide(proposed['proposal']['proposal_id'],DECISION)
    with pytest.raises(ResearchServiceError):
        research.start({"request_id":"excluded-task","consultant_id":"other-consultant","company":{
            "company":"Synthetic Factory 1","domain":"synthetic-factory-1.example"}})


def test_default_direction_queries_respect_existing_search_length_limit(manager_environment):
    research=manager_environment[1]
    queries=[]
    class Capture(PreparedProvider):
        def search(self,query,**kwargs):
            assert 3<=len(query)<=500
            queries.append(query);return super().search(query,**kwargs)
    research.provider_factory=lambda model:Capture(model)
    run,_=research.start({"request_id":"default-direction","consultant_id":"consultant-a","company":{
        "company":"Synthetic Factory 1","domain":"synthetic-factory-1.example"}})
    assert wait_terminal(research,run['run_id'])['status']=='completed' and queries


@pytest.mark.parametrize('change',[{'reviewer':' '},{'reason':' '},{'status':'auto_approved'}])
def test_direction_decision_requires_explicit_valid_human_fields(manager_environment,proposal_body,change):
    manager=manager_environment[6]
    proposed,_=manager.create_proposal(proposal_body)
    with pytest.raises(ValueError):
        manager.decide(proposed['proposal']['proposal_id'],{**DECISION,**change})
    assert len(manager_environment[4].versions())==1


def test_reused_request_and_noop_patch_do_not_create_another_proposal(manager_environment,proposal_body):
    manager=manager_environment[6]
    manager.create_proposal(proposal_body)
    with pytest.raises(ResearchServiceError):
        manager.create_proposal({**proposal_body,'rationale':'Changed same request'})
    with pytest.raises(ValueError):
        manager.create_proposal({**proposal_body,'request_id':'noop','patch':{'signals':manager_environment[4].current().signals}})
    assert len(manager.dashboard('consultant-a','theme')['proposals'])==1
