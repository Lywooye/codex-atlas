"""HTTP behavior, isolation and correction endpoints with fixture histories."""
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atlas.app import create_app
from atlas.ingest import sync


@pytest.fixture
def client(tmp_path):
    source = tmp_path / 'codex' / 'sessions'
    source.mkdir(parents=True)
    thread_id = str(uuid.uuid4())
    rows = [
        {'type': 'session_meta', 'payload': {'id': thread_id, 'cwd': '/vault/building',
          'thread_source': 'user', 'timestamp': '2026-09-25T10:00:00Z', 'source': 'vscode'}},
        {'timestamp': '2026-09-25T10:01:00Z', 'type': 'response_item', 'payload': {
          'type': 'message', 'id': 'u1', 'role': 'user',
          'content': [{'type': 'input_text', 'text': '请完成超声报告质控的标注手册。'}]}},
        {'timestamp': '2026-09-25T10:02:00Z', 'type': 'response_item', 'payload': {
          'type': 'message', 'id': 'a1', 'role': 'assistant',
          'content': [{'type': 'output_text', 'text': '标注手册已起草，仍需专家审核。'}]}}
    ]
    (source / f'rollout-{thread_id}.jsonl').write_text('\n'.join(json.dumps(x) for x in rows)+'\n')
    app = create_app(tmp_path / 'test.sqlite3', tmp_path / 'codex', background=False, allow_test_host=True)
    sync(app.state.store, tmp_path / 'codex')
    with TestClient(app) as c:
        c.thread_id = thread_id
        c.store = app.state.store
        yield c


def analyzed(client):
    job = client.store.claim_job()
    ids = [m['id'] for m in job['messages']]
    client.store.apply_analysis(job['id'], {'segments': [{
        'message_ids': ids, 'topic_names': ['超声质控'],
        'project': {'id': '', 'name': '质控标注手册', 'goal': '完成可供专家审核的标注手册'},
        'summary': '手册已起草，等待专家审核', 'stage': '待审核',
        'events': [{'kind': 'reported_done', 'text': '报告已起草手册', 'message_ids': ids[-1:]}],
        'open_items': ['专家审核'], 'decisions': []}]})
    return client.get('/api/projects').json()[0]


def test_search_source_and_coverage(client):
    assert client.get('/api/health').json()['app'] == 'codex-project-atlas'
    result = client.get('/api/search', params={'q': '质控'}).json()
    assert result['total'] >= 1
    hit = next(x for x in result['items'] if x['kind'] == 'message')
    source = client.get(f'/api/threads/{hit["thread_id"]}', params={'focus': hit['message_id']}).json()
    assert any(m['id'] == hit['message_id'] for m in source['messages'])
    assert client.get('/api/status').json()['pending'] >= 1


def test_manual_edit_assignment_and_merge_validation(client):
    project = analyzed(client)
    detail = client.get('/api/projects/'+project['id']).json()
    assert detail['segments'] and detail['events']
    assert client.patch('/api/projects/'+project['id'], json={'name': '我的质控手册'}).status_code == 200
    segment = detail['segments'][0]
    response = client.patch('/api/segments/'+segment['id'], json={'project_id': None, 'topic_ids': segment['topic_ids']})
    assert response.status_code == 200, response.text
    assert response.json()['project_id'] is None
    assert client.post('/api/projects/merge', json={'source_ids': ['missing'], 'target_id': project['id']}).status_code in {400, 404}


def test_local_origin_controls_and_error_states(client):
    assert client.post('/api/analysis', json={'action': 'pause'}, headers={'origin': 'https://example.com'}).status_code == 403
    assert client.post('/api/analysis', json={'action': 'pause'}).status_code == 200
    assert client.post('/api/analysis', json={'action': 'something'}).status_code == 400
    assert client.get('/api/projects/missing').status_code == 404
    assert client.post('/api/open-thread/not-a-uuid').status_code == 400
    assert client.get('/api/not-real').status_code == 404


def test_open_thread_dispatch_does_not_claim_navigation(client, monkeypatch):
    calls = []
    class Result:
        returncode = 0
        stderr = ''
    def run(command, **kwargs):
        calls.append(command)
        return Result()
    monkeypatch.setattr('atlas.app.subprocess.run', run)
    response = client.post('/api/open-thread/'+client.thread_id)
    assert response.status_code == 200
    assert response.json()['dispatched'] is True
    assert calls == [['/usr/bin/open', 'codex://threads/'+client.thread_id]]


def test_project_group_routes_preserve_sources_and_allow_ungrouping(client):
    child = analyzed(client)
    response = client.post('/api/projects/group', json={
        'name': '报告质量研究', 'goal': '持续建立报告质控研究与评测体系',
        'project_ids': [child['id']]})
    assert response.status_code == 200, response.text
    parent = response.json()
    assert [p['id'] for p in parent['children']] == [child['id']]
    assert parent['segments'] and parent['events'] and parent['coverage']['analyzed'] == 1
    assert client.get('/api/projects/'+child['id']).json()['parent_id'] == parent['id']
    hits = client.get('/api/search', params={'q': '质控', 'project_id': parent['id']}).json()
    assert any(item['kind'] == 'message' for item in hits['items'])
    assert client.patch('/api/projects/'+parent['id']+'/parent', json={'parent_id': child['id']}).status_code == 400
    response = client.patch('/api/projects/'+child['id']+'/parent', json={'parent_id': None})
    assert response.status_code == 200 and response.json()['parent_id'] is None
    assert client.patch('/api/projects/'+child['id']+'/parent', json={'parent_id': parent['id']}).status_code == 200
    assert client.post('/api/projects/group', json={'name': '空组', 'goal': '目标', 'project_ids': []}).status_code == 422
    assert client.patch('/api/projects/'+child['id']+'/parent', json={'parent_id': 'missing'}).status_code in {400, 404}
