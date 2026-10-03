"""Tool-local model selection using Codex's local model catalog."""
import json
import os
from pathlib import Path
import tempfile


def model_catalog(codex_home: Path):
    try:
        cache = json.loads((codex_home / 'models_cache.json').read_text(encoding='utf-8'))
        models = []
        for item in cache['models']:
            if item.get('visibility') != 'list':
                continue
            efforts = [level['effort'] for level in item['supported_reasoning_levels']]
            if efforts:
                models.append({'id': item['slug'], 'name': item.get('display_name') or item['slug'],
                               'efforts': efforts, 'default_effort': item['default_reasoning_level']})
        if not models:
            raise ValueError('empty model catalog')
        return {'models': models, 'catalog_updated_at': cache.get('fetched_at')}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError('无法读取本机 Codex 模型列表，请启动或更新 Codex 后重试；当前整理配置未改变。') from exc


def read_settings(runner):
    values = dict(value.split('=', 1) for value in runner.options() if value != '-c')
    return {'model': json.loads(values['model']) if 'model' in values else None,
            'reasoning_effort': json.loads(values['model_reasoning_effort']) if 'model_reasoning_effort' in values else None}


def save_settings(runner, model: str, effort: str):
    catalog = model_catalog(runner.codex_home)
    selected = next((item for item in catalog['models'] if item['id'] == model), None)
    if selected is None:
        raise ValueError('请选择本机 Codex 模型列表中的模型。')
    if effort not in selected['efforts']:
        raise ValueError('该模型不支持所选推理强度。')
    path = runner.settings_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # Readers see either complete version, including when the next job starts during save.
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.analysis-settings-', delete=False, encoding='utf-8') as handle:
        temporary = Path(handle.name)
        try:
            json.dump({'model': model, 'reasoning_effort': effort}, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {'model': model, 'reasoning_effort': effort, **catalog}
