import json
from pathlib import Path

from atlas.store import Store
from atlas.ingest import sync


def record(kind,payload,timestamp='2026-10-01T10:00:00Z'):
    return {'type':kind,'payload':payload,'timestamp':timestamp}


def message(role,text,id=None):
    payload={'type':'message','role':role,'content':[{'type':'input_text','text':text}]}
    if id:payload['id']=id
    return record('response_item',payload)


def write_log(home,name,items,**meta):
    p=home/'sessions'/f'{name}.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
    rows=[record('session_meta',{'id':name,'cwd':'/Users/example/building','thread_source':'user','timestamp':'2026-10-01T10:00:00Z',**meta})]+items
    p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    return p


def test_parse_incremental_partial_rebuild_missing(tmp_path):
    home=tmp_path/'codex';store=Store(tmp_path/'index.db')
    p=write_log(home,'a',[message('developer','PRIVATE SYSTEM'),message('user','<environment_context>machine config</environment_context>\n超声质控方案','u1'),message('assistant','开始试标','a1')])
    first=sync(store,home)
    assert first['messages']==2 and first['chunks']==1
    assert not store.search('PRIVATE')['items']
    assert store.search('质控')['total']>=1
    with p.open('ab') as f:f.write(b'{"type":"response_item",')
    assert sync(store,home)['messages']==2
    with p.open('rb+') as f:
        f.seek(-len(b'{"type":"response_item",'),2);f.truncate()
        f.write((json.dumps(message('assistant','验证已通过','a2'),ensure_ascii=False)+'\n').encode())
    assert sync(store,home)['messages']==3
    assert sync(store,home)['files_read']==0
    # Rewrite with same IDs but different content; index must shed deleted text.
    write_log(home,'a',[message('user','课程开发需求','u1')])
    assert sync(store,home)['messages']==1
    assert store.search('质控')['total']==0
    p.unlink();assert sync(store,home)['missing_sources']==1
    assert store.search('课程')['total']>=1
    assert store.thread('a')['available']==0


def test_technical_forks_legacy_and_long_content(tmp_path):
    home=tmp_path/'codex';store=Store(tmp_path/'index.db')
    write_log(home,'original',[record('event_msg',{'type':'user_message','message':'你好研究'}),message('user','你好研究','same'),message('assistant','字'*45000,'long')],timestamp='2026-09-01T00:00:00Z')
    write_log(home,'fork',[message('user','你好研究','same'),message('user','新的目标','fork-new')],timestamp='2026-10-02T00:00:00Z',forked_from_id='original')
    write_log(home,'child',[message('user','技术检查','tech')],thread_source='subagent',parent_thread_id='original')
    result=sync(store,home)
    assert result['threads']==2 and result['technical_threads']==1
    assert store.thread('original')['total']==2
    assert store.thread('fork')['total']==2
    assert store.thread('original')['related_threads']
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE thread_id='child'").fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM job_messages jm JOIN jobs j ON j.id=jm.job_id WHERE j.thread_id='fork'").fetchone()[0]==1
    pieces=[]
    while job:=store.claim_job():
        pieces.extend(m['text'] for m in job['messages'] if m['text'].startswith('字'))
        assert len(job['messages'])<=20 and sum(len(m['text']) for m in job['messages'])<=20000
    assert ''.join(pieces)=='字'*45000
    assert store.search('技术检查')['total']==0


def test_malformed_complete_lines_and_archived_move(tmp_path):
    home=tmp_path/'codex';store=Store(tmp_path/'index.db')
    p=write_log(home,'thread',[message('user','可以搜索的文字','m')])
    with p.open('a') as f:f.write('broken json\n')
    assert sync(store,home)['parsing_errors']==1
    archive=home/'archived_sessions';archive.mkdir();p.rename(archive/p.name)
    result=sync(store,home,{'thread':{'name':'官方标题'}})
    assert result['messages']==1 and result['chunks']==1
    assert store.thread('thread')['archived']==1
    assert store.thread('thread')['title']=='官方标题'
    assert store.thread('thread')['title_source']=='official'


