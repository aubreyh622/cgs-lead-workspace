from dataclasses import replace
from http.client import HTTPConnection
import json
import threading

import pytest

from backend.workspace_config import WorkspaceConfig
from backend.workspace_runtime import WorkspaceStores, make_workspace_server
from p1_helpers import PreparedProvider, wait_terminal
from p4_helpers import wait_review


@pytest.mark.loopback
def test_manager_http_proposal_review_approval_and_effective_theme(manager_environment,proposal_body):
    root=manager_environment[0]
    config=WorkspaceConfig.load(root,{'GEMINI_MODEL':'gemini-test'})
    stores=WorkspaceStores.open(config)
    server=make_workspace_server(replace(config,port=0),stores,
        research_provider_factory=lambda model:PreparedProvider(model),
        manager_provider_factory=lambda model:manager_environment[5])
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    connection=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
    def request(method,path,body=None,token=None):
        headers={'Content-Type':'application/json'}
        if token: headers['X-CSRF-Token']=token
        connection.request(method,path,json.dumps(body) if body is not None else None,headers)
        response=connection.getresponse();return response.status,json.loads(response.read())
    try:
        _,bootstrap=request('GET','/api/bootstrap');token=bootstrap['csrf']
        assert request('GET','/api/research/orchestrator?consultant_id=consultant-a&scope=theme')[0]==200
        assert request('GET','/api/research/orchestrator?scope=invalid')[0]==400
        path='/api/research/orchestrator/proposals'
        assert request('POST',path,proposal_body)[0]==403
        code,proposed=request('POST',path,proposal_body,token);assert code==201
        assert request('POST',path,proposal_body,token)[0]==200
        ai={k:v for k,v in proposal_body.items() if k not in ('patch','rationale')}
        ai['request_id']='http-analysis'
        code,review=request('POST','/api/research/orchestrator/reviews',ai,token);assert code==202
        assert wait_review(server.orchestrator_service,review['result']['id'])['status']=='completed'
        assert request('GET','/api/research/orchestrator/reviews/'+review['result']['id'])[0]==200
        key=proposed['result']['proposal']['proposal_id']
        decision={'request_id':'approve-http','status':'approved','reviewer':'human','reason':'Reviewed the bounded diff.'}
        code,approved=request('POST',path+'/'+key+'/decision',decision,token);assert code==201
        assert request('POST',path+'/'+key+'/decision',decision,token)[0]==200
        _,theme=request('GET','/api/research/theme?consultant_id=consultant-a')
        assert theme['result']['orchestrator_implemented']
        assert theme['result']['brief']['version']==approved['result']['proposal']['resulting_theme_version']
        body={'request_id':'research-http-next','consultant_id':'consultant-a','theme_version':theme['result']['brief']['version'],
            'company':{'company':'Synthetic Factory 1','domain':'synthetic-factory-1.example'}}
        code,run=request('POST','/api/research/runs',body,token);assert code==202
        assert wait_terminal(server.research_service,run['result']['run_id'])['status']=='completed'
        assert run['result']['theme_version']==theme['result']['brief']['version']
        assert request('GET','/api/bootstrap')[1]['cases']==bootstrap['cases']
    finally:
        connection.close();server.shutdown();server.orchestrator_service.shutdown()
        server.research_service.shutdown();server.server_close();stores.close()
