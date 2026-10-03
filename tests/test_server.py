import json
import logging
from logging.handlers import RotatingFileHandler
from atlas.server import JsonFormatter
from service_agent import service_spec


def test_diagnostics_include_traceback_and_rotate(tmp_path):
    path = tmp_path / 'atlas.jsonl'
    handler = RotatingFileHandler(path, maxBytes=900, backupCount=2, encoding='utf-8')
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger('atlas.test.rotation')
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(20):
            logger.info('job_started job_id=test-%s attempt=1', i)
        try:
            raise ValueError('invalid citation')
        except ValueError:
            logger.exception('job_retry job_id=test-19')
        records = [json.loads(line) for p in tmp_path.glob('atlas.jsonl*') for line in p.read_text().splitlines()]
        assert len(list(tmp_path.iterdir())) == 3
        error = next(r for r in records if r['level'] == 'ERROR')
        assert 'ValueError: invalid citation' in error['traceback']
        assert error['pid'] and error['time'].endswith('+00:00')
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_launchd_spec_is_local_and_keeps_service_alive(tmp_path, monkeypatch):
    monkeypatch.setenv('ATLAS_DATA_DIR', str(tmp_path))
    spec = service_spec()
    assert spec['KeepAlive'] and spec['RunAtLoad']
    assert spec['ThrottleInterval'] >= 10
    assert spec['ProgramArguments'][-1] == 'atlas.server'
    assert spec['EnvironmentVariables']['ATLAS_BACKGROUND'] == '1'
    assert spec['EnvironmentVariables']['ATLAS_MANAGED'] == '1'
    assert spec['Umask'] == 63


def test_service_management_cannot_touch_another_installation(tmp_path, monkeypatch):
    import plistlib
    import service_agent
    import pytest
    agent = tmp_path / 'atlas.plist'
    agent.write_bytes(plistlib.dumps({'WorkingDirectory': str(tmp_path/'other-installation')}))
    monkeypatch.setattr(service_agent, 'PLIST', agent)
    monkeypatch.setattr(service_agent.sys, 'platform', 'darwin')
    monkeypatch.setattr(service_agent.sys, 'argv', ['service_agent.py','uninstall'])
    def forbidden(*args, **kwargs):
        pytest.fail('Another installation must not be stopped')
    monkeypatch.setattr(service_agent.subprocess, 'run', forbidden)
    with pytest.raises(SystemExit, match='Another installation'):
        service_agent.main()
    assert agent.exists()


def test_uninstall_preserves_private_data(tmp_path, monkeypatch):
    import plistlib
    import types
    import service_agent
    agent=tmp_path/'atlas.plist'
    agent.write_bytes(plistlib.dumps({'WorkingDirectory':str(service_agent.ROOT)}))
    data=tmp_path/'private-data'
    data.mkdir()
    sentinel=data/'index.sqlite3'
    sentinel.write_bytes(b'fictional placeholder')
    monkeypatch.setattr(service_agent,'PLIST',agent)
    monkeypatch.setattr(service_agent.sys,'platform','darwin')
    monkeypatch.setattr(service_agent.sys,'argv',['service_agent.py','uninstall'])
    monkeypatch.setattr(service_agent.subprocess,'run',lambda *args,**kwargs:types.SimpleNamespace(returncode=1))
    service_agent.main()
    assert not agent.exists() and sentinel.read_bytes()==b'fictional placeholder'


def test_stop_waits_for_launchd_cleanup(tmp_path,monkeypatch):
    import plistlib
    import types
    import service_agent
    agent=tmp_path/'atlas.plist'
    agent.write_bytes(plistlib.dumps({'WorkingDirectory':str(service_agent.ROOT)}))
    monkeypatch.setattr(service_agent,'PLIST',agent)
    monkeypatch.setattr(service_agent.sys,'platform','darwin')
    monkeypatch.setattr(service_agent.sys,'argv',['service_agent.py','stop'])
    answers=iter([0,0,0,1])
    calls=[]
    def run(args,**kwargs):
        calls.append(args[1]);return types.SimpleNamespace(returncode=next(answers))
    monkeypatch.setattr(service_agent.subprocess,'run',run)
    monkeypatch.setattr(service_agent.time,'sleep',lambda _:None)
    service_agent.main()
    assert calls==['print','bootout','print','print']
    assert agent.exists()
