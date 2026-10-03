import json

import pytest
from fastapi.testclient import TestClient

from atlas.analysis import CodexRunner
from atlas.app import create_app
from atlas.model_settings import model_catalog, read_settings, save_settings


@pytest.fixture
def setup(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    home = tmp_path / 'codex'
    home.mkdir()
    (home/'config.toml').write_text('model="model-a"\nmodel_reasoning_effort="high"\n')
    (home/'models_cache.json').write_text(json.dumps({'fetched_at':'2026-10-02T00:00:00Z','models':[
        {'slug':'model-a','display_name':'Model A','visibility':'list','supported_reasoning_levels':[{'effort':'medium'},{'effort':'high'}],'default_reasoning_level':'medium'},
        {'slug':'model-b','display_name':'Model B','visibility':'list','supported_reasoning_levels':[{'effort':'low'}],'default_reasoning_level':'low'},
        {'slug':'hidden','visibility':'hide'}]}))
    monkeypatch.setenv('ATLAS_DATA_DIR', str(data))
    runner = CodexRunner(tmp_path,home)
    return runner, home, data


def test_settings_validate_combinations_and_preserve_global_config(setup):
    runner, home, data = setup
    original = (home/'config.toml').read_bytes()
    assert read_settings(runner) == {'model':'model-a','reasoning_effort':'high'}
    assert [m['id'] for m in model_catalog(home)['models']] == ['model-a','model-b']
    previous_command_options = runner.options()
    save_settings(runner,'model-b','low')
    assert read_settings(runner) == {'model':'model-b','reasoning_effort':'low'}
    assert 'model="model-a"' in previous_command_options  # Already built commands retain their settings.
    assert 'model="model-b"' in runner.options()
    assert (home/'config.toml').read_bytes() == original
    saved = runner.settings_path.read_bytes()
    for model, effort in [('hidden','low'),('invented','low'),('model-b','high')]:
        with pytest.raises(ValueError):
            save_settings(runner,model,effort)
        assert runner.settings_path.read_bytes() == saved
    assert not list(data.glob('.analysis-settings-*'))


def test_missing_catalog_does_not_overwrite_settings(setup):
    runner,home,data = setup
    save_settings(runner,'model-a','medium')
    (home/'models_cache.json').unlink()
    with pytest.raises(RuntimeError,match='模型列表'):
        save_settings(runner,'model-b','low')
    assert read_settings(runner)['model'] == 'model-a'


def test_settings_api_does_not_resume_or_touch_active_job(setup):
    runner,home,data=setup
    app=create_app(data/'test.sqlite3',home,background=False, allow_test_host=True)
    service=app.state.analyzer
    service._paused=True
    service._active=True
    service._job_id='existing-job'
    with TestClient(app) as client:
        assert client.get('/api/analysis/settings').json()['model']=='model-a'
        response=client.patch('/api/analysis/settings',json={'model':'model-b','reasoning_effort':'low'})
        assert response.status_code==200
        assert response.json()['model']=='model-b'
        assert service._paused and service._active and service._job_id=='existing-job'
        assert client.patch('/api/analysis/settings',json={'model':'model-b','reasoning_effort':'ultra'}).status_code==400
        assert client.patch('/api/analysis/settings',json={'model':'model-a','reasoning_effort':'medium'},headers={'origin':'https://example.com'}).status_code==403
        (home/'models_cache.json').write_text('invalid json')
        assert client.get('/api/analysis/settings').status_code==503
        assert client.patch('/api/analysis/settings',json={'model':'model-a','reasoning_effort':'medium'}).status_code==503