def test_same_length_middle_rewrite_is_detected(tmp_path):
    home=tmp_path/'codex';store=Store(tmp_path/'index.db')
    p=write_log(home,'a',[message('user','x'*5000+'needleAAA'+'y'*5000,'m')])
    sync(store,home)
    original=p.read_text();p.write_text(original.replace('needleAAA','needleBBB'))
    sync(store,home)
    assert store.search('needleAAA')['total']==0
    assert store.search('needleBBB')['total']>0


def test_missing_source_directory_is_explained(tmp_path):
    store=Store(tmp_path/'index.db')
    status=sync(store,tmp_path/'missing')
    assert status['source_errors'] and 'sessions' in status['source_errors'][0]['error']


def test_injected_plugins_excluded_but_discussion_retained_and_version_reparsed(tmp_path):
    home=tmp_path/'codex';store=Store(tmp_path/'index.db')
    write_log(home,'a',[message('user','<recommended_plugins>Slack plugin metadata</recommended_plugins>','injected'),message('user','请分析 <recommended_plugins>Slack</recommended_plugins> 这个标签有什么作用','real')])
    sync(store,home)
    assert store.thread('a')['total']==1
    assert store.thread('a')['messages'][0]['id']=='a:real'
    assert store.claim_job()['messages'][0]['id']=='a:real'
    with store.connection() as db:
        db.execute("UPDATE settings SET value='human-v1' WHERE key='parser_format'")
    assert sync(store,home)['files_read']==1
    assert sync(store,home)['files_read']==0
    assert store.thread('a')['total']==1


def test_combined_plugin_and_agents_injection_preserves_real_questions(tmp_path):
    from atlas.ingest import _human_text
    combined='<recommended_plugins>Plugin list</recommended_plugins>\n# AGENTS.md instructions\n<INSTRUCTIONS>Repository rules</INSTRUCTIONS>\n<environment_context>Machine paths</environment_context>'
    assert _human_text(combined)==''
    assert _human_text(combined+'\n请解释上面的配置，有什么问题？').endswith('请解释上面的配置，有什么问题？')
    quoted='请解释这段引用：\n'+combined+'\n我应该怎样修改？'
    assert 'Repository rules' in _human_text(quoted)
    assert '我应该怎样修改？' in _human_text(quoted)
    home=tmp_path/'codex';store=Store(tmp_path/'index.db')
    write_log(home,'a',[message('user',combined,'injected'),message('user','真正的项目目标','real')])
    sync(store,home)
    assert store.thread('a')['total']==1
    assert store.thread('a')['messages'][0]['id']=='a:real'


def test_context_skips_old_indexed_combined_injection_without_reimport(tmp_path):
    home=tmp_path/'codex';store=Store(tmp_path/'index.db')
    write_log(home,'a',[message('user',f'真正的问题{i}',f'u{i}') for i in range(21)])
    sync(store,home)
    combined='<recommended_plugins>Plugin list</recommended_plugins>\n# AGENTS.md instructions\n<INSTRUCTIONS>Repository rules</INSTRUCTIONS>'
    with store.connection() as db:
        # Model a legacy parsed row without touching the source file or jobs.
        db.execute('UPDATE messages SET text=? WHERE id=?',(combined,'a:u0'))
    job=store.claim_job()
    assert job['messages'][0]['id']=='a:u20'
    assert job['context'][0]['id']=='a:u1'
    assert all('Repository rules' not in c['text'] for c in job['context'])


