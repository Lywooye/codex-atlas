"""Install/start or stop this user's launchd service; never changes Codex settings."""
import os
import argparse
from pathlib import Path
import plistlib
import subprocess
import sys
import time
import urllib.request
import json

ROOT = Path(__file__).resolve().parent
LABEL = 'local.codex-project-atlas'
DOMAIN = f'gui/{os.getuid()}'
PLIST = Path.home() / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'


def service_spec():
    env = {'PATH': os.environ['PATH'], 'HOME': str(Path.home()),
           'ATLAS_BACKGROUND': '1', 'ATLAS_MANAGED': '1', 'PYTHONUNBUFFERED': '1'}
    for key in ('ATLAS_CODEX_BIN', 'ATLAS_CODEX_HOME', 'ATLAS_DATA_DIR'):
        if key in os.environ:
            env[key] = os.environ[key]
    data = Path(env.get('ATLAS_DATA_DIR', ROOT / 'data'))
    logs = data / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    return {'Label': LABEL, 'ProgramArguments': [str(ROOT / '.venv/bin/python'), '-m', 'atlas.server'],
            'WorkingDirectory': str(ROOT), 'EnvironmentVariables': env,
            'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10,
            'ProcessType': 'Background', 'ExitTimeOut': 25, 'Umask': 63,
            'StandardOutPath': str(logs / 'launcher.log'), 'StandardErrorPath': str(logs / 'launcher.log')}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Manage the local Codex Atlas macOS service.')
    parser.add_argument('action', choices=('start', 'stop', 'uninstall'), nargs='?', default='start')
    action = parser.parse_args().action
    if sys.platform != 'darwin':
        raise SystemExit('Managed startup requires macOS. Use demo.py or foreground atlas.server instead.')
    if PLIST.exists():
        installed = plistlib.loads(PLIST.read_bytes())
        if installed.get('WorkingDirectory') != str(ROOT):
            raise SystemExit('Another installation owns the Atlas background service. Manage it from that installation first.')
    if action in {'stop', 'uninstall'}:
        loaded = subprocess.run(['launchctl', 'print', f'{DOMAIN}/{LABEL}'], capture_output=True).returncode == 0
        if loaded:
            subprocess.run(['launchctl', 'bootout', f'{DOMAIN}/{LABEL}'], check=True)
            deadline = time.monotonic()+50
            while subprocess.run(['launchctl', 'print', f'{DOMAIN}/{LABEL}'], capture_output=True).returncode == 0:
                if time.monotonic() > deadline:
                    raise SystemExit('Service shutdown is still pending. Wait before restarting; private data is preserved.')
                time.sleep(.5)
        if action == 'uninstall':
            PLIST.unlink(missing_ok=True)
        print('Background service stopped. Private data has been preserved.')
        return
    spec = service_spec()
    loaded = subprocess.run(['launchctl', 'print', f'{DOMAIN}/{LABEL}'], capture_output=True).returncode == 0
    if not loaded:
        # A foreground/legacy instance must be shut down before taking ownership.
        try:
            urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=1).close()
        except OSError:
            pass
        else:
            raise SystemExit('Port 8787 is already in use. Stop that service before running start.command.')
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        PLIST.write_bytes(plistlib.dumps(spec))
        log = Path(spec['StandardErrorPath'])
        if log.exists() and log.stat().st_size > 2*1024*1024:
            log.replace(log.with_suffix('.log.1'))
        subprocess.run(['launchctl', 'bootstrap', DOMAIN, str(PLIST)], check=True)
    for _ in range(60):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=1) as response:
                if json.load(response).get('app') == 'codex-project-atlas':
                    print('Service ready: http://127.0.0.1:8787/ — logs: data/logs/atlas.jsonl')
                    return
        except OSError:
            pass
        time.sleep(.5)
    raise SystemExit('Startup timed out. If macOS asks Python for access to the project folder, grant access and retry. Otherwise inspect data/logs/launcher.log and atlas.jsonl locally.')


if __name__ == '__main__':
    main()
