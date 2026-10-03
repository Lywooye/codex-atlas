"""Managed server entry point: one instance, rotating diagnostics, clean-exit marker."""
import faulthandler
from datetime import datetime, timezone
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import time

import uvicorn

ROOT = Path(__file__).resolve().parent.parent


class JsonFormatter(logging.Formatter):
    def format(self, record):
        item = {'time': datetime.fromtimestamp(record.created, timezone.utc).isoformat(), 'level': record.levelname,
                'pid': record.process, 'logger': record.name, 'message': record.getMessage()}
        if record.exc_info:
            item['traceback'] = self.formatException(record.exc_info)
        return json.dumps(item, ensure_ascii=False)


def configure_logging(data):
    logs = data / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / 'atlas.jsonl', maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    return logs


def main():
    os.umask(0o077)
    data = Path(os.environ.get('ATLAS_DATA_DIR', ROOT / 'data'))
    data.mkdir(parents=True, exist_ok=True)
    with (data / 'server.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Atlas service already running')
        logs = configure_logging(data)
        logger = logging.getLogger(__name__)
        marker = data / 'server-state.json'
        previous = json.loads(marker.read_text()) if marker.exists() else {}
        logger.info('server_start previous=%s background=%s', previous, os.environ.get('ATLAS_BACKGROUND'))
        state = {'pid': os.getpid(), 'started_at': time.time(), 'clean_exit': False}
        marker.write_text(json.dumps(state))
        (data / 'server.pid').write_text(str(os.getpid()))
        fatal_path = logs / 'fatal.log'
        if fatal_path.exists() and fatal_path.stat().st_size > 2*1024*1024:
            fatal_path.replace(logs / 'fatal.log.1')
        with fatal_path.open('a') as fatal:
            faulthandler.enable(file=fatal)
            try:
                uvicorn.run('atlas.app:app', host='127.0.0.1', port=8787,
                            log_config=None, access_log=False, timeout_graceful_shutdown=15)
            finally:
                state.update(clean_exit=True, stopped_at=time.time())
                marker.write_text(json.dumps(state))
                logger.info('server_exit')
                faulthandler.disable()


if __name__ == '__main__':
    main()
