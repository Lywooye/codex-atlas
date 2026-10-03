"""Security boundaries use generated fixtures only; never read a user's history."""
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from atlas.app import create_app
from atlas.analysis import AnalysisService
from atlas.store import Store


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('ATLAS_DATA_DIR', str(tmp_path/'data'))
    monkeypatch.setenv('ATLAS_CODEX_HOME', str(tmp_path/'fictional-history'))
    monkeypatch.delenv('ATLAS_DEMO', raising=False)
    return tmp_path


def test_exact_origin_rebinding_and_browser_headers(isolated):
    app=create_app(background=False)
    with TestClient(app, base_url='http://127.0.0.1:8787') as client:
        for origin in ('https://example.invalid','http://localhost:8787','http://127.0.0.1:9999','null'):
            for method,path in [('GET','/api/status'),('POST','/api/analysis')]:
                response=client.request(method,path,headers={'origin':origin},json={'action':'resume'} if method=='POST' else None)
                assert response.status_code==403
        assert client.get('/api/status',headers={'host':'attacker.invalid'}).status_code==403
        assert client.get('/api/status',headers={'host':'testserver'}).status_code==403
        assert client.get('/api/status',headers={'sec-fetch-site':'cross-site'}).status_code==403
        response=client.get('/api/status',headers={'origin':'http://127.0.0.1:8787'})
        assert response.status_code==200
        assert response.headers['x-frame-options']=='DENY'
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']
        assert response.headers['cross-origin-resource-policy']=='same-origin'
        assert response.headers['cache-control']=='no-store'
        assert app.state.analyzer.status()['requires_consent']


def test_fresh_background_queue_requires_explicit_consent(isolated, monkeypatch):
    monkeypatch.setenv('ATLAS_BACKGROUND','1')
    store=Store(isolated/'fresh.sqlite3')
    service=AnalysisService(store,isolated,isolated/'fictional-history')
    claims=[]
    monkeypatch.setattr(store,'claim_job',lambda **kwargs: claims.append('claim'))
    service.start()
    try:
        service.set_parallelism(6)
        service.heartbeat('test-browser')
        assert service.status()['paused'] and service.status()['requires_consent']
        time.sleep(.08)
        assert claims==[]
        service.control('resume')
        deadline=time.monotonic()+2
        while not claims and time.monotonic()<deadline: time.sleep(.01)
        assert claims
        assert not service.status()['requires_consent']
        service.control('pause')
    finally:
        service.stop()
    restarted=AnalysisService(store,isolated,isolated/'fictional-history')
    assert restarted.status()['paused'] and not restarted.status()['requires_consent']


def test_demo_cannot_read_history_auth_or_launch_processes(isolated,monkeypatch):
    monkeypatch.setenv('ATLAS_DEMO','1')
    def forbidden(*args,**kwargs):
        pytest.fail('Demo attempted private-history access or process execution')
    monkeypatch.setattr('atlas.app.sync',forbidden)
    monkeypatch.setattr('atlas.app.load_metadata',forbidden)
    monkeypatch.setattr('atlas.analysis.CodexRunner.options',forbidden)
    monkeypatch.setattr('atlas.analysis.CodexRunner.analyze',forbidden)
    monkeypatch.setattr('atlas.app.subprocess.run',forbidden)
    monkeypatch.setattr('atlas.analysis.subprocess.Popen',forbidden)
    app=create_app(background=True)
    with TestClient(app,base_url='http://127.0.0.1:8788') as client:
        assert client.get('/api/status').json()['demo']
        assert client.get('/api/analysis/settings').json()['model']=='fictional-demo'
        for path,body in [('/api/sync',{}),('/api/analysis',{'action':'resume'}),('/api/analysis',{'action':'retry'}),('/api/open-thread/00000000-0000-4000-8000-000000000001',{})]:
            assert client.post(path,json=body).status_code==403
        assert client.patch('/api/analysis/settings',json={'model':'anything','reasoning_effort':'medium','parallelism':6}).status_code==403
        assert client.post('/api/projects/merge',json={'target_id':'fictional-a','source_ids':['fictional-b']}).status_code==403
        assert client.post('/api/heartbeat',json={'client_id':'demo-browser'}).status_code==200
        assert app.state.analyzer._thread is None


def test_new_index_is_private_to_owner(isolated):
    store=Store(isolated/'private-data'/'index.sqlite3')
    if os.name=='posix':
        assert store.db_path.stat().st_mode & 0o777 == 0o600
        assert store.db_path.parent.stat().st_mode & 0o777 == 0o700


def test_cross_origin_cannot_merge_or_change_parallelism(isolated):
    app=create_app(background=False)
    with TestClient(app,base_url='http://127.0.0.1:8787') as client:
        for origin in ('https://example.invalid','http://127.0.0.1:9999','null'):
            headers={'origin':origin}
            assert client.post('/api/projects/merge',headers=headers,json={'target_id':'fictional-a','source_ids':['fictional-b']}).status_code==403
            assert client.patch('/api/analysis/settings',headers=headers,json={'model':'fictional','reasoning_effort':'medium','parallelism':6}).status_code==403
        assert app.state.analyzer.status()['parallelism']==3
        assert app.state.analyzer.status()['requires_consent']
