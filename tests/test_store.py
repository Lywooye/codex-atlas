import pytest

from atlas.store import Store
from atlas.ingest import sync
from test_ingest import write_log,message


def result(job,name='超声质控',topic='医学',selected=None):
    ids=selected or [m['id'] for m in job['messages']]
    return {'segments':[{'message_ids':ids,'topic_names':[topic],
      'project':{'id':'','name':name,'goal':'完成质控评测' if name else ''},'summary':'已经准备标注手册','stage':'试标',
      'events':[{'kind':'reported_done','text':'报告手册完成','message_ids':ids}],
      'open_items':['验证标注一致性'],'decisions':['先小规模试标']}]}


def prepared(tmp_path):
    store=Store(tmp_path/'index.db');home=tmp_path/'codex'
    write_log(home,'a',[message('user','超声质控需要核验的片段','a1'),message('assistant','手册已准备','a2')])
    sync(store,home);job=store.claim_job();store.apply_analysis(job['id'],result(job))
    return store,home,job


def test_analysis_provenance_and_search_filters(tmp_path):
    store,home,job=prepared(tmp_path)
    p=store.projects()[0];t=store.topics()[0]
    assert p['stage']=='试标' and p['evidence_level']=='reported_done'
    assert store.project(p['id'])['coverage']=={'analyzed':1,'total':1}
    for q in ['质控','超声质控','标注','手册']:
        assert store.search(q,project_id=p['id'],topic_id=t['id'])['total']>0
    assert store.search('质控',since='2027-01-01')['total']==0
    assert store.search('质控',project_id='missing')['total']==0
    assert store.search('质控',topic_id='missing')['total']==0
    old=store.project(p['id'])
    bad=result(job);bad['segments'][0]['events'][0]['message_ids']=['not-in-job']
    with pytest.raises(ValueError):store.apply_analysis(job['id'],bad)
    assert store.project(p['id'])['segments']==old['segments']
    with pytest.raises(ValueError):store.apply_analysis(job['id'],result(job,selected=[job['messages'][0]['id']]))


def test_manual_rename_reassignment_survives_reanalysis_and_rebuild(tmp_path):
    store,home,job=prepared(tmp_path)
    p=store.projects()[0];s=store.project(p['id'])['segments'][0];topic=store.topics()[0]
    store.edit_entity('project',p['id'],{'name':'我的质控项目','stage':'等待我裁决'})
    store.edit_segment(s['id'],None,[topic['id']])
    store.apply_analysis(job['id'],result(job))
    assert store.project(p['id'])['name']=='我的质控项目'
    assert store.project(p['id'])['stage']=='等待我裁决'
    assert store.project(p['id'])['segment_count']==0
    assert store.topic(topic['id'])['segments'][0]['manual']
    # File rebuild retains explicit message IDs and independently saved overrides.
    write_log(home,'a',[message('user','修改了同一条消息','a1'),message('assistant','手册已准备','a2')])
    sync(store,home);replacement=store.claim_job();store.apply_analysis(replacement['id'],result(replacement))
    assert all(s['project_id'] is None for s in store.topic(topic['id'])['segments'])


def test_split_merge_redirects_and_queue_recovery(tmp_path):
    store,home,job=prepared(tmp_path)
    write_log(home,'b',[message('user','课程工具开发','b1')])
    sync(store,home);other=store.claim_job();store.apply_analysis(other['id'],result(other,name='课程工具',topic='教学'))
    first,second=store.projects();sid=store.project(first['id'])['segments'][0]['id']
    split=store.split_entity('project',first['id'],'新项目',[sid])
    assert split['segment_count']==1
    store.apply_analysis((job if first['name']=='超声质控' else other)['id'],result(job if first['name']=='超声质控' else other,name=first['name'],topic='医学' if first['name']=='超声质控' else '教学'))
    assert store.project(split['id'])['segment_count']==1
    target=store.merge_entities('project',[split['id']],second['id'])
    assert store.project(split['id'])['id']==target['id']
    store.edit_entity('project',target['id'],{'goal':'人工目标'})
    assert store.project(target['id'])['goal']=='人工目标'
    store.fail_job(job['id'],'failure')
    assert store.status()['failed']==1
    assert store.retry_failed()==1
    assert store.claim_job()
    assert store.recover_jobs()==1


