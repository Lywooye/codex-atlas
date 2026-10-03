"""Loopback-only HTTP interface for the conversation atlas."""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .store import Store
from .ingest import sync
from .analysis import AnalysisService
from .metadata import load_metadata
from .model_settings import model_catalog, read_settings, save_settings

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent


class Heartbeat(BaseModel):
    client_id: str = Field(min_length=1, max_length=100)


class Action(BaseModel):
    action: str


class ModelSettings(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    reasoning_effort: str = Field(min_length=1, max_length=20)
    parallelism: int | None = Field(default=None, ge=1, le=6, strict=True)


class Merge(BaseModel):
    source_ids: list[str] = Field(min_length=1)
    target_id: str


class Split(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    segment_ids: list[str] = Field(min_length=1)


class Assignment(BaseModel):
    project_id: str | None = None
    topic_ids: list[str] = []


class ProjectGroup(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=10000)
    project_ids: list[str] = Field(min_length=1, max_length=100)


class ProjectParent(BaseModel):
    parent_id: str | None


def create_app(db_path: Path | None = None, codex_home: Path | None = None,
               background: bool = True, allow_test_host: bool = False) -> FastAPI:
    demo = os.environ.get('ATLAS_DEMO') == '1'
    if demo:
        background = False
    data = Path(os.environ.get('ATLAS_DATA_DIR', str(ROOT / 'data')))
    data.mkdir(parents=True, exist_ok=True)
    store = Store(db_path or data / 'atlas.sqlite3')
    history_root = codex_home or Path(os.environ.get('ATLAS_CODEX_HOME', str(Path.home() / '.codex')))
    analyzer = AnalysisService(store, ROOT, history_root)
    sync_state = {'running': False, 'error': None, 'metadata_error': None, 'last_finished': None}
    sync_lock = asyncio.Lock()
    clients: dict[str, float] = {}
    metadata: dict = {}
    metadata_at = 0.0

    async def synchronize():
        nonlocal metadata, metadata_at
        if demo or sync_lock.locked():
            return
        async with sync_lock:
            sync_state['running'] = True
            try:
                # Local indexing remains usable even if the app-server is unavailable.
                await asyncio.to_thread(sync, store, history_root, metadata)
                if time.monotonic() - metadata_at > 300:
                    found, error = await asyncio.to_thread(load_metadata, history_root)
                    metadata_at = time.monotonic()
                    if error:
                        logger.warning('metadata_sync_failed error=%s', error)
                    sync_state['metadata_error'] = error
                    if found:
                        metadata = found
                        await asyncio.to_thread(sync, store, history_root, metadata)
                sync_state['error'] = None
                sync_state['last_finished'] = time.time()
            except Exception as exc:
                logger.exception('history_sync_failed')
                sync_state['error'] = str(exc)[:1500]
            finally:
                sync_state['running'] = False

    async def poll():
        while True:
            try:
                analyzer.start()  # Restarts an unexpectedly dead worker; no-op while alive.
                if analyzer.status()['background'] or any(time.monotonic() - t < 75 for t in clients.values()) or not sync_state['last_finished']:
                    await synchronize()
            except Exception:
                logger.exception('background_poll_failed; retrying in 30 seconds')
            await asyncio.sleep(30)

    @asynccontextmanager
    async def lifespan(app):
        task = None
        if background:
            analyzer.start()
            task = asyncio.create_task(poll())
        logger.info("application_started background=%s", background)
        yield
        logger.info("application_stopping")
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        analyzer.stop()

    app = FastAPI(title='Codex 项目总览', lifespan=lifespan)
    app.state.store = store
    app.state.analyzer = analyzer
    app.state.synchronize = synchronize

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        host = request.url.hostname
        allowed_hosts = {'127.0.0.1', 'localhost', '::1'}
        if allow_test_host:
            allowed_hosts.add('testserver')
        if host not in allowed_hosts:
            return JSONResponse({'detail': 'Local access only.'}, status_code=403)
        origin = request.headers.get('origin')
        if origin:
            parsed = urlparse(origin)
            expected = urlparse(str(request.base_url))
            if ((parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc)
                    or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
                return JSONResponse({'detail': 'Cross-origin requests are not allowed.'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': 'Cross-site requests are not allowed.'}, status_code=403)
        if demo and request.method not in {'GET', 'HEAD', 'OPTIONS'} and request.url.path != '/api/heartbeat':
            return JSONResponse({'detail': 'The fictional demo is read-only. AI and Codex navigation are disabled.'}, status_code=403)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception('http_error method=%s path=%s', request.method, request.url.path)
            raise
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        elif not request.url.path.startswith('/assets/'):
            response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({'detail': '未找到对应内容，可能已合并或来源不可用'}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.get('/api/health')
    def health():
        return {'app': 'codex-project-atlas', 'version': '0.1.0'}

    @app.get('/api/status')
    def status():
        analysis_state = ({'paused': True, 'active': False, 'clients': 0, 'model': 'fictional-demo', 'effort': None, 'background': False, 'worker_alive': False, 'requires_consent': False} if demo else analyzer.status())
        return {**store.status(), 'analysis': analysis_state, 'sync': dict(sync_state), 'demo': demo}

    @app.post('/api/heartbeat')
    def heartbeat(body: Heartbeat):
        now = time.monotonic()
        for key in list(clients):
            if now - clients[key] > 75:
                clients.pop(key, None)
        clients[body.client_id] = now
        analyzer.heartbeat(body.client_id)
        return {'ok': True}

    @app.post('/api/sync')
    async def request_sync():
        if not sync_state['running']:
            asyncio.create_task(synchronize())
        return {'accepted': True, 'sync': dict(sync_state)}

    @app.post('/api/analysis')
    def analysis(body: Action):
        if body.action not in {'pause', 'resume', 'retry'}:
            raise HTTPException(400, '未知操作')
        analyzer.control(body.action)
        return analyzer.status()

    @app.get('/api/analysis/settings')
    def analysis_settings():
        if demo:
            return {'model': 'fictional-demo', 'reasoning_effort': None, 'models': [], 'catalog_updated_at': None, 'parallelism': 3}
        try:
            return {**read_settings(analyzer.runner), **model_catalog(history_root),
                    "parallelism": analyzer.status()["parallelism"]}
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))

    @app.patch('/api/analysis/settings')
    def update_analysis_settings(body: ModelSettings):
        try:
            with analyzer._lock:
                saved = save_settings(analyzer.runner, body.model, body.reasoning_effort)
                if body.parallelism is not None:
                    analyzer.set_parallelism(body.parallelism)
                saved["parallelism"] = analyzer.status()["parallelism"]
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        logger.info('analysis_settings_saved model=%s effort=%s effective=next_job', body.model, body.reasoning_effort)
        return saved

    @app.get('/api/projects')
    def projects():
        return store.projects()

    @app.get('/api/projects/{entity_id}')
    def project(entity_id: str):
        return store.project(entity_id)

    @app.post('/api/projects/group')
    def group_projects(body: ProjectGroup):
        return store.group_projects(body.name, body.goal, body.project_ids)

    @app.patch('/api/projects/{entity_id}/parent')
    def project_parent(entity_id: str, body: ProjectParent):
        return store.set_project_parent(entity_id, body.parent_id)

    @app.get('/api/topics')
    def topics():
        return store.topics()

    @app.get('/api/topics/{entity_id}')
    def topic(entity_id: str):
        return store.topic(entity_id)

    @app.get('/api/threads')
    def threads(q: str = '', limit: int = 50, offset: int = 0):
        return store.threads(q=q, limit=min(max(limit, 1), 200), offset=max(offset, 0))

    @app.get('/api/threads/{thread_id}')
    def thread(thread_id: str, limit: int = 100, offset: int = 0, focus: str | None = None):
        return store.thread(thread_id, limit=min(max(limit, 1), 200), offset=max(offset, 0), focus=focus)

    @app.get('/api/search')
    def search(q: str, topic_id: str | None = None, project_id: str | None = None,
               since: str | None = None, until: str | None = None,
               limit: int = 50, offset: int = 0):
        if len(q) > 500:
            raise HTTPException(400, '搜索词请控制在 500 字以内')
        return store.search(q, topic_id=topic_id, project_id=project_id, since=since, until=until,
                            limit=min(max(limit, 1), 200), offset=max(offset, 0))

    @app.patch('/api/segments/{segment_id}')
    def assign(segment_id: str, body: Assignment):
        return store.edit_segment(segment_id, body.project_id, body.topic_ids)

    @app.patch('/api/{kind}/{entity_id}')
    async def edit(kind: str, entity_id: str, request: Request):
        singular = {'projects': 'project', 'topics': 'topic'}.get(kind)
        if not singular:
            raise HTTPException(404, '未知内容类型')
        changes = await request.json()
        allowed = {'name', 'goal', 'summary', 'stage'} if singular == 'project' else {'name'}
        if not isinstance(changes, dict) or not changes or set(changes) - allowed:
            raise HTTPException(400, '不支持的修改字段')
        for key, value in changes.items():
            if not isinstance(value, str) or len(value) > 10000 or (key == 'name' and not value.strip()):
                raise HTTPException(400, '请输入有效的文本')
        return store.edit_entity(singular, entity_id, changes)

    @app.post('/api/{kind}/merge')
    def merge(kind: str, body: Merge):
        singular = {'projects': 'project', 'topics': 'topic'}.get(kind)
        if not singular:
            raise HTTPException(404, '未知内容类型')
        return store.merge_entities(singular, body.source_ids, body.target_id)

    @app.post('/api/{kind}/{entity_id}/split')
    def split(kind: str, entity_id: str, body: Split):
        singular = {'projects': 'project', 'topics': 'topic'}.get(kind)
        if not singular:
            raise HTTPException(404, '未知内容类型')
        return store.split_entity(singular, entity_id, body.name, body.segment_ids)

    @app.post('/api/open-thread/{thread_id}')
    def open_thread(thread_id: str):
        try:
            uuid.UUID(thread_id)
        except ValueError:
            raise HTTPException(400, '无效的 Codex 对话 ID')
        info = store.thread(thread_id, limit=1)
        if not info.get('available'):
            raise HTTPException(404, '本机已找不到源对话，仍可查看保留的索引原文')
        url = f'codex://threads/{thread_id}'
        try:
            result = subprocess.run(['/usr/bin/open', url], capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(503, f'无法打开 Codex：{exc}')
        if result.returncode:
            raise HTTPException(503, f'Codex 未接收打开请求：{result.stderr.strip()}')
        return {'dispatched': True, 'url': url, 'note': '已交给 Codex 打开；此响应不证明页面已定位'}

    dist = ROOT / 'web' / 'dist'
    if (dist / 'assets').exists():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

    @app.get('/{path:path}')
    def frontend(path: str):
        if path.startswith('api/'):
            raise HTTPException(404, '未知接口')
        if not (dist / 'index.html').exists():
            return JSONResponse({'detail': '前端尚未构建，请运行启动脚本'}, status_code=503)
        return FileResponse(dist / 'index.html')

    return app


app = create_app()
