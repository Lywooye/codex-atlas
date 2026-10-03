"""Incremental read-only import of Codex rollout files into the local index."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .store import digest, encoded, now, uid

CHUNK_CHARS = 20000
CHUNK_MESSAGES = 20
_INJECTED = re.compile(r'<(environment_context|external_codex_apps_open_page|codex_apps_client_time_context|system_reminder|app-context|permissions instructions)\b[^>]*>.*?</\1>', re.S)
_PURE_INJECTED = re.compile(r'\s*<(recommended_plugins|skills_instructions|codex_apps_open_page_instructions|codex_apps_client_time_context|environment_context|external_codex_apps_open_page|app-context)\b[^>]*>.*?</\1>\s*', re.S)
_AMBIENT_BROWSER = re.compile(r'''<in-app-browser-context\b(?=[^>]*\bsource=["']ambient-ui-state["'])[^>]*>.*?</in-app-browser-context>\s*''', re.S)
PARSER_VERSION = 'human-v2'


def _text(content):
    if isinstance(content,str):return content
    if isinstance(content,list):
        parts=[]
        for part in content:
            if isinstance(part,str):parts.append(part)
            elif isinstance(part,dict):
                if part.get('type') in ('input_text','output_text','text') and isinstance(part.get('text'),str):parts.append(part['text'])
                elif part.get('type') in ('input_image','image','image_url'):parts.append('[图片附件]')
                elif part.get('type') in ('input_audio','audio'):parts.append('[音频附件]')
        return '\n'.join(parts)
    if isinstance(content,dict):
        if 'content' in content:return _text(content['content'])
        return encoded(content)
    return ''


def _human_text(text):
    # The desktop prepends the complete repository instructions as a synthetic
    # user item. Do not turn those instructions into the user's project work.
    # Some desktop versions concatenate plugin metadata and AGENTS instructions
    # into one synthetic user item. Test the remainder after removing complete
    # known metadata blocks; a trailing human question makes this a real message.
    cleaned,ambient_count=_AMBIENT_BROWSER.subn('',text)
    if ambient_count:cleaned=re.sub(r'^\s*## My request:\s*','',cleaned,count=1)
    # Normalize escaped newlines only for recognizing wholly injected material;
    # authored message content and code retain their original characters.
    inspection=cleaned.replace('\\r\\n','\n').replace('\\n','\n')
    remainder=_PURE_INJECTED.sub('',_INJECTED.sub('',inspection)).strip()
    if not remainder:return ''
    if re.fullmatch(r'# AGENTS\.md instructions(?:[ \t]+for[^\n]*)?\s*<INSTRUCTIONS>.*?</INSTRUCTIONS>\s*',remainder,re.S):return ''
    return _INJECTED.sub('',cleaned).strip()


def _source(meta):
    explicit=meta.get('thread_source')
    source=meta.get('source','cli')
    value=explicit if explicit is not None else source
    if isinstance(value,dict):
        label=next(iter(value),'unknown')
    else:label=str(value or 'cli')
    technical = label.lower() not in ('user','cli','vscode','exec','appserver','app_server','desktop','unknown')
    # A precise human-thread flag wins over a broad runtime origin.
    if explicit=='user':technical=False
    parent=meta.get('parent_thread_id') or meta.get('parent_id') or meta.get('forked_from_id')
    if isinstance(source,dict):
        sub=source.get('subagent',{})
        if isinstance(sub,dict):
            spawn=sub.get('thread_spawn',{})
            if isinstance(spawn,dict):parent=parent or spawn.get('parent_thread_id')
    return label,int(technical),parent


def _metadata_title(data):
    if not isinstance(data,dict):return None
    return data.get('name') or data.get('title') or data.get('thread_name') or None


def _fingerprint(path,offset):
    with path.open('rb') as f:
        head=f.read(min(4096,offset))
        f.seek(max(0,offset-2048));tail=f.read(min(2048,offset))
    return hashlib.sha256(head).hexdigest(),hashlib.sha256(tail).hexdigest()


def _record(db,store,thread_id,path,line_no,payload,record_type,timestamp,turn_id):
    typ=payload.get('type');role=None;text='';kind='message';representation='response'
    if record_type=='response_item' and typ=='message':
        role=payload.get('role')
        if role not in ('user','assistant') or payload.get('channel')=='analysis':return False
        text=_text(payload.get('content',[]))
        if role=='user':text=_human_text(text)
    elif record_type=='response_item' and typ in ('function_call_output','custom_tool_call_output'):
        role='tool';kind='tool_output';text=_text(payload.get('output',''))
    elif record_type=='event_msg' and typ in ('user_message','agent_message'):
        role='user' if typ=='user_message' else 'assistant';representation='event'
        text=payload.get('message','')
        if not isinstance(text,str):text=_text(text)
        if role=='user':text=_human_text(text)
    elif record_type=='response_item' and typ=='agent_message':
        # Internal agent-to-agent communication is available as evidence, not a
        # separately categorized conversation or a user request.
        role='tool';kind='technical_message';text=_text(payload.get('content',[]))
    else:return False
    if not text.strip():return False
    original_id=payload.get('id') or payload.get('message_id')
    source_id=str(original_id or ('line:'+str(line_no)))
    message_id=thread_id+':'+source_id
    canonical=digest(str(original_id)+':'+text) if original_id else digest((turn_id or thread_id)+':'+role+':'+text)
    # Older logs mirror user/assistant prose in both event_msg and response_item.
    if kind=='message':
        duplicate=db.execute('SELECT id,representation FROM messages WHERE thread_id=? AND role=? AND text=? AND coalesce(turn_id,\'\')=? AND representation<>? LIMIT 1',
                             (thread_id,role,text,turn_id,representation)).fetchone()
        if duplicate:
            if representation=='event':return False
            db.execute('DELETE FROM search_docs WHERE id=?',('message:'+duplicate['id'],))
            db.execute('DELETE FROM messages WHERE id=?',(duplicate['id'],))
    old=db.execute('SELECT text FROM messages WHERE id=?',(message_id,)).fetchone()
    if old and old['text']==text:return False
    db.execute('''INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
    text=excluded.text,timestamp=excluded.timestamp,line_no=excluded.line_no,turn_id=excluded.turn_id,
    canonical_key=excluded.canonical_key,source_path=excluded.source_path''',
               (message_id,thread_id,role,text,timestamp,line_no,turn_id,kind,canonical,representation,str(path),source_id))
    thread=db.execute('SELECT title,technical FROM threads WHERE id=?',(thread_id,)).fetchone()
    if not thread['technical']:store._doc(db,'message',message_id,thread['title'],text,timestamp,thread_id,message_id)
    return True


def _build_chunks(db,store,thread_id):
    thread=db.execute('SELECT * FROM threads WHERE id=?',(thread_id,)).fetchone()
    if thread['technical']:
        db.execute('DELETE FROM jobs WHERE thread_id=?',(thread_id,));return
    messages=db.execute('''SELECT m.* FROM messages m WHERE m.thread_id=? AND m.kind='message' AND NOT EXISTS(
      SELECT 1 FROM messages other JOIN threads ot ON ot.id=other.thread_id WHERE other.canonical_key=m.canonical_key
      AND other.thread_id<>m.thread_id AND ot.technical=0 AND (ot.created_at<? OR (ot.created_at=? AND ot.id<?)))
      ORDER BY m.line_no,m.id''',(thread_id,thread['created_at'],thread['created_at'],thread_id)).fetchall()
    existing={r['source_hash']:dict(r) for r in db.execute('SELECT * FROM jobs WHERE thread_id=?',(thread_id,))}
    keep=set();frozen_ranges={}
    # Completed and in-flight analysis owns a stable source range. Appending a
    # turn must not erase its conclusions merely because the tail could grow.
    for source_hash,job in existing.items():
        if job['status'] not in ('done','running'):continue
        entries=db.execute('SELECT jm.*,m.text,m.line_no FROM job_messages jm JOIN messages m ON m.id=jm.message_id WHERE jm.job_id=? ORDER BY jm.position',(job['id'],)).fetchall()
        if not entries or any(r['start_char']<0 or r['end_char']>len(r['text']) for r in entries):continue
        original=[(r['id'],r['message_id'],r['start_char'],r['end_char'],digest(r['text'][r['start_char']:r['end_char']])) for r in entries]
        evidence=db.execute("SELECT id,text FROM messages WHERE thread_id=? AND kind='tool_output' AND line_no>=? AND line_no<=? ORDER BY line_no",(thread_id,min(r['line_no'] for r in entries),max(r['line_no'] for r in entries))).fetchall()
        current_hash=digest(encoded(original)+encoded([(e['id'],digest(e['text'])) for e in evidence]))
        if current_hash!=source_hash:continue
        keep.add(source_hash)
        for r in entries:frozen_ranges.setdefault(r['message_id'],[]).append((r['start_char'],r['end_char']))
    chunks=[];chunk=[];length=0
    for m in messages:
        cursor=0;uncovered=[]
        for start,end in sorted(frozen_ranges.get(m['id'],[])):
            if start>cursor:uncovered.append((cursor,start))
            cursor=max(cursor,end)
        if cursor<len(m['text']):uncovered.append((cursor,len(m['text'])))
        for gap_start,gap_end in uncovered:
            for start in range(gap_start,gap_end,CHUNK_CHARS):
                end=min(gap_end,start+CHUNK_CHARS)
                if chunk and (length+end-start>CHUNK_CHARS or len(chunk)>=CHUNK_MESSAGES):chunks.append(chunk);chunk=[];length=0
                reference=m['id'] if start==0 and end==len(m['text']) else m['id']+'@'+str(start)+':'+str(end)
                chunk.append((reference,m['id'],start,end,digest(m['text'][start:end])));length+=end-start
    if chunk:chunks.append(chunk)
    for chunk in chunks:
        line_numbers=[db.execute('SELECT line_no FROM messages WHERE id=?',(item[1],)).fetchone()[0] for item in chunk]
        evidence=db.execute("SELECT id,text FROM messages WHERE thread_id=? AND kind='tool_output' AND line_no>=? AND line_no<=? ORDER BY line_no",(thread_id,min(line_numbers),max(line_numbers))).fetchall()
        if len(evidence)>8:
            selected=[evidence[round(i*(len(evidence)-1)/7)] for i in range(8)]
        else:selected=evidence
        source_hash=digest(encoded(chunk)+encoded([(e['id'],digest(e['text'])) for e in evidence]));keep.add(source_hash)
        if source_hash in existing:continue
        jid=uid();timestamp=now()
        db.execute('INSERT INTO jobs(id,thread_id,source_hash,created_at,updated_at,evidence_total,evidence_characters,source_time,source_order) VALUES(?,?,?,?,?,?,?,?,?)',(jid,thread_id,source_hash,timestamp,timestamp,len(evidence),sum(len(e['text']) for e in evidence),max(db.execute('SELECT timestamp FROM messages WHERE id=?',(item[1],)).fetchone()[0] or '' for item in chunk),max(line_numbers)))
        db.executemany('INSERT INTO job_messages VALUES(?,?,?,?,?,?)',[(jid,item[0],item[1],item[2],item[3],n) for n,item in enumerate(chunk)])
        db.executemany('INSERT INTO job_evidence VALUES(?,?,?)',[(jid,item['id'],n) for n,item in enumerate(selected)])
    for h,j in existing.items():
        if h not in keep:db.execute('DELETE FROM jobs WHERE id=?',(j['id'],))


def sync(store,codex_home:Path,metadata:dict|None=None):
    codex_home=Path(codex_home);metadata=metadata or {}
    paths=[];source_dirs=0
    for directory in ('sessions','archived_sessions'):
        folder=codex_home/directory
        if folder.exists():source_dirs+=1;paths.extend(folder.rglob('*.jsonl'))
    paths=sorted(paths,key=lambda p:p.name)
    changed=set();seen=set();seen_threads=set();files_read=0
    with store.connection() as db:
        parser_version=db.execute("SELECT value FROM settings WHERE key='parser_format'").fetchone()
        force_reparse=not parser_version or parser_version[0]!=PARSER_VERSION
        version=db.execute("SELECT value FROM settings WHERE key='chunk_format'").fetchone()
        if not version or version[0]!='dialogue-v3':
            changed.update(r[0] for r in db.execute('SELECT id FROM threads'))
            db.execute('DELETE FROM jobs')
            db.execute("INSERT INTO settings VALUES('chunk_format','dialogue-v3') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    # Short transactions per file keep the UI responsive even on large imports.
    for path in paths:
        source_path=str(path);seen.add(source_path)
        try:
            stat=path.stat()
            with store.connection() as db:
                old=db.execute('SELECT * FROM files WHERE path=?',(source_path,)).fetchone()
                if old and not force_reparse and not old['error'] and old['size']==stat.st_size and old['mtime_ns']==stat.st_mtime_ns and old['available']:
                    if old['thread_id']:seen_threads.add(old['thread_id'])
                    continue
                with path.open('rb') as f:
                    header=f.readline()
                    try:first=json.loads(header)
                    except (ValueError,UnicodeDecodeError):raise ValueError('Session header is incomplete or malformed')
                    if first.get('type')!='session_meta':raise ValueError('Missing session_meta header')
                    meta=first.get('payload',{})
                    thread_id=meta.get('id') or meta.get('session_id')
                    if not isinstance(thread_id,str) or not thread_id:raise ValueError('Missing session identifier')
                    seen_threads.add(thread_id)
                    label,technical,parent=_source(meta)
                    stamp=meta.get('timestamp') or first.get('timestamp') or ''
                    current=metadata.get(thread_id,{})
                    if not isinstance(current,dict):current={}
                    title=_metadata_title(current)
                    prior=db.execute('SELECT * FROM threads WHERE id=?',(thread_id,)).fetchone()
                    archived=int('archived_sessions' in path.parts)
                    db.execute('''INSERT INTO threads(id,title,cwd,source_kind,technical,parent_id,archived,created_at,updated_at,title_source)
                    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET cwd=excluded.cwd,source_kind=excluded.source_kind,
                    technical=excluded.technical,parent_id=excluded.parent_id,archived=excluded.archived,available=1''',
                               (thread_id,title or '未命名对话',meta.get('cwd',''),label,technical,parent,archived,stamp,stamp,'official' if title else 'fallback'))
                    if title:db.execute("UPDATE threads SET title=?,title_source='official' WHERE id=?",(title,thread_id))
                    rebuild=False;offset=0;line_no=0;turn_id='';errors=0
                    if old:
                        prefix,tail=_fingerprint(path,min(old['offset'],stat.st_size))
                        rebuild=(force_reparse or old['thread_id']!=thread_id or stat.st_size<=old['offset'] or str(stat.st_ino)!=old['inode'] or prefix!=old['prefix_hash'] or tail!=old['tail_hash'])
                        if not rebuild:
                            offset=old['offset'];line_no=old['line_no'];turn_id=old['turn_id'] or '';errors=old['parse_errors']
                    elif prior:
                        # An archived/moved file may contain the same records. The
                        # stable message IDs and chunk hashes avoid duplicate work.
                        rebuild=False
                    if rebuild:
                        for mid, in db.execute('SELECT id FROM messages WHERE thread_id=?',(thread_id,)).fetchall():db.execute('DELETE FROM search_docs WHERE id=?',('message:'+mid,))
                        db.execute('DELETE FROM jobs WHERE thread_id=?',(thread_id,));db.execute('DELETE FROM messages WHERE thread_id=?',(thread_id,))
                        changed.add(thread_id)
                    f.seek(offset)
                    while True:
                        before=f.tell();raw=f.readline()
                        if not raw:break
                        # Do not consume an actively written final partial line.
                        if not raw.endswith(b'\n'):f.seek(before);break
                        line_no+=1
                        try:record=json.loads(raw)
                        except (ValueError,UnicodeDecodeError):errors+=1;continue
                        if not isinstance(record,dict):errors+=1;continue
                        payload=record.get('payload',{})
                        if not isinstance(payload,dict):continue
                        rtype=record.get('type');rtype_sub=payload.get('type')
                        if rtype=='turn_context' or (rtype=='event_msg' and rtype_sub=='task_started'):turn_id=payload.get('turn_id') or turn_id
                        timestamp=record.get('timestamp') or stamp
                        if _record(db,store,thread_id,path,line_no,payload,rtype,timestamp,turn_id):changed.add(thread_id)
                        if timestamp:db.execute('UPDATE threads SET updated_at=max(updated_at,?) WHERE id=?',(timestamp,thread_id))
                    offset=f.tell()
                # Metadata is not semantic categorization; never use cwd as a topic.
                existing_title=db.execute('SELECT title_source FROM threads WHERE id=?',(thread_id,)).fetchone()[0]
                if existing_title!='official':
                    first_user=db.execute("SELECT text FROM messages WHERE thread_id=? AND role='user' ORDER BY line_no LIMIT 1",(thread_id,)).fetchone()
                    if first_user:
                        fallback=' '.join(first_user[0].split())[:90]
                        db.execute('UPDATE threads SET title=? WHERE id=?',(fallback,thread_id))
                title_now=db.execute('SELECT title FROM threads WHERE id=?',(thread_id,)).fetchone()[0]
                db.execute('UPDATE search_docs SET title=? WHERE thread_id=? AND title<>?',(title_now,thread_id,title_now))
                prefix,tail=_fingerprint(path,offset)
                db.execute('''INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                           (source_path,thread_id,offset,line_no,stat.st_size,stat.st_mtime_ns,str(stat.st_ino),prefix,tail,turn_id,errors,1,None))
                files_read+=1
        except (OSError,ValueError) as exc:
            with store.connection() as db:
                db.execute('INSERT INTO files(path,error,parse_errors,available) VALUES(?,?,1,1) ON CONFLICT(path) DO UPDATE SET error=excluded.error',
                           (source_path,str(exc)[:2000]))
    with store.connection() as db:
        for row in db.execute('SELECT path,thread_id FROM files').fetchall():
            if row['path'] not in seen:db.execute('UPDATE files SET available=0 WHERE path=?',(row['path'],))
        db.execute('UPDATE threads SET available=CASE WHEN EXISTS(SELECT 1 FROM files f WHERE f.thread_id=threads.id AND f.available=1 AND f.error IS NULL) THEN 1 ELSE 0 END')
        for thread_id,item in metadata.items():
            title=_metadata_title(item)
            if title:
                db.execute("UPDATE threads SET title=?,title_source='official' WHERE id=?",(title,thread_id))
                db.execute('UPDATE search_docs SET title=? WHERE thread_id=? AND title<>?',(title,thread_id,title))
    # Build after all files have been seen, so copied fork message IDs resolve to
    # the earliest original main thread regardless of directory traversal order.
    for thread_id in changed:
        with store.connection() as db:_build_chunks(db,store,thread_id)
    with store.connection() as db:
        db.execute("INSERT INTO settings VALUES('parser_format',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(PARSER_VERSION,))
        db.execute("INSERT INTO settings VALUES('source_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",('未找到 sessions 或 archived_sessions 目录，请检查 Codex 数据路径。' if not source_dirs else '',))
        if changed:store._recompute_projects(db);store._refresh_derived_docs(db)
        db.execute("INSERT INTO settings VALUES('last_sync',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(now(),))
    result=store.status();result.update(files_read=files_read,changed_threads=len(changed))
    return result