def test_topic_only_and_mixed_dialogue(tmp_path):
    store=Store(tmp_path/'i.db');home=tmp_path/'codex'
    write_log(home,'a',[message('user','知识查询','m1'),message('user','软件开发','m2')])
    sync(store,home);job=store.claim_job()
    a=result(job,name='',topic='知识',selected=[job['messages'][0]['id']])['segments'][0]
    b=result(job,name='软件',topic='编程',selected=[job['messages'][1]['id']])['segments'][0]
    store.apply_analysis(job['id'],{'segments':[a,b]})
    assert len(store.projects())==1 and len(store.topics())==2
    assert len(store.project(store.projects()[0]['id'])['segments'])==1
    assert store.thread('a',focus='a:m2',limit=1)['messages'][0]['id']=='a:m2'


def test_same_message_distinct_segments_manual_scope(tmp_path):
    store=Store(tmp_path/'index.db');home=tmp_path/'codex'
    write_log(home,'mixed',[message('user','同一消息同时讨论质控和课程','m1')])
    sync(store,home);job=store.claim_job()
    a=result(job,name='质控',topic='医学')['segments'][0]
    b=result(job,name='课程',topic='教学')['segments'][0]
    output={'segments':[a,b]};store.apply_analysis(job['id'],output)
    projects={p['name']:p for p in store.projects()}
    first=store.project(projects['质控']['id'])['segments'][0]
    store.edit_segment(first['id'],None,first['topic_ids'])
    store.apply_analysis(job['id'],output)
    assert store.project(projects['质控']['id'])['segment_count']==0
    assert store.project(projects['课程']['id'])['segment_count']==1
    medicine=next(t for t in store.topics() if t['name']=='医学')
    assert store.topic(medicine['id'])['segments'][0]['project_id'] is None
    assert store.topic(medicine['id'])['segments'][0]['source_ranges'][0]['start_char']==0


def test_tool_evidence_is_sampled_not_required_for_coverage(tmp_path):
    from test_ingest import record
    store=Store(tmp_path/'index.db');home=tmp_path/'codex'
    items=[message('user','请运行检查','u1')]
    items += [record('response_item',{'type':'function_call_output','id':f'tool-{i}','output':f'output{i} '+'x'*3000}) for i in range(12)]
    items.append(message('assistant','根据结果检查通过','a1'))
    write_log(home,'evidence',items)
    sync(store,home);job=store.claim_job()
    assert len(job['messages'])==2
    assert len(job['tool_evidence'])==8
    assert job['tool_evidence_omitted']==4
    assert sum(len(e['text']) for e in job['tool_evidence'])<=12000
    assert job['evidence_sampling']['truncated_outputs']==8
    assert store.status()['dialogue_messages']==2
    assert store.status()['tool_messages']==12
    output=result(job)
    output['segments'][0]['events'][0]={'kind':'verified','text':'工具输出附检查记录','message_ids':[job['tool_evidence'][0]['id']]}
    store.apply_analysis(job['id'],output)
    project=store.project(store.projects()[0]['id'])
    assert project['events'][0]['message_ids']==[job['tool_evidence'][0]['id']]
    assert all(m['kind']=='message' for m in job['messages'])
    assert store.search('output11')['total']>0


def test_homepage_progress_blocker_and_pending_decisions(tmp_path):
    store,home,job=prepared(tmp_path)
    output=result(job)
    segment=output['segments'][0]
    segment['open_items']=['待决定：选择试标专家','继续检查','待决定：']
    segment['events'].append({'kind':'blocked','text':'曾缺少标注协议','message_ids':[job['messages'][0]['id']]})
    store.apply_analysis(job['id'],output)
    project=store.projects()[0]
    assert project['latest_progress']['kind']=='reported_done'
    assert project['latest_progress']['thread_id']=='a'
    assert project['latest_blocker']['text']=='曾缺少标注协议'
    assert project['pending_decisions']==['选择试标专家']
    detail=store.project(project['id'])
    assert detail['latest_progress']==project['latest_progress']
    assert detail['latest_blocker']==project['latest_blocker']


