from dataclasses import replace
from http.client import HTTPConnection
import json
import threading

import pytest

from backend.workspace_config import WorkspaceConfig
from backend.workspace_runtime import WorkspaceStores, make_workspace_server


@pytest.mark.loopback
def test_opportunity_review_routes_and_static_ui(environment, opportunity_body, review_body):
    root, _, _, _ = environment
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
        content = response.read()
        return response.status, json.loads(content) if 'application/json' in response.getheader('Content-Type','') else content.decode()
    try:
        _, before = request('GET','/api/bootstrap')
        token = before['csrf']
        assert request('GET','/')[1].count('research.js') == 1
        assert 'window.CGSResearch' in request('GET','/research.js')[1]
        assert request('GET','/api/research/opportunities')[1]['result'] == []
        assert request('POST','/api/research/opportunities',opportunity_body)[0] == 403
        code, created = request('POST','/api/research/opportunities',opportunity_body,token)
        assert code == 201
        key = created['result']['opportunity']['opportunity_id']
        assert request('POST','/api/research/opportunities',opportunity_body,token)[0] == 200
        assert request('GET','/api/research/opportunities/'+key)[1] == created
        path = '/api/research/opportunities/'+key+'/feedback'
        assert request('POST',path,{**review_body,'reason_code':'other','comment':''},token)[0] == 400
        assert request('POST',path,review_body,token)[0] == 201
        assert request('POST',path,review_body,token)[0] == 200
        assert request('POST',path,{**review_body,'request_id':'stale'},token)[0] == 409
        assert len(request('GET','/api/research/opportunities')[1]['result'][0]['feedback_history']) == 1
        assert request('GET','/api/research/opportunities/missing')[0] == 404
        assert request('POST','/api/research/opportunities/'+key+'/handoff',{},token)[0] == 400  # P3 validates its own input.
        assert request('GET','/api/bootstrap')[1]['cases'] == before['cases']
    finally:
        connection.close()
        server.shutdown()
        server.research_service.shutdown()
        server.server_close()
        stores.close()