def test_path_agents_header_and_ambient_browser_with_real_request():
    from atlas.ingest import _human_text
    injected='<recommended_plugins>\nAvailable plugins\n</recommended_plugins>\n# AGENTS.md instructions for /Users/example/Documents/building\n\n<INSTRUCTIONS>\nRepository rules\n</INSTRUCTIONS>'
    assert _human_text(injected)==''
    assert _human_text(injected.replace('\n','\\n'))==''
    assert _human_text(injected+'\n请解释上面的配置').endswith('请解释上面的配置')
    ambient='<in-app-browser-context source="ambient-ui-state">\nThis block is automatically supplied ambient UI state.\n# In app browser:\n- Current URL: https://example.com/\n</in-app-browser-context>\n\n## My request:\n同意，继续提交'
    assert _human_text(ambient)=='同意，继续提交'
    assert _human_text(ambient.split('## My request:')[0])==''
    quoted='请解释这段配置：\n'+injected+'\n我应该修改哪里？'
    assert 'Repository rules' in _human_text(quoted)
    assert _human_text(quoted).endswith('我应该修改哪里？')


def test_append_keeps_completed_tail_analysis_and_queues_only_new_dialogue(tmp_path):
    from test_store import result
    home=tmp_path/'codex';store=Store(tmp_path/'index.db')
    p=write_log(home,'a',[message('user','最初项目目标','u1'),message('assistant','第一阶段完成','a1')])
    sync(store,home);job=store.claim_job();store.apply_analysis(job['id'],result(job))
    before=store.project(store.projects()[0]['id'])
    before_segment_ids=[s['id'] for s in before['segments']]
    before_event_ids=[e['id'] for e in before['events']]
    with p.open('a') as f:
        f.write(json.dumps(message('user','请推进下一阶段','u2'),ensure_ascii=False)+'\n')
        f.write(json.dumps(message('assistant','正在准备下一阶段','a2'),ensure_ascii=False)+'\n')
    after_sync=sync(store,home)
    assert after_sync['analyzed']==1 and after_sync['pending']==1
    after=store.project(before['id'])
    assert [s['id'] for s in after['segments']]==before_segment_ids
    assert [e['id'] for e in after['events']]==before_event_ids
    assert after['summary']==before['summary']
    with store.connection() as db:
        assert db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='done'
        refs=[r[0] for r in db.execute("SELECT jm.message_id FROM job_messages jm JOIN jobs j ON j.id=jm.job_id WHERE j.status='pending' ORDER BY jm.position")]
    assert refs==['a:u2','a:a2']
    # Repeated appends can grow a pending tail without changing completed history.
    with p.open('a') as f:f.write(json.dumps(message('user','补充条件','u3'),ensure_ascii=False)+'\n')
    assert sync(store,home)['analyzed']==1
    assert [e['id'] for e in store.project(before['id'])['events']]==before_event_ids
    # A real source rewrite still invalidates the former conclusions.
    write_log(home,'a',[message('user','完全替换了原始目标','u1')])
    assert sync(store,home)['analyzed']==0
    assert store.project(before['id'])['events']==[]


def test_append_preserves_running_tail_and_partial_long_message_coverage(tmp_path):
    from test_store import result
    home=tmp_path/'codex';store=Store(tmp_path/'index.db')
    p=write_log(home,'a',[message('user','长'*45000,'u1')])
    sync(store,home);running=store.claim_job()
    assert len(running['messages'])==1
    original_ref=running['messages'][0]['id']
    with p.open('a') as f:f.write(json.dumps(message('assistant','补充回应','a1'),ensure_ascii=False)+'\n')
    sync(store,home)
    with store.connection() as db:
        assert db.execute('SELECT status FROM jobs WHERE id=?',(running['id'],)).fetchone()[0]=='running'
        assert db.execute('SELECT count(*) FROM job_messages WHERE id=?',(original_ref,)).fetchone()[0]==1
        intervals=db.execute("SELECT start_char,end_char FROM job_messages WHERE message_id='a:u1' ORDER BY start_char").fetchall()
    assert [(r[0],r[1]) for r in intervals]==[(0,20000),(20000,40000),(40000,45000)]
    store.apply_analysis(running['id'],result(running))
    assert store.status()['analyzed']==1
