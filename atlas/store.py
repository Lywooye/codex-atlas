"""Local derived index. Codex source files are never written by this module."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
import json
from pathlib import Path
import sqlite3
import time
import uuid
import unicodedata


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS threads(
 id TEXT PRIMARY KEY,title TEXT NOT NULL DEFAULT '',cwd TEXT DEFAULT '',source_kind TEXT DEFAULT 'user',
 technical INTEGER DEFAULT 0,parent_id TEXT,archived INTEGER DEFAULT 0,available INTEGER DEFAULT 1,
 created_at TEXT DEFAULT '',updated_at TEXT DEFAULT '',title_source TEXT DEFAULT 'fallback');
CREATE TABLE IF NOT EXISTS files(
 path TEXT PRIMARY KEY,thread_id TEXT,offset INTEGER DEFAULT 0,line_no INTEGER DEFAULT 0,
 size INTEGER DEFAULT 0,mtime_ns INTEGER DEFAULT 0,inode TEXT,prefix_hash TEXT,tail_hash TEXT,
 turn_id TEXT DEFAULT '',parse_errors INTEGER DEFAULT 0,available INTEGER DEFAULT 1,error TEXT);
CREATE INDEX IF NOT EXISTS files_thread ON files(thread_id);
CREATE TABLE IF NOT EXISTS messages(
 id TEXT PRIMARY KEY,thread_id TEXT NOT NULL REFERENCES threads(id),role TEXT NOT NULL,text TEXT NOT NULL,
 timestamp TEXT,line_no INTEGER,turn_id TEXT,kind TEXT DEFAULT 'message',canonical_key TEXT,
 representation TEXT DEFAULT 'response',source_path TEXT,source_id TEXT);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id,line_no);
CREATE INDEX IF NOT EXISTS messages_canonical ON messages(canonical_key);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY,thread_id TEXT NOT NULL REFERENCES threads(id),source_hash TEXT,status TEXT DEFAULT 'pending',
 created_at TEXT,updated_at TEXT,error TEXT,evidence_total INTEGER DEFAULT 0,evidence_characters INTEGER DEFAULT 0,source_time TEXT DEFAULT '',source_order INTEGER DEFAULT 0,UNIQUE(thread_id,source_hash));
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,updated_at);
CREATE TABLE IF NOT EXISTS job_messages(
 job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,id TEXT NOT NULL,message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
 start_char INTEGER,end_char INTEGER,position INTEGER,PRIMARY KEY(job_id,id));
CREATE TABLE IF NOT EXISTS job_evidence(job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,position INTEGER,PRIMARY KEY(job_id,message_id));
CREATE TABLE IF NOT EXISTS projects(
 id TEXT PRIMARY KEY,name TEXT NOT NULL,goal TEXT DEFAULT '',summary TEXT DEFAULT '',stage TEXT DEFAULT '未知',
 updated_at TEXT,manual_fields TEXT DEFAULT '[]',merged_into TEXT,parent_id TEXT REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS topics(
 id TEXT PRIMARY KEY,name TEXT NOT NULL,updated_at TEXT,manual_fields TEXT DEFAULT '[]',merged_into TEXT);
CREATE TABLE IF NOT EXISTS aliases(kind TEXT,name TEXT,entity_id TEXT,PRIMARY KEY(kind,name));
CREATE TABLE IF NOT EXISTS segments(
 id TEXT PRIMARY KEY,job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,thread_id TEXT REFERENCES threads(id),
 summary TEXT,stage TEXT,project_id TEXT REFERENCES projects(id),updated_at TEXT,
 open_items TEXT DEFAULT '[]',decisions TEXT DEFAULT '[]',manual INTEGER DEFAULT 0,machine_project_id TEXT,machine_topic_ids TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS segment_messages(
 segment_id TEXT REFERENCES segments(id) ON DELETE CASCADE,message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
 PRIMARY KEY(segment_id,message_id));
CREATE TABLE IF NOT EXISTS segment_ranges(segment_id TEXT REFERENCES segments(id) ON DELETE CASCADE,reference_id TEXT,message_id TEXT,start_char INTEGER,end_char INTEGER);
CREATE INDEX IF NOT EXISTS segment_messages_msg ON segment_messages(message_id);
CREATE TABLE IF NOT EXISTS segment_topics(
 segment_id TEXT REFERENCES segments(id) ON DELETE CASCADE,topic_id TEXT REFERENCES topics(id),PRIMARY KEY(segment_id,topic_id));
CREATE TABLE IF NOT EXISTS events(
 id TEXT PRIMARY KEY,segment_id TEXT REFERENCES segments(id) ON DELETE CASCADE,kind TEXT,text TEXT,
 message_ids TEXT,timestamp TEXT);
CREATE TABLE IF NOT EXISTS overrides(
 message_id TEXT PRIMARY KEY,project_id TEXT,topic_ids TEXT NOT NULL,updated_at TEXT);
CREATE TABLE IF NOT EXISTS assignment_overrides(id TEXT PRIMARY KEY,anchor_refs TEXT,machine_project_id TEXT,machine_topic_ids TEXT,project_id TEXT,topic_ids TEXT,updated_at TEXT);
CREATE TABLE IF NOT EXISTS search_docs(
 id TEXT PRIMARY KEY,kind TEXT,entity_id TEXT,title TEXT,text TEXT,thread_id TEXT,message_id TEXT,timestamp TEXT,project_id TEXT);
CREATE INDEX IF NOT EXISTS search_docs_thread ON search_docs(thread_id);
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(title,text,content='search_docs',content_rowid='rowid',tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON search_docs BEGIN
 INSERT INTO search_fts(rowid,title,text) VALUES(new.rowid,new.title,new.text); END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON search_docs BEGIN
 INSERT INTO search_fts(search_fts,rowid,title,text) VALUES('delete',old.rowid,old.title,old.text); END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON search_docs BEGIN
 INSERT INTO search_fts(search_fts,rowid,title,text) VALUES('delete',old.rowid,old.title,old.text);
 INSERT INTO search_fts(rowid,title,text) VALUES(new.rowid,new.title,new.text); END;
"""


