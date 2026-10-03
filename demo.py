"""Run Atlas with entirely fictional data, without Codex or model calls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from atlas.ingest import sync
from atlas.store import Store


PROJECTS = {
    'seed': ('Community Seed Library', 'Build a small offline seed-sharing catalog.'),
    'walk': ('Neighborhood Walking Guide', 'Create an accessible walking guide for a fictional neighborhood.'),
}


def segment(message_ids, project, summary, stage, events, open_items=None, decisions=None):
    name, goal = PROJECTS[project] if project else ('', '')
    return {
        'message_ids': message_ids,
        'project': {'id': '', 'name': name, 'goal': goal, 'parent_id': ''},
        'topic_names': ['Community projects', 'Offline tools'] if project else ['Learning notes'],
        'summary': summary, 'stage': stage,
        'events': [{'kind': kind, 'text': text, 'message_ids': refs} for kind, text, refs in events],
        'open_items': open_items or [], 'decisions': decisions or [],
    }


def seed_demo(root: Path) -> tuple[Store, Path]:
    """Create only synthetic sources and derived data underneath an empty root."""
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError('The demo requires an empty directory; existing data will not be overwritten.')
    history = root / 'fictional-history'
    sessions = history / 'sessions'
    sessions.mkdir(parents=True)
    (history / 'archived_sessions').mkdir()
    store = Store(root / 'data' / 'atlas.sqlite3')
    definitions = [
        ('demo-seed-planning', '2025-01-10', 'Seed library: scope and choices', 'fictional/shared-workspace', [
            ('s1', 'user', 'Let us build a seed library catalog. 种子目录需要离线搜索。'),
            ('s2', 'assistant', 'We could add a public map and a printable inventory.'),
            ('s3', 'user', 'Decided: start with a printable inventory. The public map can wait.'),
        ], [segment(['s1', 's2', 's3'], 'seed', 'Chose a printable offline inventory as the first deliverable.', 'Prototype', [
            ('discussed', 'Discussed a public map and printable inventory.', ['s2']),
            ('decided', 'Prioritized the printable inventory.', ['s3']),
        ], ['Test search with mixed Chinese and English labels.'], ['Start with a printable inventory.'])]),
        ('demo-walking-guide', '2025-01-11', 'Walking guide: draft route', 'fictional/shared-workspace', [
            ('w1', 'user', 'Create a walking guide for the imaginary Willow Quarter. It should work offline.'),
            ('w2', 'assistant', 'The draft route is written. Accessibility has not been checked.'),
            ('w3', 'user', 'The route review is blocked until we have a fictional ramp survey.'),
        ], [segment(['w1', 'w2', 'w3'], 'walk', 'A draft route was reported complete; accessibility review remains blocked.', 'Awaiting review', [
            ('reported_done', 'The assistant reported that the draft route was written.', ['w2']),
            ('blocked', 'Accessibility review needs a ramp survey.', ['w3']),
        ], ['Review the route for accessibility.'])]),
        ('demo-seed-checks', '2025-01-12', 'Seed search: check and revised scope', 'fictional/another-workspace', [
            ('s4', 'assistant', 'The public map prototype is ready for release.'),
            ('s5', 'user', 'Correction: do not release the public map. Withdraw that readiness claim; only the local inventory is in scope.'),
            ('s6', 'tool', 'FICTIONAL TEST OUTPUT: inventory_search: 3 passed. Queries: seed, 种子, 离线. Public map: not tested.'),
            ('s7', 'assistant', 'The attached fictional test record covers local inventory search only. The public map remains deferred.'),
            ('n1', 'user', 'General question: why does an offline-first tool remain useful without a connection?'),
            ('n2', 'assistant', 'It keeps a local copy of essential data. This is a general learning note, not a new project.'),
        ], [segment(['s4', 's5', 's7'], 'seed', 'Withdrew the public-map release claim; the supplied test record covers local inventory search.', 'Search checked', [
            ('reported_done', 'Previously claimed the public map was ready.', ['s4']),
            ('retracted', 'Withdrew the map readiness claim and kept local inventory in scope.', ['s5']),
            ('verified', 'The fictional tool record reports three local-search checks passing; the map was not tested.', ['s6']),
        ], ['Review the printable inventory layout.'], ['Keep the public map deferred.']),
            segment(['n1', 'n2'], None, 'General explanation of offline-first software, without a separate project.', '', [
                ('discussed', 'Discussed why local copies make offline tools useful.', ['n1', 'n2']),
            ])]),
    ]
    results = {}
    metadata = {}
    for thread_id, day, title, cwd, messages, segments in definitions:
        stamp = f'{day}T10:00:00Z'
        records = [{'type': 'session_meta', 'timestamp': stamp, 'payload': {
            'id': thread_id, 'cwd': cwd, 'thread_source': 'user', 'timestamp': stamp,
        }}]
        for index, (mid, role, text) in enumerate(messages):
            payload = ({'type': 'function_call_output', 'id': mid, 'output': text} if role == 'tool' else {
                'type': 'message', 'id': mid, 'role': role, 'content': [{'type': 'input_text', 'text': text}],
            })
            records.append({'type': 'response_item', 'timestamp': f'{day}T10:{index + 1:02d}:00Z', 'payload': payload})
        (sessions / f'{thread_id}.jsonl').write_text(
            ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records), encoding='utf-8')
        for item in segments:
            item['message_ids'] = [f'{thread_id}:{mid}' for mid in item['message_ids']]
            for event in item['events']:
                event['message_ids'] = [f'{thread_id}:{mid}' for mid in event['message_ids']]
        results[thread_id] = {'segments': segments}
        metadata[thread_id] = {'name': title}
    sync(store, history, metadata)
    while job := store.claim_job():
        store.apply_analysis(job['id'], results[job['thread_id']])
    store.save_queue_settings({'paused': True, 'background': False})
    return store, history


def verify_demo(store: Store) -> dict:
    status = store.status()
    assert status['threads'] == 3 and status['analyzed'] == 3
    assert status['pending'] == 0 and status['failed'] == 0
    assert len(store.projects()) == 2 and len(store.topics()) == 3
    for query in ('seed', '种子', '离线', 'offline'):
        assert store.search(query)['total'] > 0, query
    projects = [store.project(project['id']) for project in store.projects()]
    kinds = {event['kind'] for project in projects for event in project['events']}
    assert {'discussed', 'decided', 'reported_done', 'verified', 'retracted', 'blocked'} <= kinds
    assert len(next(project for project in projects if project['name'] == PROJECTS['seed'][0])['threads']) == 2
    return {'demo': True, 'projects': 2, 'topics': 3, 'threads': 3, 'analyzed_batches': 3,
            'search_checks': 4, 'event_kinds': sorted(kinds)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate fictional data and exit without starting a server.')
    parser.add_argument('--port', type=int, default=8788, help='Demo loopback port (default: 8788).')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='codex-atlas-demo-') as temporary:
        root = Path(temporary)
        store, history = seed_demo(root)
        summary = verify_demo(store)
        if args.check:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return
        os.environ.update(ATLAS_DEMO='1', ATLAS_DATA_DIR=str(root / 'data'),
                          ATLAS_CODEX_HOME=str(history), ATLAS_BACKGROUND='0')
        import uvicorn
        from atlas.app import app
        print(f'Fictional demo only: http://127.0.0.1:{args.port}/')
        print('No Codex login or model calls. Press Ctrl+C to stop and remove the temporary demo data.')
        uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