def test_project_source_time_is_independent_of_manual_edit(tmp_path):
    store,home,job=prepared(tmp_path)
    original=store.projects()[0]
    assert original['source_updated_at']=='2026-10-01T10:00:00Z'
    assert original['analyzed_at']
    edited=store.edit_entity('project',original['id'],{'name':'人工改名'})
    assert edited['source_updated_at']==original['source_updated_at']
    assert edited['updated_at']!=edited['source_updated_at']
    assert edited['analyzed_at']==original['analyzed_at']
    listed=store.projects()[0]
    assert listed['source_updated_at']==edited['source_updated_at']
    assert listed['analyzed_at']==edited['analyzed_at']


def test_lists_hide_empty_automatic_entities_but_keep_manual_and_catalog(tmp_path):
    store,home,job=prepared(tmp_path)
    project=store.projects()[0];topic=store.topics()[0]
    segment=store.project(project['id'])['segments'][0]
    store.edit_segment(segment['id'],None,[])
    assert store.projects()==[] and store.topics()==[]
    assert any(p['id']==project['id'] for p in store.catalog()['projects'])
    assert any(t['id']==topic['id'] for t in store.catalog()['topics'])
    store.edit_entity('project',project['id'],{'name':'人工保留项目'})
    store.edit_entity('topic',topic['id'],{'name':'人工保留主题'})
    assert store.projects()[0]['segment_count']==0
    assert store.topics()[0]['segment_count']==0


def test_status_failed_jobs_are_bounded_and_descriptive(tmp_path):
    store=Store(tmp_path/'index.db');home=tmp_path/'codex'
    for i in range(7):write_log(home,f'chat-{i}',[message('user',f'测试问题{i}',f'm-{i}')])
    sync(store,home)
    while job:=store.claim_job():store.fail_job(job['id'],'Example model failure')
    status=store.status()
    assert status['failed']==7
    assert len(status['failed_jobs'])==5
    assert all(j['thread_title'] and j['updated_at'] and j['error']=='Example model failure' for j in status['failed_jobs'])
    assert 'source_errors' in status


def test_named_empty_project_clears_only_automatic_summary_and_stage(tmp_path):
    store,home,job=prepared(tmp_path)
    project=store.projects()[0]
    store.edit_entity('project',project['id'],{'name':'保留的人工名称'})
    segment=store.project(project['id'])['segments'][0]
    moved=store.split_entity('project',project['id'],'迁移后的项目',[segment['id']])
    emptied=store.project(project['id'])
    assert emptied['name']=='保留的人工名称'
    assert emptied['segment_count']==0
    assert emptied['summary']=='' and emptied['stage']=='未知'
    assert emptied['latest_progress'] is None
    assert moved['segment_count']==1 and moved['summary']
    assert any(p['id']==emptied['id'] for p in store.projects())


def test_project_name_only_does_not_merge_different_goals(tmp_path):
    store=Store(tmp_path/'index.db');home=tmp_path/'codex'
    write_log(home,'a',[message('user','完善教学课程','a1')])
    write_log(home,'b',[message('user','改进设备工具','b1')])
    sync(store,home)
    one=store.claim_job();first=result(one,name='研究助手')
    first['segments'][0]['project']['goal']='完成课程编辑'
    store.apply_analysis(one['id'],first)
    first_id=store.projects()[0]['id']
    two=store.claim_job();second=result(two,name='研究助手')
    second['segments'][0]['project']['goal']='修复设备同步'
    store.apply_analysis(two['id'],second)
    assert len(store.projects())==2
    # A known project ID is an explicit association across threads.
    second['segments'][0]['project']['id']=first_id
    store.apply_analysis(two['id'],second)
    assert store.project(first_id)['segment_count']==2