class Store:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with self.connection() as db:
            db.executescript(SCHEMA)
            columns={r[1] for r in db.execute('PRAGMA table_info(jobs)')}
            for column,definition in [('evidence_total','INTEGER DEFAULT 0'),('evidence_characters','INTEGER DEFAULT 0'),('source_time',"TEXT DEFAULT ''"),('source_order','INTEGER DEFAULT 0'),('attempts','INTEGER DEFAULT 0'),('next_retry_at','REAL DEFAULT 0')]:
                if column not in columns:db.execute(f'ALTER TABLE jobs ADD COLUMN {column} {definition}')
            project_columns={r[1] for r in db.execute('PRAGMA table_info(projects)')}
            if 'parent_id' not in project_columns:db.execute('ALTER TABLE projects ADD COLUMN parent_id TEXT REFERENCES projects(id)')
            db.execute('CREATE INDEX IF NOT EXISTS projects_parent ON projects(parent_id)')
            segment_columns={r[1] for r in db.execute('PRAGMA table_info(segments)')}
            for column,definition in [('machine_project_id','TEXT'),('machine_topic_ids',"TEXT DEFAULT '[]'")]:
                if column not in segment_columns:db.execute(f'ALTER TABLE segments ADD COLUMN {column} {definition}')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=30000')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _doc(db, kind, entity_id, title, text, timestamp='', thread_id=None, message_id=None, project_id=None):
        db.execute('''INSERT INTO search_docs VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
        title=excluded.title,text=excluded.text,timestamp=excluded.timestamp,thread_id=excluded.thread_id,
        message_id=excluded.message_id,project_id=excluded.project_id''',
                   (kind+':'+entity_id,kind,entity_id,title,text,thread_id,message_id,timestamp,project_id))

    def index_messages(self, db, thread_id):
        thread = db.execute('SELECT * FROM threads WHERE id=?',(thread_id,)).fetchone()
        for m in db.execute('SELECT * FROM messages WHERE thread_id=?',(thread_id,)).fetchall():
            self._doc(db,'message',m['id'],thread['title'],m['text'],m['timestamp'],thread_id,m['id'])

    def status(self):
        with self.connection() as db:
            scalar = lambda q: db.execute(q).fetchone()[0]
            result = {
                'threads': scalar('SELECT count(*) FROM threads WHERE technical=0'),
                'dialogue_messages': scalar("SELECT count(*) FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.technical=0 AND m.kind='message'"),
                'tool_messages': scalar("SELECT count(*) FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.technical=0 AND m.kind='tool_output'"),
                'evidence_sampling': '自然对话完整分片；工具输出全文可搜索，AI 每片最多抽样 8 条、约 12000 字符。',
                'technical_threads': scalar('SELECT count(*) FROM threads WHERE technical=1'),
                'messages': scalar('SELECT count(*) FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.technical=0'),
                'chunks': scalar('SELECT count(*) FROM jobs j JOIN threads t ON t.id=j.thread_id WHERE t.technical=0'),
                'parsing_errors': scalar('SELECT coalesce(sum(parse_errors),0) FROM files'),
                'missing_sources': scalar('SELECT count(*) FROM threads WHERE available=0 AND technical=0'),
            }
            for key,state in [('analyzed','done'),('pending','pending'),('failed','failed'),('running','running')]:
                result[key] = db.execute('SELECT count(*) FROM jobs j JOIN threads t ON t.id=j.thread_id WHERE t.technical=0 AND j.status=?',(state,)).fetchone()[0]
            settings = dict(db.execute('SELECT key,value FROM settings'))
            result.update(last_sync=settings.get('last_sync'),analysis_updated_at=settings.get('analysis_updated_at'))
            bounds=db.execute('SELECT min(timestamp),max(timestamp) FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.technical=0').fetchone()
            result.update(first_message_at=bounds[0],last_message_at=bounds[1])
            result['retrying']=scalar("SELECT count(*) FROM jobs WHERE status='pending' AND next_retry_at>0")
            result['next_retry_at']=scalar("SELECT min(next_retry_at) FROM jobs WHERE status='pending' AND next_retry_at>0")
            result['failed_jobs']=[dict(r) for r in db.execute("SELECT j.id,j.thread_id,t.title AS thread_title,j.error,j.updated_at FROM jobs j JOIN threads t ON t.id=j.thread_id WHERE j.status='failed' AND t.technical=0 ORDER BY j.updated_at DESC,j.id LIMIT 5")]
            result['source_errors']=[dict(r) for r in db.execute('SELECT path,error,parse_errors FROM files WHERE error IS NOT NULL OR parse_errors>0 LIMIT 20')]
            if settings.get('source_error'):result['source_errors'].insert(0,{'path':'Codex sessions','error':settings['source_error'],'parse_errors':0})
            return result

    @staticmethod
    def _resolve(db, kind, entity_id):
        table = 'projects' if kind=='project' else 'topics'
        seen=set()
        while entity_id and entity_id not in seen:
            seen.add(entity_id)
            row=db.execute(f'SELECT merged_into FROM {table} WHERE id=?',(entity_id,)).fetchone()
            if row is None: raise KeyError(entity_id)
            if not row[0]: return entity_id
            entity_id=row[0]
        raise ValueError('Invalid entity redirect')

    def _segment(self, db, row):
        s=dict(row)
        owner=db.execute('SELECT name FROM projects WHERE id=?',(s['project_id'],)).fetchone()
        s['project_name']=owner[0] if owner else None
        analysis=db.execute('SELECT updated_at,evidence_total FROM jobs WHERE id=?',(s['job_id'],)).fetchone()
        s['analyzed_at']=analysis['updated_at'] if analysis else None
        s['thread_title']=db.execute('SELECT title FROM threads WHERE id=?',(s['thread_id'],)).fetchone()[0]
        s['topics']=[dict(r) for r in db.execute('SELECT t.id,t.name FROM topics t JOIN segment_topics st ON st.topic_id=t.id WHERE st.segment_id=? ORDER BY t.name',(s['id'],))]
        s['topic_ids']=[t['id'] for t in s['topics']]
        s['message_ids']=[r[0] for r in db.execute('SELECT sm.message_id FROM segment_messages sm JOIN messages m ON m.id=sm.message_id WHERE segment_id=? ORDER BY m.line_no',(s['id'],))]
        s['source_ranges']=[dict(r) for r in db.execute('SELECT reference_id,message_id,start_char,end_char FROM segment_ranges WHERE segment_id=? ORDER BY rowid',(s['id'],))]
        s['manual']=bool(s['manual'])
        s['open_items']=json.loads(s['open_items']);s['decisions']=json.loads(s['decisions'])
        return s

    def _project(self,db,row,detail=False):
        p=dict(row);manual_fields=json.loads(p.pop('manual_fields'));p['manual']=bool(manual_fields)
        p.pop('merged_into',None)
        parent=db.execute('SELECT name FROM projects WHERE id=?',(p['parent_id'],)).fetchone() if p['parent_id'] else None
        p['parent_name']=parent[0] if parent else None
        children=db.execute('SELECT * FROM projects WHERE parent_id=? AND merged_into IS NULL ORDER BY name,id',(p['id'],)).fetchall()
        p['children']=[self._project(db,c) for c in children];p['child_count']=len(children)
        scope=[p['id']]+[c['id'] for c in children];marks=','.join('?' for _ in scope)
        rows=db.execute(f'SELECT * FROM segments WHERE project_id IN ({marks}) ORDER BY updated_at DESC,id',scope).fetchall()
        p['segment_count']=len(rows)
        p['source_updated_at']=rows[0]['updated_at'] if rows else None
        p['analyzed_at']=db.execute(f"SELECT max(j.updated_at) FROM jobs j JOIN segments s ON s.job_id=j.id WHERE s.project_id IN ({marks}) AND j.status='done'",scope).fetchone()[0]
        p['topics']=[dict(r) for r in db.execute(f"SELECT DISTINCT t.id,t.name FROM topics t JOIN segment_topics st ON st.topic_id=t.id JOIN segments s ON s.id=st.segment_id WHERE s.project_id IN ({marks}) ORDER BY t.name",scope)]
        p['topic_ids']=[t['id'] for t in p['topics']]
        latest_by_project={}
        for segment in rows:latest_by_project.setdefault(segment['project_id'],segment)
        p['open_items']=list(dict.fromkeys(item for segment in latest_by_project.values() for item in json.loads(segment['open_items'])))
        p['decisions']=list(dict.fromkeys(x for r in rows for x in json.loads(r['decisions'])))
        events=[dict(e) for e in db.execute(f"SELECT e.*,s.thread_id,s.project_id,p.name AS project_name,t.title AS thread_title FROM events e JOIN segments s ON s.id=e.segment_id JOIN projects p ON p.id=s.project_id JOIN threads t ON t.id=s.thread_id WHERE s.project_id IN ({marks}) ORDER BY e.timestamp,e.id",scope)]
        for e in events:e['message_ids']=json.loads(e['message_ids'])
        p['evidence_level']=next((k for k in ['verified','reported_done','decided','discussed'] if any(e['kind']==k for e in events)),'unknown')
        p['latest_progress']=next((e for e in reversed(events) if e['kind'] in ('reported_done','verified')),None)
        p['latest_blocker']=next((e for e in reversed(events) if e['kind']=='blocked'),None)
        p['pending_decisions']=[item[len(prefix):].strip() for item in p['open_items'] for prefix in ('待决定：', 'Decision needed:') if item.startswith(prefix) and item[len(prefix):].strip()]
        if children:
            if 'stage' not in manual_fields:p['stage']=f"{len(children)}个工作分项"
            if 'summary' not in manual_fields:
                p['summary']='包含：'+'、'.join(c['name'] for c in children)+'。'
                if rows:
                    owner=db.execute('SELECT name FROM projects WHERE id=?',(rows[0]['project_id'],)).fetchone()[0]
                    p['summary']+='最近变化（'+owner+'）：'+rows[0]['summary']
                else:p['summary']+='尚无已分析的进展来源。'
        p['coverage']=dict(db.execute(f"SELECT count(DISTINCT CASE WHEN j.status='done' THEN j.id END) AS analyzed,count(DISTINCT j.id) AS total FROM jobs j WHERE j.thread_id IN (SELECT thread_id FROM segments WHERE project_id IN ({marks}))",scope).fetchone())
        if detail:
            p['segments']=[self._segment(db,r) for r in rows];p['events']=events
            p['threads']=[dict(r) for r in db.execute(f'SELECT DISTINCT t.id,t.title,t.available,t.archived FROM threads t JOIN segments s ON s.thread_id=t.id WHERE s.project_id IN ({marks})',scope)]
        return p

    def projects(self):
        with self.connection() as db:
            items=[self._project(db,r) for r in db.execute('SELECT * FROM projects WHERE merged_into IS NULL ORDER BY updated_at DESC,name').fetchall()]
            return [p for p in items if p['manual'] or p['segment_count']>0]

    def project(self,entity_id):
        with self.connection() as db:
            entity_id=self._resolve(db,'project',entity_id)
            return self._project(db,db.execute('SELECT * FROM projects WHERE id=?',(entity_id,)).fetchone(),True)

    def _topic(self,db,row):
        t=dict(row);t['manual']=bool(json.loads(t.pop('manual_fields')));t.pop('merged_into',None)
        t['segment_count']=db.execute('SELECT count(*) FROM segment_topics WHERE topic_id=?',(t['id'],)).fetchone()[0]
        t['project_count']=db.execute('SELECT count(DISTINCT coalesce(p.parent_id,p.id)) FROM segments s JOIN projects p ON p.id=s.project_id JOIN segment_topics st ON st.segment_id=s.id WHERE st.topic_id=?',(t['id'],)).fetchone()[0]
        return t

    def topics(self):
        with self.connection() as db:
            items=[self._topic(db,r) for r in db.execute('SELECT * FROM topics WHERE merged_into IS NULL ORDER BY name').fetchall()]
            return [t for t in items if t['manual'] or t['segment_count']>0]

    def topic(self,entity_id):
        with self.connection() as db:
            entity_id=self._resolve(db,'topic',entity_id)
            t=self._topic(db,db.execute('SELECT * FROM topics WHERE id=?',(entity_id,)).fetchone())
            t['segments']=[self._segment(db,r) for r in db.execute('SELECT s.* FROM segments s JOIN segment_topics st ON st.segment_id=s.id WHERE st.topic_id=? ORDER BY s.updated_at DESC',(entity_id,)).fetchall()]
            t['projects']=[self._project(db,r) for r in db.execute('SELECT DISTINCT root.* FROM projects root JOIN projects p ON coalesce(p.parent_id,p.id)=root.id JOIN segments s ON s.project_id=p.id JOIN segment_topics st ON st.segment_id=s.id WHERE st.topic_id=?',(entity_id,)).fetchall()]
            return t

    def threads(self,q='',limit=50,offset=0):
        limit=max(1,min(limit,200));offset=max(0,offset)
        with self.connection() as db:
            where='technical=0 AND (?="" OR instr(lower(title),lower(?))>0)'
            total=db.execute('SELECT count(*) FROM threads WHERE '+where,(q,q)).fetchone()[0]
            items=[]
            for row in db.execute('SELECT * FROM threads WHERE '+where+' ORDER BY updated_at DESC LIMIT ? OFFSET ?',(q,q,limit,offset)).fetchall():
                t=dict(row);t['message_count']=db.execute('SELECT count(*) FROM messages WHERE thread_id=?',(t['id'],)).fetchone()[0]
                states=[r[0] for r in db.execute('SELECT status FROM jobs WHERE thread_id=?',(t['id'],))]
                t['analysis_status']=next((s for s in ['running','failed','pending'] if s in states),'done' if states else 'empty')
                items.append(t)
            return {'items':items,'total':total}

    @staticmethod
    def _display_thread_title(thread):
        title=thread.get('title','')
        injected_prefixes=('<recommended_plugins>','# AGENTS.md instructions','<environment_context>','<skills_instructions>','<INSTRUCTIONS>')
        if thread.get('technical') and (not title.strip() or title=='未命名对话' or title.lstrip().startswith(injected_prefixes)):
            return '技术子对话 · '+thread['id'][:8]
        return title

    def thread(self,thread_id,limit=100,offset=0,focus=None):
        limit=max(1,min(limit,300));offset=max(0,offset)
        with self.connection() as db:
            row=db.execute('SELECT * FROM threads WHERE id=?',(thread_id,)).fetchone()
            if row is None:raise KeyError(thread_id)
            t=dict(row)
            t['title']=self._display_thread_title(t)
            if focus:
                found=db.execute('SELECT line_no FROM messages WHERE id=? AND thread_id=?',(focus,thread_id)).fetchone()
                if found:offset=max(0,db.execute('SELECT count(*) FROM messages WHERE thread_id=? AND line_no<?',(thread_id,found[0])).fetchone()[0]-limit//3)
            t['messages']=[dict(r) for r in db.execute('SELECT id,role,text,timestamp,line_no,turn_id,kind FROM messages WHERE thread_id=? ORDER BY line_no,id LIMIT ? OFFSET ?',(thread_id,limit,offset))]
            t['total']=db.execute('SELECT count(*) FROM messages WHERE thread_id=?',(thread_id,)).fetchone()[0];t['offset']=offset
            t['related_threads']=[dict(r) for r in db.execute('SELECT id,title,source_kind,technical,available FROM threads WHERE parent_id=? OR id=?',(thread_id,t['parent_id']))]
            for related in t['related_threads']:related['title']=self._display_thread_title(related)
            return t

    def search(self,q,topic_id=None,project_id=None,since=None,until=None,limit=50,offset=0):
        q=q.strip();limit=max(1,min(limit,200));offset=max(0,offset)
        if not q:return {'items':[],'total':0}
        params=[];where=[];join=''
        if len(q)>=3:
            join=' JOIN search_fts ON search_fts.rowid=d.rowid';where.append('search_fts MATCH ?');params.append('"'+q.replace('"','""')+'"')
        else:
            where.append('(instr(lower(d.text),lower(?))>0 OR instr(lower(d.title),lower(?))>0)');params.extend([q,q])
        where.append('(d.thread_id IS NULL OR EXISTS(SELECT 1 FROM threads t WHERE t.id=d.thread_id AND t.technical=0))')
        segment_link="(d.kind='segment' AND s.id=d.entity_id OR d.kind='event' AND EXISTS(SELECT 1 FROM events e WHERE e.id=d.entity_id AND e.segment_id=s.id) OR d.kind='message' AND EXISTS(SELECT 1 FROM segment_messages sm WHERE sm.segment_id=s.id AND sm.message_id=d.message_id))"
        if project_id:
            scope="(SELECT id FROM projects WHERE merged_into IS NULL AND (id=? OR parent_id=?))"
            where.append("(d.kind='project' AND d.entity_id IN "+scope+" OR EXISTS(SELECT 1 FROM segments s WHERE s.project_id IN "+scope+" AND "+segment_link+") OR d.kind='topic' AND EXISTS(SELECT 1 FROM segment_topics st JOIN segments s ON s.id=st.segment_id WHERE s.project_id IN "+scope+" AND st.topic_id=d.entity_id))")
            params.extend([project_id]*6)
        if topic_id:
            where.append("(d.kind='topic' AND d.entity_id=? OR EXISTS(SELECT 1 FROM segment_topics st JOIN segments s ON s.id=st.segment_id WHERE st.topic_id=? AND ("+segment_link+" OR d.kind='project' AND d.entity_id IN (SELECT id FROM projects WHERE id=s.project_id OR id=(SELECT parent_id FROM projects WHERE id=s.project_id)))))")
            params.extend([topic_id]*2)
        if since:where.append('d.timestamp>=?');params.append(since)
        if until:where.append('d.timestamp<=?');params.append(until+'T23:59:59.999999Z' if len(until)==10 else until)
        sql=' FROM search_docs d'+join+' WHERE '+' AND '.join('('+s+')' for s in where)
        with self.connection() as db:
            total=db.execute('SELECT count(*)'+sql,params).fetchone()[0]
            rows=db.execute('SELECT d.*'+sql+' ORDER BY d.timestamp DESC,d.id LIMIT ? OFFSET ?',params+[limit,offset]).fetchall()
            items=[]
            for r in rows:
                x=dict(r)
                # Keep the full original in thread view, a bounded match context here.
                pos=x['text'].lower().find(q.lower());start=max(0,pos-90)
                x['text']=('…' if start else '')+x['text'][start:start+450]+('…' if len(x['text'])>start+450 else '')
                if x['kind'] in ('segment','event'):
                    sid=x['entity_id'] if x['kind']=='segment' else db.execute('SELECT segment_id FROM events WHERE id=?',(x['entity_id'],)).fetchone()[0]
                    m=db.execute('SELECT message_id FROM segment_messages WHERE segment_id=? LIMIT 1',(sid,)).fetchone()
                    if m:x['message_id']=m[0]
                items.append(x)
            return {'items':items,'total':total}

    def _refresh_derived_docs(self,db):
        db.execute("DELETE FROM search_docs WHERE kind<>'message'")
        for p in db.execute('SELECT * FROM projects WHERE merged_into IS NULL').fetchall():
            self._doc(db,'project',p['id'],p['name'],'\n'.join([p['goal'],p['summary'],p['stage']]),p['updated_at'],project_id=p['id'])
        for t in db.execute('SELECT * FROM topics WHERE merged_into IS NULL').fetchall():self._doc(db,'topic',t['id'],t['name'],t['name'],t['updated_at'])
        for s in db.execute('SELECT s.*,t.title FROM segments s JOIN threads t ON t.id=s.thread_id').fetchall():
            self._doc(db,'segment',s['id'],s['title'],s['summary']+'\n'+'\n'.join(json.loads(s['decisions'])+json.loads(s['open_items'])),s['updated_at'],s['thread_id'],project_id=s['project_id'])
        for e in db.execute('SELECT e.*,s.thread_id,s.project_id,t.title FROM events e JOIN segments s ON s.id=e.segment_id JOIN threads t ON t.id=s.thread_id').fetchall():
            refs=json.loads(e['message_ids'])
            self._doc(db,'event',e['id'],e['title'],e['text'],e['timestamp'],e['thread_id'],refs[0] if refs else None,e['project_id'])
        db.execute("INSERT INTO settings VALUES('analysis_updated_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(now(),))

    def _validate_parent(self,db,project_id,parent_id):
        if parent_id is None:return None
        parent_id=self._resolve(db,'project',parent_id)
        if project_id==parent_id:raise ValueError('项目不能归入自身')
        parent=db.execute('SELECT parent_id FROM projects WHERE id=?',(parent_id,)).fetchone()
        if parent['parent_id'] is not None:raise ValueError('仅支持主项目与工作分项两层；父项目必须是主项目')
        if project_id and db.execute('SELECT 1 FROM projects WHERE parent_id=? AND merged_into IS NULL',(project_id,)).fetchone():
            raise ValueError('已有工作分项的主项目不能再成为分项')
        return parent_id

    def _set_parent(self,db,project_id,parent_id):
        parent_id=self._validate_parent(db,project_id,parent_id)
        fields=json.loads(db.execute('SELECT manual_fields FROM projects WHERE id=?',(project_id,)).fetchone()[0])
        db.execute('UPDATE projects SET parent_id=?,manual_fields=?,updated_at=? WHERE id=?',(parent_id,encoded(sorted(set(fields)|{'parent_id'})),now(),project_id))

    def group_projects(self,name,goal,project_ids):
        if not isinstance(name,str) or not name.strip() or not isinstance(goal,str) or not goal.strip() or not project_ids:raise ValueError('主项目名称、目标和待归类项目不能为空')
        root_id=uid()
        with self.connection() as db:
            ids=list(dict.fromkeys(self._resolve(db,'project',p) for p in project_ids))
            for child in ids:
                if db.execute('SELECT 1 FROM projects WHERE parent_id=? AND merged_into IS NULL',(child,)).fetchone():raise ValueError('请先处理已有工作分项；不能将主项目嵌套到另一主项目')
            db.execute('INSERT INTO projects(id,name,goal,updated_at,manual_fields) VALUES(?,?,?,?,?)',(root_id,name.strip(),goal.strip(),now(),encoded(['name','goal','parent_id'])))
            db.execute('INSERT OR REPLACE INTO aliases VALUES(?,?,?)',('project',name.strip().casefold(),root_id))
            for child in ids:self._set_parent(db,child,root_id)
            self._recompute_projects(db);self._refresh_derived_docs(db)
        return self.project(root_id)

    def set_project_parent(self,project_id,parent_id):
        with self.connection() as db:
            project_id=self._resolve(db,'project',project_id)
            self._set_parent(db,project_id,parent_id)
            self._recompute_projects(db);self._refresh_derived_docs(db)
        return self.project(project_id)

    def edit_entity(self,kind,entity_id,changes):
        if kind not in ('project','topic'):raise ValueError('Unknown entity type')
        allowed={'name','goal','summary','stage'} if kind=='project' else {'name'}
        if not changes or set(changes)-allowed:raise ValueError('Unsupported edit fields')
        if any(not isinstance(v,str) for v in changes.values()):raise ValueError('Fields must be text')
        if 'name' in changes and not changes['name'].strip():raise ValueError('Name is required')
        table='projects' if kind=='project' else 'topics'
        with self.connection() as db:
            entity_id=self._resolve(db,kind,entity_id)
            row=db.execute(f'SELECT * FROM {table} WHERE id=?',(entity_id,)).fetchone()
            fields=set(json.loads(row['manual_fields']))|set(changes)
            if 'name' in changes:
                db.execute('INSERT OR REPLACE INTO aliases VALUES(?,?,?)',(kind,row['name'].casefold().strip(),entity_id))
                db.execute('INSERT OR REPLACE INTO aliases VALUES(?,?,?)',(kind,changes['name'].casefold().strip(),entity_id))
            db.execute(f'UPDATE {table} SET '+','.join(k+'=?' for k in changes)+',manual_fields=?,updated_at=? WHERE id=?',list(changes.values())+[encoded(sorted(fields)),now(),entity_id])
            self._recompute_projects(db);self._refresh_derived_docs(db)
        return self.project(entity_id) if kind=='project' else self.topic(entity_id)

    def _save_override(self,db,segment_id):
        s=db.execute('SELECT * FROM segments WHERE id=?',(segment_id,)).fetchone()
        topics=[r[0] for r in db.execute('SELECT topic_id FROM segment_topics WHERE segment_id=? ORDER BY topic_id',(segment_id,))]
        refs=sorted(set(r[0] for r in db.execute('SELECT reference_id FROM segment_ranges WHERE segment_id=?',(segment_id,))))
        if not refs:refs=sorted(r[0] for r in db.execute('SELECT message_id FROM segment_messages WHERE segment_id=?',(segment_id,)))
        scope=encoded([s['machine_project_id'],json.loads(s['machine_topic_ids']),refs])
        db.execute('INSERT OR REPLACE INTO assignment_overrides VALUES(?,?,?,?,?,?,?)',(digest(scope),encoded(refs),s['machine_project_id'],s['machine_topic_ids'],s['project_id'],encoded(topics),now()))
        db.execute('UPDATE segments SET manual=1 WHERE id=?',(segment_id,))

    def _assignment(self,db,ref,machine_project,machine_topics,unambiguous=False):
        candidates=[]
        for o in db.execute('SELECT * FROM assignment_overrides ORDER BY updated_at DESC').fetchall():
            if ref not in json.loads(o['anchor_refs']):continue
            candidates.append(o)
            original_project=self._resolve(db,'project',o['machine_project_id']) if o['machine_project_id'] else None
            original_topics=sorted(set(self._resolve(db,'topic',t) for t in json.loads(o['machine_topic_ids'])))
            if original_project==machine_project and original_topics==sorted(machine_topics):return o
        if unambiguous and len(candidates)==1:return candidates[0]
        return None

    def merge_entities(self,kind,source_ids,target_id):
        if kind not in ('project','topic'):raise ValueError('Unknown entity type')
        table='projects' if kind=='project' else 'topics'
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            target_id=self._resolve(db,kind,target_id)
            if kind=='project':
                members={target_id}|{self._resolve(db,kind,s) for s in source_ids}
                parents={db.execute('SELECT parent_id FROM projects WHERE id=?',(p,)).fetchone()[0] for p in members}
                if len(parents)>1:raise ValueError('只能合并同一主项目下的工作分项，或同层独立项目')
            for source in source_ids:
                source=self._resolve(db,kind,source)
                if source==target_id:continue
                row=db.execute(f'SELECT * FROM {table} WHERE id=?',(source,)).fetchone()
                db.execute(f'UPDATE {table} SET merged_into=? WHERE id=?',(target_id,source))
                db.execute('INSERT OR REPLACE INTO aliases VALUES(?,?,?)',(kind,row['name'].casefold().strip(),target_id))
                db.execute('UPDATE aliases SET entity_id=? WHERE kind=? AND entity_id=?',(target_id,kind,source))
                if kind=='project':
                    for child in db.execute('SELECT id,manual_fields FROM projects WHERE parent_id=? AND merged_into IS NULL',(source,)).fetchall():
                        fields=sorted(set(json.loads(child['manual_fields']))|{'parent_id'})
                        db.execute('UPDATE projects SET parent_id=?,manual_fields=?,updated_at=? WHERE id=?',(target_id,encoded(fields),now(),child['id']))
                    db.execute('UPDATE segments SET project_id=?,manual=1 WHERE project_id=?',(target_id,source))
                    db.execute('UPDATE overrides SET project_id=? WHERE project_id=?',(target_id,source))
                    db.execute('UPDATE assignment_overrides SET project_id=? WHERE project_id=?',(target_id,source))
                else:
                    db.execute('INSERT OR IGNORE INTO segment_topics SELECT segment_id,? FROM segment_topics WHERE topic_id=?',(target_id,source))
                    db.execute('DELETE FROM segment_topics WHERE topic_id=?',(source,))
                    for o in db.execute('SELECT * FROM assignment_overrides').fetchall():
                        ids=json.loads(o['topic_ids'])
                        if source in ids:db.execute('UPDATE assignment_overrides SET topic_ids=? WHERE id=?',(encoded(sorted(set(target_id if x==source else x for x in ids))),o['id']))
                    for o in db.execute('SELECT * FROM overrides').fetchall():
                        ids=json.loads(o['topic_ids'])
                        if source in ids:db.execute('UPDATE overrides SET topic_ids=? WHERE message_id=?',(encoded(sorted(set(target_id if x==source else x for x in ids))),o['message_id']))
            prior_fields=json.loads(db.execute(f'SELECT manual_fields FROM {table} WHERE id=?',(target_id,)).fetchone()[0])
            db.execute(f"UPDATE {table} SET manual_fields=?,updated_at=? WHERE id=?",(encoded(sorted(set(prior_fields)|{'name'})),now(),target_id))
            self._recompute_projects(db);self._refresh_derived_docs(db)
        return self.project(target_id) if kind=='project' else self.topic(target_id)

    def split_entity(self,kind,entity_id,name,segment_ids):
        if kind not in ('project','topic') or not name.strip() or not segment_ids:raise ValueError('Name and selected segments are required')
        new_id=uid()
        with self.connection() as db:
            entity_id=self._resolve(db,kind,entity_id)
            for sid in segment_ids:
                query='SELECT 1 FROM segments WHERE id=? AND project_id=?' if kind=='project' else 'SELECT 1 FROM segment_topics WHERE segment_id=? AND topic_id=?'
                if not db.execute(query,(sid,entity_id)).fetchone():raise ValueError('Segment is not a member of this entity')
            if kind=='project':
                old=db.execute('SELECT goal,parent_id FROM projects WHERE id=?',(entity_id,)).fetchone()
                db.execute('INSERT INTO projects(id,name,goal,updated_at,manual_fields,parent_id) VALUES(?,?,?,?,?,?)',(new_id,name.strip(),old['goal'],now(),encoded(['name','goal','parent_id']),old['parent_id']))
            else:db.execute('INSERT INTO topics(id,name,updated_at,manual_fields) VALUES(?,?,?,?)',(new_id,name.strip(),now(),encoded(['name'])))
            for sid in segment_ids:
                if kind=='project':db.execute('UPDATE segments SET project_id=? WHERE id=?',(new_id,sid))
                else:
                    db.execute('DELETE FROM segment_topics WHERE segment_id=? AND topic_id=?',(sid,entity_id))
                    db.execute('INSERT INTO segment_topics VALUES(?,?)',(sid,new_id))
                self._save_override(db,sid)
            self._recompute_projects(db)
            self._refresh_derived_docs(db)
        return self.project(new_id) if kind=='project' else self.topic(new_id)

    def edit_segment(self,segment_id,project_id,topic_ids):
        with self.connection() as db:
            if not db.execute('SELECT 1 FROM segments WHERE id=?',(segment_id,)).fetchone():raise KeyError(segment_id)
            project_id=self._resolve(db,'project',project_id) if project_id else None
            topics=set(self._resolve(db,'topic',x) for x in topic_ids)
            db.execute('UPDATE segments SET project_id=? WHERE id=?',(project_id,segment_id))
            db.execute('DELETE FROM segment_topics WHERE segment_id=?',(segment_id,))
            db.executemany('INSERT INTO segment_topics VALUES(?,?)',[(segment_id,t) for t in topics])
            self._save_override(db,segment_id);self._recompute_projects(db);self._refresh_derived_docs(db)
            return self._segment(db,db.execute('SELECT * FROM segments WHERE id=?',(segment_id,)).fetchone())

    def catalog(self):
        with self.connection() as db:
            projects=[]
            for row in db.execute('SELECT * FROM projects WHERE merged_into IS NULL ORDER BY updated_at DESC LIMIT 250').fetchall():
                item=self._project(db,row)
                projects.append({key:item[key] for key in ('id','name','goal','summary','stage','updated_at','source_updated_at','open_items','parent_id','parent_name','child_count')})
            return {'projects':projects,'topics':[dict(r) for r in db.execute('SELECT id,name FROM topics WHERE merged_into IS NULL ORDER BY updated_at DESC LIMIT 250')]}

    def claim_job(self, exclude_thread_ids=()):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            excluded = tuple(exclude_thread_ids)
            exclude_sql = ' AND j.thread_id NOT IN ('+','.join('?' for _ in excluded)+')' if excluded else ''
            job=db.execute("SELECT j.*,t.title AS thread_title FROM jobs j JOIN threads t ON t.id=j.thread_id WHERE j.status='pending' AND j.next_retry_at<=? AND t.technical=0 AND NOT EXISTS (SELECT 1 FROM jobs active WHERE active.thread_id=j.thread_id AND active.status='running')"+exclude_sql+" ORDER BY t.updated_at DESC,j.source_time DESC,j.source_order DESC,j.id LIMIT 1",(time.time(),*excluded)).fetchone()
            if not job:return None
            db.execute("UPDATE jobs SET status='running',attempts=attempts+1,updated_at=?,error=NULL WHERE id=?",(now(),job['id']))
            result=dict(job);result['attempts']+=1;result['messages']=[]
            for r in db.execute('SELECT jm.id,jm.start_char,jm.end_char,m.role,m.text,m.timestamp,m.kind FROM job_messages jm JOIN messages m ON m.id=jm.message_id WHERE jm.job_id=? ORDER BY jm.position',(job['id'],)):
                m=dict(r);m['text']=m['text'][m.pop('start_char'):m.pop('end_char')];result['messages'].append(m)
            first=db.execute('SELECT min(m.line_no) FROM job_messages jm JOIN messages m ON m.id=jm.message_id WHERE jm.job_id=?',(job['id'],)).fetchone()[0]
            context=[]
            from .ingest import _human_text
            def meaningful_context(query,params):
                for row in db.execute(query,params):
                    item=dict(row)
                    if item['role']=='user':item['text']=_human_text(item['text'])
                    if item['text'].strip():return item
                return None
            initial=meaningful_context("SELECT id,role,text,timestamp,kind FROM messages WHERE thread_id=? AND role='user' AND kind='message' ORDER BY line_no",(job['thread_id'],))
            recent=meaningful_context("SELECT id,role,text,timestamp,kind FROM messages WHERE thread_id=? AND role='user' AND kind='message' AND line_no<? ORDER BY line_no DESC",(job['thread_id'],first or 0))
            assistant=meaningful_context("SELECT id,role,text,timestamp,kind FROM messages WHERE thread_id=? AND role='assistant' AND kind='message' AND line_no<? ORDER BY line_no DESC",(job['thread_id'],first or 0))
            covered_sources={r[0] for r in db.execute('SELECT message_id FROM job_messages WHERE job_id=?',(job['id'],))}
            for row in (initial,recent,assistant):
                if row and row['id'] not in covered_sources and row['id'] not in {c['id'] for c in context}:
                    item=dict(row);item['truncated']=len(item['text'])>6000;item['text']=item['text'][:6000];context.append(item)
            result['context']=context
            result['tool_evidence']=self._tool_evidence(db,job['id'])
            truncated=sum(bool(e['truncated']) for e in result['tool_evidence'])
            retained=sum(sum(r['end_char']-r['start_char'] for r in e['source_ranges']) for e in result['tool_evidence'])
            result['tool_evidence_omitted']=job['evidence_total']-len(result['tool_evidence'])
            result['evidence_sampling']={'total_outputs':job['evidence_total'],'sampled_outputs':len(result['tool_evidence']),'omitted_outputs':result['tool_evidence_omitted'],'truncated_outputs':truncated,'omitted_characters':max(0,job['evidence_characters']-retained)}
            return result

    @staticmethod
    def _tool_evidence(db,job_id):
        result=[]
        for row in db.execute('SELECT m.id,m.role,m.text,m.timestamp,m.kind FROM job_evidence je JOIN messages m ON m.id=je.message_id WHERE je.job_id=? ORDER BY je.position',(job_id,)):
            e=dict(row);length=len(e['text']);e['truncated']=length>1450
            if e['truncated']:
                e['source_ranges']=[{'start_char':0,'end_char':750},{'start_char':length-700,'end_char':length}]
                e['text']=e['text'][:750]+'\n[工具输出中段省略，原文可检索]\n'+e['text'][-700:]
            else:e['source_ranges']=[{'start_char':0,'end_char':length}]
            result.append(e)
        return result

    def _entity(self,db,kind,name,goal='',entity_id=None,parent_id=None):
        table='projects' if kind=='project' else 'topics'
        if entity_id:
            try:return self._resolve(db,kind,entity_id)
            except KeyError:raise ValueError('Model referenced an unknown entity')
        name=name.strip()
        if not name:return None
        requested_parent=self._validate_parent(db,None,parent_id) if kind=='project' and parent_id else None
        found=db.execute('SELECT entity_id FROM aliases WHERE kind=? AND name=?',(kind,name.casefold())).fetchone()
        if kind=='topic':
            if found:return self._resolve(db,kind,found[0])
            found=db.execute('SELECT id FROM topics WHERE lower(name)=lower(?) AND merged_into IS NULL',(name,)).fetchone()
            if found:return found[0]
        else:
            normalized_goal=lambda value:' '.join(unicodedata.normalize('NFKC',value).casefold().split())
            incoming_goal=normalized_goal(goal)
            if found:
                target_id=self._resolve(db,kind,found[0])
                target=db.execute('SELECT goal,manual_fields,parent_id FROM projects WHERE id=?',(target_id,)).fetchone()
                # Rename and merge explicitly preserve identity under prior names.
                # An automatically recorded name alias alone is not a shared goal.
                if 'name' in json.loads(target['manual_fields']) or (incoming_goal and normalized_goal(target['goal'])==incoming_goal and (not parent_id or target['parent_id']==requested_parent)):return target_id
            for candidate in db.execute('SELECT id,name,goal,parent_id FROM projects WHERE merged_into IS NULL'):
                if candidate['name'].strip().casefold()==name.casefold() and incoming_goal and normalized_goal(candidate['goal'])==incoming_goal and (not parent_id or candidate['parent_id']==requested_parent):return candidate['id']
        result=uid()
        if kind=='project':
            db.execute('INSERT INTO projects(id,name,goal,updated_at,parent_id) VALUES(?,?,?,?,?)',(result,name,goal,now(),requested_parent))
        else:db.execute('INSERT INTO topics(id,name,updated_at) VALUES(?,?,?)',(result,name,now()))
        db.execute('INSERT OR REPLACE INTO aliases VALUES(?,?,?)',(kind,name.casefold(),result))
        return result

    def _recompute_projects(self,db):
        for p in db.execute('SELECT * FROM projects WHERE merged_into IS NULL ORDER BY (parent_id IS NULL),id').fetchall():
            if db.execute('SELECT 1 FROM projects WHERE parent_id=? AND merged_into IS NULL',(p['id'],)).fetchone():
                aggregate=self._project(db,p)
                fields=json.loads(p['manual_fields'])
                changes={key:aggregate[key] for key in ('summary','stage') if key not in fields and p[key]!=aggregate[key]}
                if changes:
                    changes['updated_at']=now()
                    db.execute('UPDATE projects SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',list(changes.values())+[p['id']])
                continue
            latest=db.execute('SELECT summary,stage,updated_at FROM segments WHERE project_id=? ORDER BY updated_at DESC,id DESC LIMIT 1',(p['id'],)).fetchone()
            manual=json.loads(p['manual_fields'])
            if latest:
                changes={k:latest[k] for k in ('summary','stage') if k not in manual}
                changes['updated_at']=now() if any(p[k]!=v for k,v in changes.items()) else p['updated_at']
                db.execute('UPDATE projects SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',list(changes.values())+[p['id']])
            else:
                # A manual name does not make the former machine summary manual.
                changes={k:v for k,v in [('summary',''),('stage','未知')] if k not in manual and p[k]!=v}
                if changes:
                    changes['updated_at']=now()
                    db.execute('UPDATE projects SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',list(changes.values())+[p['id']])

    def apply_analysis(self,job_id,result):
        if not isinstance(result,dict) or not isinstance(result.get('segments'),list):raise ValueError('Invalid analysis shape')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            job=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not job:raise KeyError(job_id)
            if job['status'] not in ('running','pending','failed','done'):raise ValueError('Job no longer current')
            refs={r['id']:dict(r) for r in db.execute('SELECT jm.*,m.timestamp FROM job_messages jm JOIN messages m ON m.id=jm.message_id WHERE jm.job_id=?',(job_id,))}
            dialogue_refs=set(refs)
            evidence=self._tool_evidence(db,job_id)
            evidence_ranges={e['id']:e['source_ranges'] for e in evidence}
            for e in evidence:refs[e['id']]={'message_id':e['id'],'timestamp':e['timestamp'],'start_char':0,'end_char':len(e['text'])}
            covered=set()
            valid_kinds={'discussed','decided','reported_done','verified','blocked','retracted'}
            for s in result['segments']:
                if not isinstance(s.get('message_ids'),list) or not s['message_ids'] or not set(s['message_ids'])<=dialogue_refs:raise ValueError('Invalid segment provenance')
                if not set(s['message_ids'])&dialogue_refs:raise ValueError('A segment must contain dialogue, not only tool output')
                covered.update(set(s['message_ids'])&dialogue_refs)
                if not isinstance(s.get('summary'),str) or not isinstance(s.get('stage'),str):raise ValueError('Invalid segment description')
                if not isinstance(s.get('project'),dict) or any(not isinstance(s['project'].get(k),str) for k in ('id','name','goal')):raise ValueError('Invalid project shape')
                if 'parent_id' in s['project'] and not isinstance(s['project']['parent_id'],str):raise ValueError('Invalid project parent')
                for field in ('topic_names','open_items','decisions'):
                    if not isinstance(s.get(field),list) or any(not isinstance(x,str) for x in s[field]):raise ValueError('Invalid text list')
                if not isinstance(s.get('events'),list):raise ValueError('Invalid events')
                for e in s['events']:
                    if e.get('kind') not in valid_kinds or not isinstance(e.get('text'),str) or not e.get('message_ids') or not set(e['message_ids'])<=(set(s['message_ids'])|set(evidence_ranges)):raise ValueError('Invalid event provenance')
            if covered!=dialogue_refs:raise ValueError('Analysis did not cover every input message')
            reference_segments={ref:sum(ref in s['message_ids'] for s in result['segments']) for ref in dialogue_refs}
            db.execute('DELETE FROM segments WHERE job_id=?',(job_id,))
            for i,s in enumerate(result['segments']):
                machine_project=self._entity(db,'project',s['project']['name'],s['project']['goal'],s['project']['id'] or None,s['project'].get('parent_id') or None)
                machine_topics=[self._entity(db,'topic',n) for n in dict.fromkeys(s['topic_names']) if n.strip()]
                # Split a regenerated segment at saved manual-assignment boundaries.
                groups={}
                for ref in s['message_ids']:
                    mid=refs[ref]['message_id'];o=self._assignment(db,ref,machine_project,machine_topics,reference_segments.get(ref)==1)
                    if o:
                        pid=self._resolve(db,'project',o['project_id']) if o['project_id'] else None
                        tids=sorted(set(self._resolve(db,'topic',t) for t in json.loads(o['topic_ids'])))
                    else:pid=machine_project;tids=sorted(machine_topics)
                    key=(pid,tuple(tids),bool(o));groups.setdefault(key,[]).append(ref)
                for group_index,((pid,tids,manual),group) in enumerate(groups.items()):
                    mids=list(dict.fromkeys(refs[r]['message_id'] for r in group));sid=digest(job_id+':'+str(i)+':'+str(group_index))[:32]
                    stamp=max((refs[r]['timestamp'] or '' for r in group if r in dialogue_refs),default='') or job['created_at']
                    db.execute('INSERT INTO segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(sid,job_id,job['thread_id'],s['summary'],s['stage'],pid,stamp,encoded(s['open_items']),encoded(s['decisions']),int(manual),machine_project,encoded(sorted(machine_topics))))
                    db.executemany('INSERT INTO segment_messages VALUES(?,?)',[(sid,m) for m in mids])
                    for ref in group:
                        ranges=evidence_ranges.get(ref,[{'start_char':refs[ref]['start_char'],'end_char':refs[ref]['end_char']}])
                        db.executemany('INSERT INTO segment_ranges VALUES(?,?,?,?,?)',[(sid,ref,refs[ref]['message_id'],r['start_char'],r['end_char']) for r in ranges])
                    db.executemany('INSERT INTO segment_topics VALUES(?,?)',[(sid,t) for t in tids])
                    for e in s['events']:
                        actual=[refs[r]['message_id'] for r in e['message_ids'] if r in group or r in evidence_ranges]
                        if not actual:continue
                        timestamp=max((refs[r]['timestamp'] or '' for r in e['message_ids'] if r in group or r in evidence_ranges),default='') or stamp
                        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(uid(),sid,e['kind'],e['text'],encoded(list(dict.fromkeys(actual))),timestamp))
            db.execute("UPDATE jobs SET status='done',updated_at=?,error=NULL WHERE id=?",(now(),job_id))
            self._recompute_projects(db);self._refresh_derived_docs(db)

    def fail_job(self,job_id,error):
        with self.connection() as db:db.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=?",(str(error)[:4000],now(),job_id))

    def retry_failed(self):
        with self.connection() as db:return db.execute("UPDATE jobs SET status='pending',attempts=0,next_retry_at=0,error=NULL WHERE status='failed'").rowcount

    def recover_jobs(self, exclude_ids=()):
        excluded = tuple(exclude_ids)
        clause = ' AND id NOT IN ('+','.join('?' for _ in excluded)+')' if excluded else ''
        with self.connection() as db:
            return db.execute("UPDATE jobs SET status='pending' WHERE status='running'"+clause,excluded).rowcount


    def queue_settings(self):
        with self.connection() as db:
            row=db.execute("SELECT value FROM settings WHERE key='queue_control'").fetchone()
            return json.loads(row[0]) if row else {}

    def save_queue_settings(self, values):
        with self.connection() as db:
            db.execute("INSERT INTO settings(key,value) VALUES('queue_control',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(encoded(values),))

    def schedule_retry(self,job_id,error,delay):
        retry_at=time.time()+delay
        with self.connection() as db:
            db.execute("UPDATE jobs SET status='pending',error=?,next_retry_at=?,updated_at=? WHERE id=?",(str(error)[:4000],retry_at,now(),job_id))
        return retry_at
