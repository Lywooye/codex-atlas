"""Exercise demo routes with real-history, subprocess, and network guards."""
from __future__ import annotations

from contextlib import ExitStack
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo import seed_demo, verify_demo


def forbidden(*args, **kwargs):
    raise AssertionError('The fictional demo attempted an external operation.')


def main():
    os.umask(0o077)
    real_history = (Path.home() / '.codex').resolve()
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path.resolve().is_relative_to(real_history):
            raise AssertionError('The fictional demo attempted to open real Codex files.')
        return original_open(path, *args, **kwargs)

    with tempfile.TemporaryDirectory(prefix='codex-atlas-demo-check-') as temporary, ExitStack() as guards:
        root = Path(temporary)
        history = root / 'fictional-history'
        guards.enter_context(patch.dict(os.environ, {
            'ATLAS_DEMO': '1', 'ATLAS_DATA_DIR': str(root / 'data'),
            'ATLAS_CODEX_HOME': str(history), 'ATLAS_BACKGROUND': '1',
        }))
        guards.enter_context(patch.object(Path, 'open', guarded_open))
        guards.enter_context(patch('subprocess.Popen', side_effect=forbidden))
        guards.enter_context(patch('subprocess.run', side_effect=forbidden))
        guards.enter_context(patch('socket.socket.connect', side_effect=forbidden))
        guards.enter_context(patch('atlas.analysis._model_options', side_effect=forbidden))
        guards.enter_context(patch('atlas.analysis.CodexRunner.analyze', side_effect=forbidden))
        store, history = seed_demo(root)
        summary = verify_demo(store)
        module = importlib.import_module('atlas.app')
        guards.enter_context(patch.object(module, 'load_metadata', side_effect=forbidden))
        from fastapi.testclient import TestClient
        app = module.create_app(background=True, allow_test_host=True)
        with TestClient(app) as client:
            status = client.get('/api/status').json()
            assert status['demo'] is True
            assert status['analysis']['paused'] is True
            assert status['analysis']['active'] is False
            assert status['analyzed'] == 3
            assert client.get('/api/analysis/settings').status_code == 200
            assert len(client.get('/api/projects').json()) == 2
            assert client.get('/api/search', params={'q': '种子'}).json()['total'] > 0
            source = client.get('/api/threads/demo-seed-planning').json()
            assert source['messages']
            writes = [
                ('post', '/api/sync', {}),
                ('post', '/api/analysis', {'action': 'resume'}),
                ('post', '/api/analysis', {'action': 'retry'}),
                ('patch', '/api/analysis/settings', {'model': 'fictional-model', 'reasoning_effort': 'medium'}),
                ('post', '/api/open-thread/demo-seed-planning', {}),
            ]
            for method, url, body in writes:
                assert getattr(client, method)(url, json=body).status_code == 403, url
            assert client.post('/api/heartbeat', json={'client_id': 'fictional-browser'}).status_code == 200
        summary.update(api_checks=True, external_operations='blocked', private_codex_reads='blocked')
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