def test_project_goal_normalization_and_manual_aliases(tmp_path):
    store,home,job=prepared(tmp_path)
    project=store.projects()[0]
    write_log(home,'b',[message('user','后续研究工作','b1')])
    sync(store,home);second=store.claim_job();output=result(second)
    output['segments'][0]['project']['goal']='  完成质控评测  '
    store.apply_analysis(second['id'],output)
    assert len(store.projects())==1
    store.edit_entity('project',project['id'],{'name':'人工名称'})
    output['segments'][0]['project']['goal']='AI换了另一种目标表述'
    store.apply_analysis(second['id'],output)
    assert len(store.projects())==1 and store.projects()[0]['name']=='人工名称'
    # Merge leaves an explicit old-name alias even when machine goals differ.
    write_log(home,'c',[message('user','另一项相关工作','c1')])
    sync(store,home);third=store.claim_job();other=result(third,name='历史名称')
    other['segments'][0]['project']['goal']='另一目标'
    store.apply_analysis(third['id'],other)
    other_id=next(p['id'] for p in store.projects() if p['name']=='历史名称')
    store.merge_entities('project',[other_id],project['id'])
    store.apply_analysis(third['id'],other)
    assert len(store.projects())==1
    assert store.project(project['id'])['segment_count']==3


def test_technical_thread_injected_fallback_title_is_cleaned_only_for_display(tmp_path):
    store=Store(tmp_path/'index.db');home=tmp_path/'codex'
    write_log(home,'parent',[message('user','实际用户目标','u')])
    write_log(home,'child-technical',[message('user','技术检查工作','c')],thread_source='subagent',parent_thread_id='parent')
    sync(store,home)
    polluted='<recommended_plugins> Here is a list of plugins # AGENTS.md instructions'
    with store.connection() as db:db.execute('UPDATE threads SET title=? WHERE id=?',(polluted,'child-technical'))
    related=store.thread('parent')['related_threads'][0]
    assert related['title']=='技术子对话 · child-te'
    assert store.thread('child-technical')['title']==related['title']
    with store.connection() as db:
        assert db.execute('SELECT title FROM threads WHERE id=?',('child-technical',)).fetchone()[0]==polluted
        db.execute('UPDATE threads SET title=? WHERE id=?',('检查来源引用','child-technical'))
    assert store.thread('parent')['related_threads'][0]['title']=='检查来源引用'


def hierarchy_fixture(tmp_path):
    store,home,job=prepared(tmp_path)
    first=store.projects()[0]
    write_log(home,'b',[message('user','开发课程阅读工作台','b1')])
    sync(store,home);second_job=store.claim_job()
    second_result=result(second_job,name='学习工作台',topic='教学')
    second_result['segments'][0]['open_items']=['待决定：选择部署位置','继续界面验收']
    second_result['segments'][0]['summary']='已实现课程阅读界面'
    store.apply_analysis(second_job['id'],second_result)
    second=next(p for p in store.projects() if p['id']!=first['id'])
    return store,home,job,second_job,first,second


def test_group_projects_aggregates_sources_and_uses_neutral_stage(tmp_path):
    store,home,job,second_job,first,second=hierarchy_fixture(tmp_path)
    root=store.group_projects('课程设计与学习工作台','持续建设课程和学习工具',[first['id'],second['id']])
    assert root['parent_id'] is None and root['parent_name'] is None
    assert root['child_count']==2 and len(root['children'])==2
    assert root['stage']=='2个工作分项'
    assert first['name'] in root['summary'] and second['name'] in root['summary']
    assert '最近变化' in root['summary']
    assert root['segment_count']==2 and len(root['segments'])==2
    assert len(root['threads'])==2 and len(root['events'])==2
    assert len(root['topics'])==2
    assert root['coverage']=={'analyzed':2,'total':2}
    assert '验证标注一致性' in root['open_items'] and '继续界面验收' in root['open_items']
    assert root['pending_decisions']==['选择部署位置']
    assert root['source_updated_at']=='2026-10-01T10:00:00Z'
    assert {s['project_name'] for s in root['segments']}=={first['name'],second['name']}
    assert {e['project_id'] for e in root['events']}=={first['id'],second['id']}
    assert len(store.projects())==3
    assert all(c['parent_id']==root['id'] and c['parent_name']==root['name'] for c in root['children'])
    root=store.edit_entity('project',root['id'],{'summary':'人工总览摘要','stage':'等待整体评审'})
    assert root['summary']=='人工总览摘要' and root['stage']=='等待整体评审'
    catalog={p['id']:p for p in store.catalog()['projects']}
    assert catalog[first['id']]['parent_id']==root['id']
    assert catalog[root['id']]['child_count']==2


