from dataclasses import replace
from datetime import date
from http.client import HTTPConnection
import json
import threading

import pytest

from backend.workspace_config import WorkspaceConfig
from backend.workspace_runtime import WorkspaceStores, make_workspace_server


@pytest.mark.loopback
def test_handoff_and_contact_http_lifecycle(handoff_environment, acceptance):
    root, _, _, _, _, _, opportunity = handoff_environment
    config = WorkspaceConfig.load(root, {})
    stores = WorkspaceStores.open(config)
    server = make_workspace_server(replace(config, port=0), stores)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    def request(method, path, body=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-CSRF-Token'] = token
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    try:
        _, bootstrap = request('GET', '/api/bootstrap')
        token = bootstrap['csrf']
        path = '/api/research/opportunities/'+opportunity+'/handoff'
        body = {'request_id':'submit-http', 'revision':2}
        assert request('POST', path, body)[0] == 403
        code, pending = request('POST', path, body, token)
        assert code == 201
        key = pending['result']['receipt']['handoff_id']
        assert request('GET', '/api/research/handoffs/'+key)[0] == 200
        assert request('GET', '/api/research/handoffs/missing')[0] == 404
        path = '/api/research/handoffs/'+key+'/decision'
        assert request('POST', path, acceptance)[0] == 403
        assert request('POST', path, {**acceptance,'relationship_checked':False},token)[0] == 400
        code, accepted = request('POST', path, acceptance, token)
        assert code == 201
        assert request('POST', path, acceptance, token)[0] == 200
        key = accepted['result']['receipt']['case_id']
        assert request('POST','/api/demo/reset',{'confirm':'RESET'},token)[0] == 409
        _, bootstrap = request('GET','/api/bootstrap')
        case = next(x for x in bootstrap['cases'] if x['id']==key)
        assert len(bootstrap['cases']) == 8 and case['stage']=='Not contacted'
        assert case['workspaceDate'] == date.today().isoformat()
        assert request('POST','/api/cases/'+key+'/outreach',{'revision':case['revision']},token)[0] == 400
        body = {'revision':case['revision'],'contact':'Synthetic Contact','role':'Operations lead','source_rationale':'Confirmed synthetic person for test.'}
        assert request('POST','/api/cases/'+key+'/contact',body,token)[0] == 200
        assert request('POST','/api/cases/'+key+'/contact',body,token)[0] == 409
        assert request('GET','/api/research/handoffs')[1]['result'][0]['receipt']['status']=='accepted'
    finally:
        connection.close()
        server.shutdown()
        server.research_service.shutdown()
        server.server_close()
        stores.close()