def test_root_search_and_topic_views_cover_children_and_deduplicate(tmp_path):
    store,home,job,second_job,first,second=hierarchy_fixture(tmp_path)
    root=store.group_projects('课程主项目','综合课程',[first['id'],second['id']])
    assert store.search('课程阅读工作台',project_id=root['id'])['total']>0
    assert store.search('超声质控需要核验',project_id=root['id'])['total']>0
    medicine=next(t for t in store.topics() if t['name']=='医学')
    assert store.topic(medicine['id'])['projects'][0]['id']==root['id']
    # Give the same topic to both children; this must remain one main project.
    s=store.project(second['id'])['segments'][0]
    store.edit_segment(s['id'],second['id'],[medicine['id']])
    topic=store.topic(medicine['id'])
    assert topic['project_count']==1 and len(topic['projects'])==1
    assert topic['projects'][0]['id']==root['id']
    assert store.search('课程主项目',project_id=root['id'],topic_id=medicine['id'])['total']>0
    assert store.search('课程阅读工作台',project_id=first['id'])['total']==0


def test_two_level_validation_parent_locks_sibling_merge_and_split(tmp_path):
    store,home,job,second_job,first,second=hierarchy_fixture(tmp_path)
    root=store.group_projects('整体项目','整体目标',[first['id'],second['id']])
    for child,parent in [(root['id'],first['id']),(first['id'],first['id']),(second['id'],first['id'])]:
        with pytest.raises(ValueError):store.set_project_parent(child,parent)
    with pytest.raises(ValueError):store.group_projects('第三层','不允许',[root['id']])
    with pytest.raises(ValueError):store.merge_entities('project',[root['id']],first['id'])
    # Splitting a work item inherits its existing parent.
    sid=store.project(first['id'])['segments'][0]['id']
    split=store.split_entity('project',first['id'],'拆出的工作分项',[sid])
    assert split['parent_id']==root['id']
    with store.connection() as db:
        assert 'parent_id' in __import__('json').loads(db.execute('SELECT manual_fields FROM projects WHERE id=?',(split['id'],)).fetchone()[0])
    merged=store.merge_entities('project',[split['id']],second['id'])
    assert merged['parent_id']==root['id'] and merged['segment_count']==2
    assert store.project(split['id'])['id']==second['id']
    # Detachment is a manual lock, including explicit root-level placement.
    detached=store.set_project_parent(second['id'],None)
    assert detached['parent_id'] is None
    with pytest.raises(ValueError):store.merge_entities('project',[first['id']],second['id'])
    output=result(second_job,name=second['name'],topic='教学')
    output['segments'][0]['project'].update(id=second['id'],parent_id=root['id'])
    store.apply_analysis(second_job['id'],output)
    assert store.project(second['id'])['parent_id'] is None


def test_ai_new_child_uses_known_root_and_reanalysis_preserves_parent(tmp_path):
    store,home,job=prepared(tmp_path)
    first=store.projects()[0]
    root=store.group_projects('长期建设','长期目标',[first['id']])
    write_log(home,'b',[message('user','建设新的课程单元','b1')])
    sync(store,home);new_job=store.claim_job();output=result(new_job,name='新的课程单元',topic='教学')
    output['segments'][0]['project']['parent_id']=root['id']
    store.apply_analysis(new_job['id'],output)
    child=next(p for p in store.projects() if p['name']=='新的课程单元')
    assert child['parent_id']==root['id']
    # A new child cannot point to another child.
    output['segments'][0]['project'].update(name='非法第三层',parent_id=child['id'])
    with pytest.raises(ValueError):store.apply_analysis(new_job['id'],output)
    assert store.project(child['id'])['segment_count']==1
    # Known IDs and manual assignments are not moved by analysis.
    store.set_project_parent(child['id'],None)
    output['segments'][0]['project'].update(id=child['id'],name=child['name'],parent_id=root['id'])
    store.apply_analysis(new_job['id'],output)
    assert store.project(child['id'])['parent_id'] is None


def test_project_parent_schema_migrates_existing_index_without_data_loss(tmp_path):
    import sqlite3
    path=tmp_path/'legacy.db'
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,goal TEXT DEFAULT '',summary TEXT DEFAULT '',stage TEXT DEFAULT '未知',updated_at TEXT,manual_fields TEXT DEFAULT '[]',merged_into TEXT)")
        db.execute("INSERT INTO projects(id,name,goal,manual_fields) VALUES('old','旧项目','保留目标','[\"name\"]')")
    store=Store(path)
    old=store.project('old')
    assert old['name']=='旧项目' and old['goal']=='保留目标'
    assert old['parent_id'] is None and old['child_count']==0
    assert Store(path).project('old')['id']=='old'


def test_group_requires_meaningful_goal_and_stores_trimmed_values(tmp_path):
    store,home,job=prepared(tmp_path);project=store.projects()[0]
    with pytest.raises(ValueError):store.group_projects('主项目','   ',[project['id']])
    grouped=store.group_projects('  主项目  ','  持续学习目标  ',[project['id']])
    assert grouped['name']=='主项目' and grouped['goal']=='持续学习目标'


def test_renamed_child_refreshes_parent_search_without_overwriting_manual_summary(tmp_path):
    store,home,job,second_job,first,second=hierarchy_fixture(tmp_path)
    root=store.group_projects('主项目','综合课程目标',[first['id'],second['id']])
    store.edit_entity('project',first['id'],{'name':'重新命名的试标分项'})
    hits=store.search('重新命名的试标分项',project_id=root['id'])['items']
    assert any(item['kind']=='project' and item['entity_id']==root['id'] for item in hits)
    with store.connection() as db:
        assert '重新命名的试标分项' in db.execute('SELECT summary FROM projects WHERE id=?',(root['id'],)).fetchone()[0]
    store.edit_entity('project',root['id'],{'summary':'人工保留的总览内容'})
    store.edit_entity('project',second['id'],{'name':'重新命名的工作台分项'})
    assert store.project(root['id'])['summary']=='人工保留的总览内容'
    with store.connection() as db:
        assert db.execute('SELECT summary FROM projects WHERE id=?',(root['id'],)).fetchone()[0]=='人工保留的总览内容'


def test_retry_deadline_attempts_and_manual_pause_are_durable(tmp_path):
    store, home, old_job = prepared(tmp_path)
    store.fail_job(old_job['id'], 'old failure')
    store.retry_failed()
    job = store.claim_job()
    assert job['attempts'] == 1
    retry_at = store.schedule_retry(job['id'], 'bad citation', 120)
    store.save_queue_settings({'paused': True, 'cooldown_until': retry_at})
    store = Store(store.db_path)
    assert store.claim_job() is None
    assert store.status()['retrying'] == 1
    assert store.queue_settings()['paused'] is True
    write_log(home,'b',[message('user','新的独立目标','b1')])
    sync(store,home)
    other = store.claim_job()
    assert other and other['id'] != job['id']  # A deferred batch never blocks ready work.
    store.recover_jobs()
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='done' WHERE id=?", (other['id'],))
        db.execute("UPDATE jobs SET next_retry_at=0 WHERE id=?", (job['id'],))
    assert store.claim_job()['attempts'] == 2
    store.fail_job(job['id'], 'exhausted')
    store.retry_failed()
    assert store.claim_job()['attempts'] == 1
