import { getLanguage, locale, localizeError, setLanguage, tr } from './i18n';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUpRight, Check, CheckCheck, ChevronDown, ChevronRight, Circle, Clock3, ExternalLink, FileText, FolderKanban, GitMerge, History, Layers3, ListFilter, LoaderCircle, MessageSquare, MoreHorizontal, Pause, Pencil, Play, RefreshCw, Search, Settings2, ShieldCheck, Sparkles, Split, Tag, TriangleAlert, X } from 'lucide-react';
import { api, date, errorText, patch, post } from './api';
import type { Correction, EntityKind, Message, ProgressEvent, Project, SearchItem, Segment, Status, Thread, ThreadDetail, Topic, View } from './types';

const eventLabels: Record<string, string> = {discussed: '讨论过', decided: '已决定', reported_done: '对话报告完成', verified: '对话附验证记录', blocked: '遇到阻碍', retracted: '已撤回 / 调整'};
const statusLabels: Record<string, string> = {pending:'待分析', running:'分析中', analyzed:'已分析', completed:'已分析', done:'已分析', failed:'分析失败', partial:'部分已分析', none:'待分析'};
const navItems: {id: View; label: string; icon: typeof Search; hint: string}[] = [{id:'projects',label:'项目总览',icon:FolderKanban,hint:'让工作保持脉络'}, {id:'topics',label:'主题索引',icon:Layers3,hint:'连接长期关注'}, {id:'history',label:'对话历史',icon:History,hint:'回到每一次讨论'}];
const routeFromLocation = () => {const p = new URLSearchParams(location.search); return {view:(['projects','topics','history','search'].includes(p.get('view') || '') ? p.get('view') : 'projects') as View, project:p.get('project') || '', q:p.get('q') || '', topic:p.get('topic') || '', filterProject:p.get('filterProject') || '', since:p.get('since') || '', until:p.get('until') || ''};};
type Route = ReturnType<typeof routeFromLocation>;
function Empty({title, children, icon: Icon = Layers3}: {title: string; children: React.ReactNode; icon?: typeof Search}) {return <div className="empty"><div className="empty-icon"><Icon size={27}/></div><h3>{title}</h3><p>{children}</p></div>;}
function Loading({label=tr("正在读取…")}:{label?:string}) {return <div className="loading"><LoaderCircle size={20} className="spin"/>{label}</div>;}
function ErrorBox({error, retry}:{error: string; retry?:()=>void}) {return <div className="error-box" role="alert"><TriangleAlert size={17}/><span>{localizeError(error)}</span>{retry && <button onClick={retry}>{tr("重试")}</button>}</div>;}
function Badge({kind, children}:{kind?:string;children:React.ReactNode}) {return <span className={`badge ${kind || ''}`}>{children}</span>;}
function Evidence({kind}:{kind:string}) {return <Badge kind={kind}>{tr(eventLabels[kind] || kind || '依据待整理')}</Badge>;}
function SourceLink({onClick, children=tr("查看来源")}:{onClick:()=>void;children?:React.ReactNode}) {return <button className="text-button source-button" onClick={onClick}><FileText size={13}/>{children}<ArrowUpRight size={12}/></button>;}

export default function App() {
  const [language, updateLanguage] = useState(getLanguage);
  useEffect(() => { setLanguage(language); }, [language]);
  const [route, setRoute] = useState<Route>(routeFromLocation);
  const [status, setStatus] = useState<Status | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [connectionError, setConnectionError] = useState('');
  const [catalogError, setCatalogError] = useState('');
  const [source, setSource] = useState<{id:string;focus?:string} | null>(null);
  const [correction, setCorrection] = useState<Correction | null>(null);
  const [grouping, setGrouping] = useState<Project | null>(null);
  const rootProjects = useMemo(()=>projects.filter(project=>!project.parent_id),[projects]);
  const [revision, setRevision] = useState(0);
  const [notification, setNotification] = useState('');
  const [statusExpanded, setStatusExpanded] = useState(false);
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [actionBusy, setActionBusy] = useState('');
  const [searchDraft, setSearchDraft] = useState(route.q);
  const lastAnalysis = useRef<string | undefined>(undefined);
  const clientId = useRef(crypto.randomUUID());
  const notify = useCallback((message:string) => {setNotification(message);},[]);
  const navigate = useCallback((next:Partial<Route>) => {setRoute(old => {const n = {...old, ...next}; const p = new URLSearchParams(); Object.entries(n).forEach(([key,value]) => {if(value && !(key==='view' && value==='projects')) p.set(key,value);}); history.pushState({}, '', `${location.pathname}${p.size ? `?${p}` : ''}`); return n;});},[]);
  useEffect(() => {const handler = () => {const next = routeFromLocation(); setRoute(next); setSearchDraft(next.q);}; window.addEventListener('popstate',handler); return () => window.removeEventListener('popstate',handler);},[]);
  useEffect(() => {if(!notification) return; const timer = window.setTimeout(()=>setNotification(''),6000); return ()=>clearTimeout(timer);},[notification]);
  const reloadCatalog = useCallback(async () => {
    try {const [p,t] = await Promise.all([api<Project[]>('/projects'),api<Topic[]>('/topics')]); setProjects(p); setTopics(t); setCatalogError('');} catch(e) {setCatalogError(errorText(e));} finally {setCatalogLoading(false);}
  },[]);
  const refreshStatus = useCallback(async () => {
    try {const s = await api<Status>('/status'); setStatus(s); setConnectionError('');
      const fingerprint = [s.analysis_updated_at,s.last_sync,s.analyzed,s.chunks,s.threads,s.failed].join(':');
      if(lastAnalysis.current !== fingerprint) {lastAnalysis.current=fingerprint; setRevision(n=>n+1);}
    } catch(e) {setConnectionError(tr("无法连接本地服务：{0}", [errorText(e)]));}
  },[]);
  useEffect(() => {void reloadCatalog();},[reloadCatalog,revision]);
  useEffect(() => {void refreshStatus(); const timer=window.setInterval(refreshStatus,4000); return ()=>clearInterval(timer);},[refreshStatus]);
  useEffect(() => {const heartbeat = () => post('/heartbeat',{client_id:clientId.current}).catch(()=>{}); void heartbeat(); const timer=window.setInterval(heartbeat,20000); return ()=>clearInterval(timer);},[]);
  const runAction = async (action: 'sync' | 'pause' | 'resume' | 'retry') => {
    setActionBusy(action);
    try {await post(action==='sync' ? '/sync' : '/analysis', action==='sync' ? {} : {action}); await refreshStatus(); if(action==='sync') setRevision(n=>n+1); notify({sync:tr("正在检查新增聊天内容"),pause:tr("已暂停整理；正在处理的这一批会继续完成"),resume:tr("已继续整理聊天内容"),retry:tr("未成功的内容已安排重新整理")}[action]);} catch(e) {notify(errorText(e));} finally {setActionBusy('');}
  };
  const openThread = async (id:string) => {try {await post(`/open-thread/${encodeURIComponent(id)}`);} catch(e) {notify(tr("无法打开原聊天：{0}", [errorText(e)]));}};
  const correctionDone = () => {setCorrection(null); setGrouping(null); setRevision(n=>n+1); void refreshStatus(); notify(tr("已保存修改，后续分析会保留人工调整"));};
  const title = route.project && route.view==='projects' ? tr("项目详情") : {projects:tr("项目总览"),topics:tr("主题索引"),history:tr("对话历史"),search:tr("全局搜索")}[route.view];
  const activeAnalysis = status?.analysis.active;
  const fullCoverage = status && status.chunks > 0 && status.analyzed===status.chunks;
  return <div className="app-shell">
    {modelSettingsOpen && <ModelSettingsModal demo={!!status?.demo} onClose={()=>setModelSettingsOpen(false)} onSaved={()=>{setModelSettingsOpen(false); void refreshStatus(); notify(tr("模型设置已保存，从下一批开始生效"));}}/>}
    <aside className="sidebar">
      <a href="/" className="brand" onClick={e=>{e.preventDefault();navigate({view:'projects',project:''});}}><span className="brand-mark"><i/><i/><i/></span><span><strong>{tr("对话图谱")}</strong><small>CODEX ATLAS</small></span></a>
      <div className="sidebar-caption">{tr("你的工作，连成一线")}</div>
      <nav aria-label={tr("主导航")}>{navItems.map(({id,label,icon:Icon,hint})=><button key={id} className={`nav-item ${route.view===id ? 'selected' : ''}`} onClick={()=>navigate({view:id,project:''})}><Icon size={19}/><span>{tr(label)}<small>{tr(hint)}</small></span>{id==='projects' && rootProjects.length>0 && <b>{rootProjects.length}</b>}</button>)}</nav>
      <div className="sidebar-rule"/>
      <button className={`nav-item search-nav ${route.view==='search'?'selected':''}`} onClick={()=>navigate({view:'search',project:''})}><Search size={18}/><span>{tr("搜索全部内容")}</span></button>
      <div className="sidebar-bottom"><div className="local-label"><span className="status-dot"/>{tr("本机工作空间")}</div><p>{tr("按对话内容组织")}<br/>{tr("每一条进展，都有出处")}</p><div className="sidebar-foot"><ShieldCheck size={14}/>{tr("本地存储 · Codex 分析")}</div></div>
    </aside>
    <div className="main-wrap">
      <header className="topbar"><div className="breadcrumb">{tr("工作空间 ")}<ChevronRight size={13}/><span>{title}</span></div><form className="header-search" onSubmit={e=>{e.preventDefault();navigate({view:'search',project:'',q:searchDraft});}}><Search size={16}/><input aria-label={tr("搜索所有对话与项目")} value={searchDraft} onChange={e=>setSearchDraft(e.target.value)} placeholder={tr("搜索对话、项目、决定…")}/><kbd>↵</kbd></form><label className="language-picker"><span>{tr('界面语言')}</span><select aria-label={tr('界面语言')} value={language} onChange={event=>{const next=event.target.value==='zh'?'zh':'en';setLanguage(next);updateLanguage(next);}}><option value="en" lang="en">English</option><option value="zh" lang="zh-CN">中文</option></select></label><button className={`button subtle sync-button ${status?.sync.running?'working':''}`} disabled={!!status?.demo || !!actionBusy || status?.sync.running} onClick={()=>runAction('sync')}><RefreshCw size={15} className={status?.sync.running?'spin':''}/><span>{status?.sync.running?tr("检查中"):tr("检查新内容")}</span></button></header>
      <main>
        {connectionError && <ErrorBox error={connectionError} retry={refreshStatus}/>}
        {status?.demo && <div className="mode-notice" role="status"><ShieldCheck size={18}/><div><strong>{tr('虚构数据演示')}</strong><p>{tr('这里仅展示虚构项目和对话，不读取你的 Codex 记录，也不会调用 AI 或打开真实聊天。可以切换语言、浏览项目和搜索示例内容。')}</p></div></div>}
        {status?.analysis.requires_consent && !status.demo && <div className="mode-notice consent-notice"><ShieldCheck size={18}/><div><strong>{tr('开始前，请了解 AI 整理的处理方式')}</strong><p>{tr('索引保存在本机。点击“开始 AI 整理”后，所选聊天正文、工具结果摘录和项目摘要会通过已登录的 Codex 发往云端分析，并消耗你的账户额度。关闭网页后后台整理仍会继续；你可以随时暂停。')}</p></div></div>}
        <section className="coverage-strip" aria-label={tr("收录与整理进度")}>
          <button className="coverage-toggle" onClick={()=>setStatusExpanded(v=>!v)} aria-expanded={statusExpanded}>
            <span className={`status-orb ${activeAnalysis?'pulsing':''}`}><Sparkles size={15}/></span>
            <span className="coverage-label">{!status?tr("正在连接"):status.analysis.paused?tr("整理已暂停"):status.analysis.cooldown_until?tr("等待自动恢复"):activeAnalysis?tr("正在整理 {0} 批 / 上限 {1} 批", [status.analysis.active_count ?? 1, status.analysis.parallelism ?? 1]):status.pending>0?tr("等待 AI 整理"):status.failed>0?tr("部分内容整理失败"):status.chunks>0?tr("整理已完成"):tr("尚无待整理内容")}</span>
            <span className="coverage-counts">{tr("已收录 ")}<b>{status?.threads.toLocaleString(locale()) ?? '—'}</b>{tr(" 条聊天 ")}<i/>{tr("AI 已整理 ")}<b>{status?.analyzed.toLocaleString(locale()) ?? '—'}</b> / {status?.chunks.toLocaleString(locale()) ?? '—'}{tr(" 批")}</span>
            <ChevronDown size={14} className={statusExpanded?'rotated':''}/>
          </button>
          <div className="queue-controls">
            <button className="button model-settings-button" onClick={()=>setModelSettingsOpen(true)}><Settings2 size={14}/>{tr("模型设置")}</button>
            {!!status?.failed && <button className="text-button danger" disabled={!!status?.demo || !!actionBusy} onClick={()=>runAction('retry')}><RefreshCw size={13}/>{tr("重试 ")}{status.failed}{tr(" 批")}</button>}
            <button className="text-button" disabled={!status || !!status.demo || !!actionBusy} onClick={()=>runAction(status?.analysis.paused?'resume':'pause')}>{status?.analysis.paused?<Play size={14}/>:<Pause size={14}/>}<span>{status?.analysis.requires_consent?tr('开始 AI 整理'):status?.analysis.paused?tr("继续整理"):tr("暂停整理")}</span></button>
          </div>
        </section>
        {statusExpanded && status && <section className="status-details">
          <p className="status-explainer">{tr("收录后就能搜索；AI 整理后，会归入主题、项目，并提炼摘要和进展。")}</p>
          <div className="status-stats">
            <div><span>{tr("可搜索的内容")}</span><strong>{status.messages.toLocaleString(locale())}<small>{tr("条")}</small></strong><p>{tr("包括聊天消息、工具运行结果及 AI 协作记录，不代表已完成 AI 整理。")}</p></div>
            <div><span>{tr("等待 AI 整理")}</span><strong>{status.pending.toLocaleString(locale())}<small>{tr("批")}</small></strong><p>{tr("长对话会分批处理。一批不等于一条聊天，也不等于一个项目。")}</p></div>
            <div><span>{tr("整理失败")}</span><strong>{status.failed.toLocaleString(locale())}<small>{tr("批")}</small></strong><p>{status.failed>0?tr("这些内容未能完成 AI 整理，可以重试；已整理的结果仍保留。"):tr("目前没有整理失败的内容。")}</p></div>
          </div>
          <p>{activeAnalysis?tr("AI 正在处理其中一批内容。"):''}{fullCoverage?tr("当前收录的内容已全部完成 AI 整理。"):tr("项目摘要与进展只来自已整理的内容，后续会逐步补充。")}</p>
          <details className="collection-details">
            <summary><span>{tr("查看收录详情")}</span><small>{status.parsing_errors || status.missing_sources ? tr("{0} 处读取异常 · {1} 条聊天原文件不可用", [status.parsing_errors, status.missing_sources]) : tr("没有发现读取问题")}</small><ChevronDown size={14}/></summary>
            <div className="status-stats">
              <div><span>{tr("后台辅助对话")}</span><strong>{status.technical_threads.toLocaleString(locale())}<small>{tr("条")}</small></strong><p>{tr("Codex 子代理、审核或自动化等后台记录，不单独列为项目。")}</p></div>
              <div><span>{tr("读取异常")}</span><strong>{status.parsing_errors.toLocaleString(locale())}<small>{tr("处")}</small></strong><p>{tr("部分原始记录或文件无法正常读取，可能导致内容收录不全。")}</p></div>
              <div><span>{tr("原文件不可用的聊天")}</span><strong>{status.missing_sources.toLocaleString(locale())}<small>{tr("条")}</small></strong><p>{tr("原文件缺失或暂时无法读取；已收录的内容仍可搜索。")}</p></div>
            </div>
            <p>{tr("聊天正文：")}{status.dialogue_messages?.toLocaleString(locale()) ?? '—'}{tr(" 条；工具运行结果：")}{status.tool_messages?.toLocaleString(locale()) ?? '—'}{tr(" 条；其余为 AI 内部协作记录。AI 主要整理聊天正文，按需摘取工具运行结果作为依据。")}</p>
            <p>{tr("已收录内容的时间范围：")}{date(status.first_message_at,true)} — {date(status.last_message_at,true)}</p>
            <p>{tr("上次检查新内容：")}{date(status.last_sync,true)}{tr(" · 整理结果更新：")}{date(status.analysis_updated_at,true)}</p>
            <p>{tr("使用模型：")}{status.analysis.model || tr("Codex 默认")}{tr(" · 思考强度：")}{status.analysis.effort || tr("默认")}</p>
          </details>
          <p>{status.analysis.background?tr("后台持续整理，每 30 秒检查新内容；关闭网页也会继续。点击“暂停整理”可停止领取新批次。"):tr("页面打开时每 30 秒检查新内容；关闭后不再开始新的整理，正在处理的一批会继续完成。")}</p>
          {!!status.retrying && <p>{status.retrying}{tr(" 批已安排自动重试，最早重试时间：")}{date(new Date((status.next_retry_at || 0)*1000).toISOString(),true)}{tr("。多次校验失败的批次会保留错误，继续整理其他内容。")}</p>}
          {!!status.analysis.cooldown_until && <p>{tr("服务暂时不可用或连续出错，将在 ")}{date(new Date(status.analysis.cooldown_until*1000).toISOString(),true)}{tr(" 自动尝试恢复；手动暂停仍优先。")}</p>}
          {[status.sync.error,status.sync.metadata_error,status.analysis.error].filter(Boolean).map((e,i)=><ErrorBox key={i} error={String(e)}/>)}
          {!!status.failed_jobs?.length&&<div className="status-error-list"><h3>{tr("未能完成整理的内容 ")}<span>{tr("最近 ")}{Math.min(5,status.failed_jobs.length)}{tr(" 条")}</span></h3>{status.failed_jobs.slice(0,5).map((job,i)=><details className="status-error-record" key={i}><summary><TriangleAlert size={13}/><span>{job.thread_title || tr("未命名对话")}</span><time>{date(job.updated_at,true)}</time><ChevronDown size={12}/></summary><pre>{job.error ? localizeError(job.error) : tr("暂无详细错误信息")}</pre></details>)}</div>}
          {!!status.source_errors?.length&&<div className="status-error-list"><h3>{tr("读取问题详情 ")}<span>{tr("显示 ")}{Math.min(5,status.source_errors.length)}{tr(" 条")}</span></h3>{status.source_errors.slice(0,5).map((source,i)=><details className="status-error-record" key={i}><summary><TriangleAlert size={13}/><span title={source.path}>{source.path?.split('/').pop() || tr("未知来源")}</span>{!!source.parse_errors&&<small>{source.parse_errors}{tr(" 处读取异常")}</small>}<ChevronDown size={12}/></summary><code>{source.path}</code><pre>{source.error ? localizeError(source.error) : tr("发现 {0} 处读取异常，部分聊天内容可能尚未收录。", [source.parse_errors || 0])}</pre></details>)}</div>}
        </section>}
        {catalogError && <ErrorBox error={tr("无法读取项目与主题：{0}", [catalogError])} retry={reloadCatalog}/>}
        {route.view==='projects' && !route.project && <ProjectGrid onMerged={correctionDone} onGroup={setGrouping} projects={projects} topics={topics} loading={catalogLoading} status={status} onSelect={id=>navigate({project:id})} onSource={(id,focus)=>setSource({id,focus})} onEdit={setCorrection}/>}
        {route.view==='projects' && route.project && <ProjectDetail onGroup={setGrouping} onProject={id=>navigate({project:id})} id={route.project} revision={revision} demo={!!status?.demo} onBack={()=>navigate({project:''})} onSource={(id,focus)=>setSource({id,focus})} onOpen={openThread} onEdit={setCorrection}/>}
        {route.view==='topics' && <TopicsView initialTopic={route.topic} topics={topics} projects={projects} revision={revision} loading={catalogLoading} onProject={id=>navigate({view:'projects',project:id})} onSource={(id,focus)=>setSource({id,focus})} onEdit={setCorrection}/>}
        {route.view==='history' && <HistoryView demo={!!status?.demo} revision={revision} onSource={id=>setSource({id})} onOpen={openThread}/>}
        {route.view==='search' && <SearchView route={route} navigate={navigate} topics={topics} projects={projects} revision={revision} onSource={(id,focus)=>setSource({id,focus})} onProject={id=>navigate({view:'projects',project:id})}/>}
        <footer className="page-footer"><span>{tr("从对话中找回工作的脉络")}</span><span>{tr("内容分类 · 可追溯来源 · 保留人工修正")}</span></footer>
      </main>
    </div>
    {source && <SourceDrawer demo={!!status?.demo} source={source} onClose={()=>setSource(null)} onOpen={openThread} onSource={(id,focus)=>setSource({id,focus})}/>}
    {correction && <CorrectionModal correction={correction} projects={projects} topics={topics} onClose={()=>setCorrection(null)} onDone={correctionDone}/>}
    {grouping && <GroupingModal project={grouping} projects={projects} onClose={()=>setGrouping(null)} onDone={correctionDone}/>}
    {notification && <div className="toast" role="status"><Check size={17}/><span>{localizeError(notification)}</span><button aria-label={tr("关闭提示")} onClick={()=>setNotification('')}><X size={14}/></button></div>}
  </div>;
}

function childrenFor(project:Project, projects:Project[]) {return project.children ?? projects.filter(item=>item.parent_id===project.id);}
function ProjectGrid({projects,topics,loading,status,onSelect,onSource,onEdit,onGroup,onMerged}:{onMerged:()=>void;projects:Project[];topics:Topic[];loading:boolean;status:Status|null;onSelect:(id:string)=>void;onSource:(id:string,focus?:string)=>void;onEdit:(c:Correction)=>void;onGroup:(project:Project)=>void}) {
  const currentLocale=locale();
  const [filter,setFilter] = useState(''); const [query,setQuery]=useState(''); const [sort,setSort]=useState('recent');
  const roots=useMemo(()=>projects.filter(project=>!project.parent_id),[projects]);
  const filtered=useMemo(()=>roots.filter(project=>{
    const members=[project,...childrenFor(project,projects)];
    return (!filter || members.some(member=>member.topic_ids?.includes(filter))) && (!query || members.some(member=>`${member.name} ${member.goal} ${member.summary}`.toLowerCase().includes(query.toLowerCase())));
  }).sort((a,b)=>sort==='name'?a.name.localeCompare(b.name,locale()):String(b.source_updated_at || '').localeCompare(String(a.source_updated_at || ''))),[roots,projects,filter,query,sort,currentLocale]);
  const [selected,setSelected]=useState<string[]>([]);
  const [menu,setMenu]=useState<{x:number;y:number}|null>(null);
  const [merging,setMerging]=useState<string[]|null>(null);
  const menuRef=useRef<HTMLDivElement>(null);
  const selection=filtered.filter(p=>selected.includes(p.id));
  useEffect(()=>{const visible=new Set(filtered.map(p=>p.id));setSelected(old=>{const next=old.filter(id=>visible.has(id));return next.length===old.length?old:next;});},[filtered]);
  useEffect(()=>{
    if(!menu)return;
    menuRef.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    const outside=(e:PointerEvent)=>{if(!menuRef.current?.contains(e.target as Node))setMenu(null);};
    const dismiss=()=>setMenu(null);
    const keyboard=(e:KeyboardEvent)=>{if(e.key==='Escape'||e.key==='Tab')dismiss();};
    window.addEventListener('pointerdown',outside);window.addEventListener('keydown',keyboard);
    window.addEventListener('scroll',dismiss,true);window.addEventListener('resize',dismiss);
    return()=>{window.removeEventListener('pointerdown',outside);window.removeEventListener('keydown',keyboard);window.removeEventListener('scroll',dismiss,true);window.removeEventListener('resize',dismiss);};
  },[menu]);
  const toggle=(id:string)=>{setSelected(old=>old.includes(id)?old.filter(x=>x!==id):[...old,id]);setMenu(null);};
  const openMenu=(id:string,x:number,y:number)=>{if(!selected.includes(id))setSelected([id]);setMenu({x:Math.max(8,Math.min(x,window.innerWidth-248)),y:Math.max(8,Math.min(y,window.innerHeight-128))});};
  const beginMerge=()=>{if(selection.length>=2){setMerging(selection.map(p=>p.id));setMenu(null);}};
  return <><div className="page-heading"><div><span className="eyebrow">{tr("看清工作的全貌")}</span><h1>{tr("项目总览")}<span className="heading-count">{roots.length}</span></h1><p>{tr("先看完整目标，再进入课程、工具与研究等具体工作分项。")}</p></div><div className="heading-note"><span className="small-line"/>{tr("主项目连接共同目标")}<br/><span>{tr("工作分项保留各自进展")}</span></div></div>
    <div className="overview-stats"><div><span>{tr("主项目")}</span><strong>{roots.length}<small>{tr("个完整目标")}</small></strong><FolderKanban size={22}/></div><div><span>{tr("连接主题")}</span><strong>{topics.length}<small>{tr("个关注方向")}</small></strong><Layers3 size={22}/></div><div><span>{tr("有待推进事项")}</span><strong>{roots.filter(p=>p.open_items?.length).length}<small>{tr("个主项目")}</small></strong><ArrowUpRight size={22}/></div></div>
    <div className="section-toolbar"><div className="section-label"><span className="dot-small"/>{tr("全部主项目 ")}<span>{filtered.length}</span></div><div className="toolbar-controls"><div className="small-search"><Search size={14}/><input aria-label={tr("筛选项目与工作分项")} placeholder={tr("项目或工作分项…")} value={query} onChange={e=>setQuery(e.target.value)}/></div><select aria-label={tr("按主题筛选项目")} value={filter} onChange={e=>setFilter(e.target.value)}><option value="">{tr("全部主题")}</option>{topics.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}</select><select aria-label={tr("项目排序")} value={sort} onChange={e=>setSort(e.target.value)}><option value="recent">{tr("最近对话进展")}</option><option value="name">{tr("按名称")}</option></select></div></div>
    <div className="project-selection-bar" aria-label={tr("批量项目操作")}>
      <label><input type="checkbox" aria-label={tr("选择当前显示的全部项目")} checked={filtered.length>0&&selection.length===filtered.length} disabled={!filtered.length} onChange={()=>{setSelected(selection.length===filtered.length?[]:filtered.map(p=>p.id));setMenu(null);}}/>{tr("选择当前显示")}</label>
      <span role="status">{tr("已选 {0} 个项目", [selection.length])}</span>
      <button className="button small" disabled={selection.length<2} onClick={beginMerge}><GitMerge size={14}/>{tr("合并所选项目")}</button>
      {selection.length>0&&<button className="text-button" onClick={()=>{setSelected([]);setMenu(null);}}>{tr("取消选择")}</button>}
      <small>{tr("勾选卡片后，可右键合并")}</small>
    </div>
    {menu&&<div ref={menuRef} className="project-context-menu" role="menu" aria-label={tr("所选项目操作")} style={{left:menu.x,top:menu.y}}>
      <span>{tr("已选 {0} 个项目", [selection.length])}</span>
      <button role="menuitem" disabled={selection.length<2} onClick={beginMerge}><GitMerge size={15}/>{tr("合并项目…")}</button>
      {selection.length<2&&<small>{tr("请先勾选至少两张卡片")}</small>}
    </div>}
    {merging&&<BatchMergeModal ids={merging} projects={projects} onClose={()=>setMerging(null)} onDone={()=>{setMerging(null);setSelected([]);onMerged();}}/>}
    {loading?<Loading/>:filtered.length===0?<Empty title={roots.length?tr("没有符合条件的主项目"):tr("项目正在从对话中浮现")}>{roots.length?tr("可以用主项目或工作分项的名称、目标进行筛选。"):status?.threads ? tr("已索引 {0} 条主对话。分析会识别共同目标，并在主项目内保留具体工作分项。", [status.threads]):tr("点击“检查新内容”读取本机 Codex 对话。分类依据是内容、目标与交付物；目录名称不会自动成为项目。")}</Empty>:<div className="project-grid">{filtered.map((p,i)=>{const children=childrenFor(p,projects);return <article className={`project-card ${selected.includes(p.id)?'is-selected':''}`} key={p.id} onContextMenu={e=>{e.preventDefault();openMenu(p.id,e.clientX,e.clientY);}} onKeyDown={e=>{if(e.key==='ContextMenu'||(e.shiftKey&&e.key==='F10')){e.preventDefault();const r=e.currentTarget.getBoundingClientRect();openMenu(p.id,r.left+25,r.top+45);}}}>
      <div className="card-top"><input className="project-select" type="checkbox" aria-label={tr("选择项目：{0}", [p.name])} checked={selected.includes(p.id)} onChange={()=>toggle(p.id)}/><span className={`project-symbol symbol-${i%4}`}><FolderKanban size={20}/></span><Badge>{p.stage || tr("阶段待确认")}</Badge>{!!p.manual && <span className="manual-mark" title={tr("含人工修正")}><Pencil size={12}/></span>}<button className="icon-button card-menu" aria-label={tr("编辑 {0}", [p.name])} onClick={()=>onEdit({mode:'edit',kind:'projects',entity:p})}><MoreHorizontal size={18}/></button></div>
      <button className="card-link" onClick={e=>{if(e.metaKey||e.ctrlKey)toggle(p.id);else onSelect(p.id);}}><h2>{p.name}<ArrowUpRight size={18}/></h2><p>{p.summary || p.goal || tr("正在整理项目摘要")}</p></button>
      <div className="topic-tags">{p.topics?.slice(0,3).map(t=><span key={t.id}>{t.name}</span>)}{(p.topics?.length||0)>3 && <span>+{p.topics.length-3}</span>}</div>
      {children.length>0?<div className="project-child-preview"><span><Layers3 size={12}/>{tr("工作分项 · ")}{children.length}</span><div>{children.slice(0,4).map(child=><button key={child.id} onClick={()=>onSelect(child.id)} title={tr("查看工作分项：{0}", [child.name])}>{child.name}</button>)}{children.length>4&&<button onClick={()=>onSelect(p.id)}>{tr("另 ")}{children.length-4}{tr(" 项")}<ChevronRight size={11}/></button>}</div></div>:<button className="text-button group-card-button" onClick={()=>onGroup(p)}><Layers3 size={12}/>{tr("归入主项目")}</button>}
      <div className="card-records"><CardRecord label={tr("最近成果")} event={p.latest_progress} onSource={onSource}/><CardRecord label={tr("最近障碍记录")} event={p.latest_blocker} onSource={onSource}/>{p.pending_decisions?.length?<details className="card-record"><summary><span>{tr("待决定")}</span><p>{p.pending_decisions[0]}</p><b>{p.pending_decisions.length}</b><ChevronDown size={11}/></summary><ul>{p.pending_decisions.map((item,i)=><li key={i}>{item}</li>)}</ul></details>:<div className="card-record missing"><span>{tr("待决定")}</span><p>{tr("尚未明确")}</p></div>}</div>
      <div className="card-next"><span>{p.open_items?.length?tr("待推进"):tr("当前目标")}</span><p>{p.open_items?.[0] || p.goal || tr("尚未提取明确的后续事项")}</p></div><div className="card-bottom"><span><MessageSquare size={13}/>{p.segment_count}{tr(" 个内容片段")}</span><time title={tr("最近关联对话的来源时间")}>{p.source_updated_at?tr("{0}记录", [date(p.source_updated_at)]):tr("暂无来源时间")}</time></div>
    </article>;})}</div>}
    <div className="provenance-note"><ShieldCheck size={15}/><p>{tr("主项目汇集工作分项的历史，分项保留独立来源与进展。历史对话中的“完成”和“附有验证记录”分别标注，当前状态仍以实际核验为准。")}</p></div>
  </>;
}

function BatchMergeModal({ids,projects,onClose,onDone}:{ids:string[];projects:Project[];onClose:()=>void;onDone:()=>void}) {
  const [target,setTarget]=useState(ids[0]);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const busyRef=useRef(false);const closeRef=useRef<HTMLButtonElement>(null);
  const close=()=>{if(!busyRef.current)onClose();};
  useModalKeyboard(close,closeRef);
  const items=ids.map(id=>projects.find(p=>p.id===id&&!p.parent_id)).filter((p):p is Project=>!!p);
  const valid=items.length===ids.length&&items.length>=2&&items.some(p=>p.id===target);
  const submit=async(e:React.FormEvent)=>{
    e.preventDefault();if(!valid||busyRef.current)return;
    busyRef.current=true;setBusy(true);setError('');
    try{await post('/projects/merge',{target_id:target,source_ids:ids.filter(id=>id!==target)});onDone();}
    catch(e){setError(errorText(e));}finally{busyRef.current=false;setBusy(false);}
  };
  return <div className="overlay modal-overlay" onMouseDown={e=>{if(e.target===e.currentTarget)close();}}><section className="correction-modal" role="dialog" aria-modal="true" aria-labelledby="batch-merge-title">
    <header><div><span className="eyebrow">{tr("整理项目归属")}</span><h2 id="batch-merge-title">{tr("合并 {0} 个项目", [ids.length])}</h2></div><button className="icon-button" ref={closeRef} disabled={busy} aria-label={tr("关闭合并项目")} onClick={close}><X size={19}/></button></header>
    <form onSubmit={submit}><div className="modal-body">
      <p className="form-help">{tr("将所选卡片合为一个项目。请选择保留的项目名称与目标；其他项目的内容片段、历史记录和工作分项会迁入，旧名称与聊天来源继续保留。")}</p>
      <ul className="batch-merge-list">{items.map(p=><li key={p.id}>{p.name}{!!p.child_count&&<small>{tr("含 {0} 个工作分项", [p.child_count])}</small>}</li>)}</ul>
      {!valid&&<ErrorBox error={tr("所选项目已变化，请取消后重新选择。")}/>}
      {error&&<ErrorBox error={error}/>}
      <label className="form-field">{tr("合并到哪个项目")}<select value={target} disabled={busy} onChange={e=>setTarget(e.target.value)}>{items.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <p className="form-help">{tr("确认后，其他所选卡片不再单独显示。同名工作分项会分别保留，后续自动分析会沿用这次合并关系。")}</p>
    </div><footer><button type="button" className="button" disabled={busy} onClick={close}>{tr("取消")}</button><button type="submit" className="button primary" disabled={!valid||busy}>{busy?<LoaderCircle className="spin" size={15}/>:<GitMerge size={15}/>} {busy?tr('正在合并…'):tr('确认合并')}</button></footer></form>
  </section></div>;
}

function CardRecord({label,event,onSource}:{label:string;event?:ProgressEvent|null;onSource:(id:string,focus?:string)=>void}) {if(!event)return <div className="card-record missing"><span>{label}</span><p>{tr("尚未提取到明确记录")}</p></div>;return <details className="card-record"><summary><span>{label}</span><p>{event.text}</p><time>{date(event.timestamp)}</time><ChevronDown size={11}/></summary><div className="card-record-body"><p>{event.text}</p><div><Evidence kind={event.kind}/><time>{date(event.timestamp,true)}</time></div>{event.kind==='blocked'&&<small>{tr("历史对话中的障碍记录，是否已解除需查看后续进展。")}</small>}<EventSources event={event} onSource={onSource}/></div></details>;}

function ProjectDetail({demo=false,id,revision,onBack,onSource,onOpen,onEdit,onGroup,onProject}:{demo?:boolean;id:string;revision:number;onBack:()=>void;onSource:(id:string,focus?:string)=>void;onOpen:(id:string)=>void;onEdit:(c:Correction)=>void;onGroup:(project:Project)=>void;onProject:(id:string)=>void}) {
  const [project,setProject]=useState<Project|null>(null);const [error,setError]=useState('');const [tab,setTab]=useState<'timeline'|'segments'|'threads'>('timeline');const [eventFilter,setEventFilter]=useState('');
  const requestVersion=useRef(0);
  const load=useCallback(()=>{const request=++requestVersion.current;api<Project>(`/projects/${encodeURIComponent(id)}`).then(p=>{if(request===requestVersion.current){setProject(p);setError('');}}).catch(e=>{if(request===requestVersion.current)setError(errorText(e));});},[id]);
  useEffect(()=>{setProject(null);setError('');setEventFilter('');},[id]);useEffect(load,[load,revision]);
  if(error)return <><button className="text-button back-button" onClick={onBack}><ArrowLeft size={15}/>{tr("返回项目总览")}</button><ErrorBox error={error} retry={load}/></>;
  if(!project)return <Loading label={tr("正在连接项目的历史脉络…")}/>;
  const events=project.events?.filter(e=>!eventFilter||e.kind===eventFilter) || [];
  const children=project.children || [];
  const hasChildren=children.length>0 || !!project.child_count;
  return <>
    <div className="detail-navigation"><button className="text-button back-button" onClick={onBack}><ArrowLeft size={15}/>{tr("项目总览")}</button>{project.parent_id&&<><ChevronRight size={13}/><button className="text-button" onClick={()=>onProject(project.parent_id!)}>{project.parent_name || tr("所属主项目")}</button><ChevronRight size={13}/><span>{tr("工作分项")}</span></>}</div>
    {project.parent_id&&<div className="parent-project-banner"><Layers3 size={17}/><div><span>{tr('这是“{0}”的工作分项', [project.parent_name || tr('所属主项目')])}</span><p>{tr("该分项的历史同时汇入主项目，在这里查看具体进展。")}</p></div><button className="text-button" onClick={()=>onProject(project.parent_id!)}>{tr("返回主项目")}<ArrowUpRight size={14}/></button></div>}
    <div className="detail-heading"><div><div className="topic-tags">{project.topics?.map(t=><span key={t.id}>{t.name}</span>)}</div><h1>{project.name}</h1><p>{project.goal || tr("尚未提取明确目标")}</p><div className="detail-meta"><Badge>{project.parent_id?tr("工作分项"):tr("主项目")}</Badge><Badge>{project.stage || tr("阶段待确认")}</Badge>{!!project.manual&&<span><Pencil size={12}/>{tr("含人工修正")}</span>}<span><Clock3 size={13}/>{tr("来源更新：")}{date(project.source_updated_at,true)}</span></div></div>
    <div className="entity-actions"><button className="button" onClick={()=>onEdit({mode:'edit',kind:'projects',entity:project})}><Pencil size={14}/>{tr("编辑")}</button>{!hasChildren&&<><button className="button" onClick={()=>onGroup(project)}><Layers3 size={14}/>{project.parent_id?tr("调整主项目"):tr("归入主项目")}</button><button className="button" onClick={()=>onEdit({mode:'merge',kind:'projects',entity:project})}><GitMerge size={14}/>{tr("合并")}</button><button className="button" onClick={()=>onEdit({mode:'split',kind:'projects',entity:project})}><Split size={14}/>{tr("拆分")}</button></>}</div></div>
    <div className="project-summary"><div className="summary-icon"><Sparkles size={19}/></div><div><span className="eyebrow">{hasChildren?tr("主项目与各分项的有据进展"):tr("最近有据进展")}</span><p>{project.summary || tr("已识别项目，正在积累可总结的内容。")}</p><small>{tr("覆盖 ")}{project.coverage?.analyzed ?? '—'} / {project.coverage?.total ?? '—'}{tr(" 个关联分析片段 · ")}{hasChildren?tr("汇集本项目及各工作分项的已有摘要；具体范围与原文依据见下方"):tr("摘要来自最近关联片段，并非完整累计总结；下方可展开原文依据")}{project.analyzed_at&&tr(" · 整理于 {0}", [date(project.analyzed_at,true)])}</small></div></div>
    {hasChildren&&<section className="work-items-section"><div className="section-toolbar"><div><h2>{tr("工作分项 ")}<span>{children.length}</span></h2><p>{tr("共同目标下的具体工作，分别保留阶段、成果和历史。")}</p></div><span className="muted">{tr("主项目 · 工作分项")}</span></div><div className="work-items-grid">{children.map(child=><article className="work-item-card" key={child.id}><div><Badge>{child.stage || tr("阶段待确认")}</Badge><button className="icon-button" title={tr("调整所属主项目")} aria-label={tr("调整 {0} 所属主项目", [child.name])} onClick={()=>onGroup(child)}><Settings2 size={14}/></button></div><button className="work-item-link" onClick={()=>onProject(child.id)}><h3>{child.name}<ArrowUpRight size={15}/></h3><p>{child.summary || child.goal || tr("尚无摘要")}</p></button><footer><span>{child.segment_count}{tr(" 个片段")}</span><time>{date(child.source_updated_at)}</time></footer></article>)}</div><p className="hierarchy-help">{tr("需要合并、拆分或改变归属时，请进入对应工作分项操作；各分项的原始对话保留完整。")}</p></section>}
    <div className="detail-columns"><div className="detail-main"><div className="tabs" role="tablist" aria-label={tr("项目内容")}><button role="tab" aria-selected={tab==='timeline'} className={tab==='timeline'?'active':''} onClick={()=>setTab('timeline')}><History size={16}/>{hasChildren?tr("汇总历史"):tr("历史步骤")} <span>{project.events?.length||0}</span></button><button role="tab" aria-selected={tab==='segments'} className={tab==='segments'?'active':''} onClick={()=>setTab('segments')}><Layers3 size={16}/>{tr("内容片段 ")}<span>{project.segments?.length||0}</span></button><button role="tab" aria-selected={tab==='threads'} className={tab==='threads'?'active':''} onClick={()=>setTab('threads')}><MessageSquare size={16}/>{tr("关联对话 ")}<span>{project.threads?.length||0}</span></button></div>
    {tab==='timeline'&&<><div className="timeline-toolbar"><span>{hasChildren?tr("汇集各分项历史，保留来源与变化"):tr("按历史发生顺序，保留决定与变化")}</span><select aria-label={tr("筛选进展类型")} value={eventFilter} onChange={e=>setEventFilter(e.target.value)}><option value="">{tr("全部进展类型")}</option>{Object.entries(eventLabels).map(([value,label])=><option value={value} key={value}>{tr(label)}</option>)}</select></div>{events.length?<div className="timeline">{events.map(e=><TimelineEvent key={e.id} event={e} onSource={onSource} onProject={onProject}/>)}</div>:<Empty title={tr("暂无可展示的历史步骤")} icon={History}>{tr("随着关联对话完成分析，这里会出现讨论、决定、完成记录和后续调整。")}</Empty>}</>}
    {tab==='segments'&&<div className="segment-list">{project.segments?.length?project.segments.map(s=><SegmentCard key={s.id} segment={s} onSource={onSource} onEdit={onEdit} onProject={onProject}/>):<Empty title={tr("暂无内容片段")}>{tr("关联的原对话完成分析后会显示在这里。")}</Empty>}</div>}
    {tab==='threads'&&<div className="thread-list">{project.threads?.length?project.threads.map(t=><ThreadRow demo={demo} key={t.id} thread={t} onSource={onSource} onOpen={onOpen}/>):<Empty title={tr("暂无关联对话")}>{tr("当前没有可显示的对话来源。")}</Empty>}</div>}
    </div><aside className="detail-side"><div className="side-panel"><h3><ArrowUpRight size={16}/>{tr("待推进事项")}</h3>{project.open_items?.length?<ul className="task-list">{project.open_items.map((item,i)=><li key={i}><Circle size={13}/><span>{item}</span></li>)}</ul>:<p className="muted">{tr("未从已分析对话中提取到明确的待办事项。")}</p>}</div><div className="side-panel"><h3><CheckCheck size={16}/>{tr("重要决定")}</h3>{project.decisions?.length?<ul className="decision-list">{project.decisions.map((item,i)=><li key={i}>{item}</li>)}</ul>:<p className="muted">{tr("暂无已记录的明确决定。")}</p>}</div><div className="evidence-help"><ShieldCheck size={17}/><p>{tr("“对话附验证记录”表示历史对话中存在验证依据，不代表工具重新运行了验证。")}</p></div></aside></div>
  </>;
}

function EventSources({event,onSource}:{event:ProgressEvent;onSource:(id:string,focus?:string)=>void}) {const refs=Array.from(new Set(event.message_ids||[]));return <div className="event-sources">{refs.length?refs.map((id,i)=><SourceLink key={id} onClick={()=>onSource(event.thread_id,id)}>{refs.length===1?tr("查看来源"):tr("来源 {0}", [i+1])}</SourceLink>):<SourceLink onClick={()=>onSource(event.thread_id)}/>}</div>;}
function TimelineEvent({event:e,onSource,onProject}:{event:ProgressEvent;onSource:(id:string,focus?:string)=>void;onProject?:(id:string)=>void}) {return <article className={`timeline-event event-${e.kind}`}><div className="timeline-node">{e.kind==='verified'?<ShieldCheck size={14}/>:e.kind==='reported_done'?<Check size={14}/>:e.kind==='blocked'?<TriangleAlert size={14}/>:<Circle size={9}/>}</div><div className="event-top"><Evidence kind={e.kind}/><time>{date(e.timestamp,true)}</time></div>{e.project_name&&<button className="provenance-project" onClick={()=>e.project_id&&onProject?.(e.project_id)} disabled={!onProject||!e.project_id}><Layers3 size={12}/>{tr("来自：")}{e.project_name}</button>}<p>{e.text}</p><div className="event-bottom"><span>{e.thread_title || tr("源对话")}</span><EventSources event={e} onSource={onSource}/></div></article>;}
function SegmentCard({segment:s,onSource,onEdit,onProject}:{segment:Segment;onSource:(id:string,focus?:string)=>void;onEdit:(c:Correction)=>void;onProject?:(id:string)=>void}) {return <article className="segment-card"><div className="segment-top"><Badge>{s.stage || tr("内容片段")}</Badge>{!!s.manual&&<Badge kind="manual">{tr("人工归属")}</Badge>}<time>{date(s.updated_at)}</time></div>{s.project_name&&<button className="provenance-project" onClick={()=>s.project_id&&onProject?.(s.project_id)} disabled={!onProject||!s.project_id}><Layers3 size={12}/>{tr("来自：")}{s.project_name}</button>}<p>{s.summary}</p><div className="topic-tags">{s.topics?.map(t=><span key={t.id}>{t.name}</span>)}</div><div className="segment-source"><span>{s.thread_title || tr("源对话")}</span><div><button className="text-button" onClick={()=>onEdit({mode:'assign',segment:s})}><Settings2 size={13}/>{tr("调整归属")}</button><SourceLink onClick={()=>onSource(s.thread_id,s.message_ids?.[0])}/></div></div></article>;}
function ThreadRow({demo=false,thread:t,onSource,onOpen}:{demo?:boolean;thread:Thread;onSource:(id:string)=>void;onOpen:(id:string)=>void}) {return <article className="thread-row"><div className="thread-icon"><MessageSquare size={18}/></div><button className="thread-text" onClick={()=>onSource(t.id)}><strong>{t.title || tr("无标题对话")}</strong><span>{t.updated_at && date(t.updated_at,true)}{t.message_count!==undefined&&tr(" · {0} 条消息", [t.message_count])}{!!t.archived&&tr(" · 已归档")}{!t.available&&tr(" · 来源文件缺失")}</span></button>{t.analysis_status&&<Badge kind={t.analysis_status==='failed'?'blocked':''}>{tr(statusLabels[t.analysis_status] || t.analysis_status)}</Badge>}<button className="icon-button" title={demo?tr('演示模式不会打开真实聊天'):t.available?tr("在 Codex 中打开"):tr("来源缺失，无法直接打开")} aria-label={tr("在 Codex 中打开 {0}", [t.title])} disabled={demo || !t.available} onClick={()=>onOpen(t.id)}><ExternalLink size={16}/></button><button className="icon-button" aria-label={tr("查看 {0}", [t.title])} onClick={()=>onSource(t.id)}><ChevronRight size={18}/></button></article>;}

function TopicsView({initialTopic,topics,projects,revision,loading,onProject,onSource,onEdit}:{initialTopic:string;topics:Topic[];projects:Project[];revision:number;loading:boolean;onProject:(id:string)=>void;onSource:(id:string,focus?:string)=>void;onEdit:(c:Correction)=>void}) {
  const [selected,setSelected]=useState<string[]>(initialTopic?[initialTopic]:[]);const [details,setDetails]=useState<Topic[]>([]);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  useEffect(()=>{if(!loading)setSelected(old=>old.filter(id=>topics.some(t=>t.id===id)));},[topics,loading]);
  useEffect(()=>{if(!selected.length){setDetails([]);return;}let live=true;setBusy(true);Promise.all(selected.map(id=>api<Topic>(`/topics/${encodeURIComponent(id)}`))).then(d=>{if(live){setDetails(d);setError('');}}).catch(e=>{if(live)setError(errorText(e));}).finally(()=>{if(live)setBusy(false);});return()=>{live=false;};},[selected,revision]);
  const filteredProjects=projects.filter(p=>!p.parent_id&&selected.every(id=>p.topic_ids?.includes(id)));
  const segments=Array.from(new Map(details.flatMap(t=>t.segments||[]).filter(s=>selected.every(id=>s.topic_ids?.includes(id))).map(s=>[s.id,s])).values());
  const selectedTopic=selected.length===1 ? details.find(topic=>topic.id===selected[0]) : null;
  return <><div className="page-heading"><div><span className="eyebrow">{tr("连接想法与工作")}</span><h1>{tr("主题索引")}<span className="heading-count">{topics.length}</span></h1><p>{tr("长期关注的信息与项目，在这里相互连接。")}</p></div><div className="heading-note">{tr("主题可以重叠")}<br/><span>{tr("知识讨论也有自己的位置")}</span></div></div>
    {loading?<Loading/>:!topics.length?<Empty title={tr("你的关注方向正在整理中")}>{tr("分析会阅读对话正文提取主题。一般知识讨论会保留在主题中，无需创建项目。")}</Empty>:<><div className="section-toolbar"><div className="section-label"><Tag size={15}/>{tr("选择主题 ")}<span>{tr("多选取交集")}</span></div>{selected.length>0&&<button className="text-button" onClick={()=>setSelected([])}>{tr("清除 ")}{selected.length}{tr(" 个筛选")}<X size={13}/></button>}</div><div className="topic-grid">{topics.map(t=><button key={t.id} className={`topic-card ${selected.includes(t.id)?'selected':''}`} onClick={()=>setSelected(old=>old.includes(t.id)?old.filter(x=>x!==t.id):[...old,t.id])}><div><Tag size={17}/>{selected.includes(t.id)&&<Check size={16}/>}</div><h3>{t.name}</h3><span>{t.project_count}{tr(" 个项目 · ")}{t.segment_count}{tr(" 个片段")}</span>{!!t.manual&&<Pencil className="topic-manual" size={11}/>}</button>)}</div>
    {selected.length>0&&<section className="topic-results"><div className="section-toolbar"><h2>{selected.map(id=>topics.find(t=>t.id===id)?.name).join(' ∩ ')}</h2>{selectedTopic&&<div className="entity-actions"><button className="button" onClick={()=>onEdit({mode:'edit',kind:'topics',entity:selectedTopic})}><Pencil size={13}/>{tr("编辑")}</button><button className="button" onClick={()=>onEdit({mode:'merge',kind:'topics',entity:selectedTopic})}><GitMerge size={13}/>{tr("合并")}</button><button className="button" onClick={()=>onEdit({mode:'split',kind:'topics',entity:selectedTopic})}><Split size={13}/>{tr("拆分")}</button></div>}</div>{error&&<ErrorBox error={error}/>}<h3 className="subheading">{tr("关联项目 ")}<span>{filteredProjects.length}</span></h3><div className="related-projects">{filteredProjects.map(p=><button key={p.id} onClick={()=>onProject(p.id)}><FolderKanban size={16}/><span>{p.name}</span><Badge>{p.stage||tr("阶段待确认")}</Badge><ArrowUpRight size={15}/></button>)}{!filteredProjects.length&&<p className="muted">{tr("当前筛选下没有项目；一般讨论仍会保留在下方。")}</p>}</div><h3 className="subheading">{tr("全部相关片段 ")}<span>{segments.length}</span></h3>{busy?<Loading/>:segments.length?<div className="segment-list">{segments.map(s=><SegmentCard key={s.id} segment={s} onSource={onSource} onEdit={onEdit} onProject={onProject}/>)}</div>:<Empty title={tr("暂无匹配片段")}>{tr("尝试减少同时选择的主题。")}</Empty>}</section>}
    {!selected.length&&<div className="select-topic-hint"><Layers3 size={22}/><p>{tr("选择一个主题，查看它连接的项目与讨论。")}<br/><span>{tr("同时选择多个主题，可以发现它们的交集。")}</span></p></div>}</>}
  </>;
}

function HistoryView({demo=false,revision,onSource,onOpen}:{demo?:boolean;revision:number;onSource:(id:string)=>void;onOpen:(id:string)=>void}) {
  const [query,setQuery]=useState('');const [search,setSearch]=useState('');const [offset,setOffset]=useState(0);const [result,setResult]=useState<{items:Thread[];total:number}|null>(null);const [error,setError]=useState('');const [loading,setLoading]=useState(true);
  useEffect(()=>{let live=true;setLoading(true);const p=new URLSearchParams({q:search,offset:String(offset),limit:'40'});api<{items:Thread[];total:number}>(`/threads?${p}`).then(r=>{if(live){setResult(r);setError('');}}).catch(e=>{if(live)setError(errorText(e));}).finally(()=>{if(live)setLoading(false);});return()=>{live=false;};},[search,offset,revision]);
  return <><div className="page-heading"><div><span className="eyebrow">{tr("每次讨论都有价值")}</span><h1>{tr("对话历史")}<span className="heading-count">{result?.total ?? '—'}</span></h1><p>{tr("完整保留原始上下文，随时回到工作的起点。")}</p></div></div><div className="section-toolbar"><form className="inline-search" onSubmit={e=>{e.preventDefault();setSearch(query);setOffset(0);}}><Search size={16}/><input aria-label={tr("搜索对话历史")} value={query} onChange={e=>setQuery(e.target.value)} placeholder={tr("搜索对话标题…")}/><button className="button small" type="submit">{tr("查找")}</button></form><span className="muted">{tr("搜索正文请使用全局搜索 · 最近活动优先")}</span></div>{error&&<ErrorBox error={error}/>}<div className={loading?'is-loading':''}>{!result?<Loading/>:result.items.length?<div className="thread-list">{result.items.map(t=><ThreadRow demo={demo} key={t.id} thread={t} onSource={onSource} onOpen={onOpen}/>)}</div>:<Empty title={search?tr("没有找到匹配的对话"):tr("尚未索引对话")} icon={MessageSquare}>{search?tr("尝试不同关键词，或通过全局搜索精确检索内容。"):tr("点击右上角“检查新内容”读取本机历史。")}</Empty>}</div>{result&&<Pagination offset={offset} limit={40} total={result.total} onPage={setOffset}/>}</>;
}
function Pagination({offset,limit,total,onPage}:{offset:number;limit:number;total:number;onPage:(n:number)=>void}) {if(total===0)return null;return <div className="pagination"><span>{offset+1}–{Math.min(offset+limit,total)} / {total.toLocaleString(locale())}</span><button className="button small" disabled={offset===0} onClick={()=>onPage(Math.max(0,offset-limit))}><ArrowLeft size={13}/>{tr("上一页")}</button><button className="button small" disabled={offset+limit>=total} onClick={()=>onPage(offset+limit)}>{tr("下一页")}<ArrowRight size={13}/></button></div>;}

function SearchView({route,navigate,topics,projects,revision,onSource,onProject}:{route:Route;navigate:(r:Partial<Route>)=>void;topics:Topic[];projects:Project[];revision:number;onSource:(id:string,focus?:string)=>void;onProject:(id:string)=>void}) {
  const [draft,setDraft]=useState(route.q);const [offset,setOffset]=useState(0);const [result,setResult]=useState<{items:SearchItem[];total:number}|null>(null);const [error,setError]=useState('');const [loading,setLoading]=useState(false);
  useEffect(()=>setDraft(route.q),[route.q]);
  useEffect(()=>setOffset(0),[route.q,route.topic,route.filterProject,route.since,route.until]);
  useEffect(()=>{if(!route.q.trim()){setResult(null);return;}let live=true;setLoading(true);const p=new URLSearchParams({q:route.q,offset:String(offset),limit:'30'});if(route.topic)p.set('topic_id',route.topic);if(route.filterProject)p.set('project_id',route.filterProject);if(route.since)p.set('since',route.since);if(route.until)p.set('until',route.until);api<{items:SearchItem[];total:number}>(`/search?${p}`).then(r=>{if(live){setResult(r);setError('');}}).catch(e=>{if(live)setError(errorText(e));}).finally(()=>{if(live)setLoading(false);});return()=>{live=false;};},[route.q,route.topic,route.filterProject,route.since,route.until,offset,revision]);
  const itemLabels:Record<string,string>={message:tr("原始消息"),project:tr("项目"),topic:tr("主题"),segment:tr("内容片段"),event:tr("进展记录")};
  const hasFilter=!!(route.topic||route.filterProject||route.since||route.until);
  return <><div className="page-heading"><div><span className="eyebrow">{tr("找回对话中的线索")}</span><h1>{tr("全局搜索")}</h1><p>{tr("查找原文、摘要和重要决定，让每一条线索都有去处。")}</p></div></div><form className="big-search" onSubmit={e=>{e.preventDefault();navigate({q:draft});}}><Search size={21}/><input autoFocus aria-label={tr("全局搜索关键词")} value={draft} onChange={e=>setDraft(e.target.value)} placeholder={tr("输入中文或英文关键词…")}/>{draft&&<button type="button" className="icon-button" aria-label={tr("清除搜索")} onClick={()=>{setDraft('');navigate({q:''});}}><X size={17}/></button>}<button className="button primary" type="submit">{tr("搜索")}<ArrowRight size={15}/></button></form>
    <div className="search-filters"><span><ListFilter size={15}/>{tr("筛选")}</span><select aria-label={tr("搜索主题")} value={route.topic} onChange={e=>navigate({topic:e.target.value})}><option value="">{tr("全部主题")}</option>{topics.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}</select><select aria-label={tr("搜索项目")} value={route.filterProject} onChange={e=>navigate({filterProject:e.target.value})}><option value="">{tr("全部项目")}</option>{projects.filter(p=>!p.parent_id).map(p=><optgroup key={p.id} label={p.name}><option value={p.id}>{tr("主项目：")}{p.name}</option>{childrenFor(p,projects).map(child=><option key={child.id} value={child.id}>{tr("↳ 工作分项：")}{child.name}</option>)}</optgroup>)}</select><label>{tr("从")}<input type="date" aria-label={tr("搜索开始日期")} value={route.since} onChange={e=>navigate({since:e.target.value})}/></label><label>{tr("至")}<input type="date" aria-label={tr("搜索结束日期")} min={route.since} value={route.until} onChange={e=>navigate({until:e.target.value})}/></label>{hasFilter&&<button className="text-button" onClick={()=>navigate({topic:'',filterProject:'',since:'',until:''})}>{tr("清除筛选")}</button>}</div>
    {route.since&&route.until&&route.since>route.until&&<ErrorBox error={tr("开始日期应早于或等于结束日期。")}/>}{error&&<ErrorBox error={error}/>}
    {!route.q?<Empty title={tr("从一个关键词开始")} icon={Search}>{tr("可以搜索一个想法、一项决定或曾经遇到的问题。短中文词和中英文混合内容均可检索。")}</Empty>:loading&&!result?<Loading label={tr("正在检索本地内容…")}/>:result&&<div className={loading?'is-loading':''}><div className="search-result-caption">{tr("找到 ")}<strong>{result.total}</strong>{tr(" 项结果 ")}<span>{tr("关键词：")}{route.q}</span></div>{result.items.length?<div className="search-results">{result.items.map((item,i)=><article className="search-result" key={`${item.kind}-${item.id}-${i}`}><div className="result-top"><Badge>{item.kind==='project'&&projects.find(p=>p.id===(item.entity_id || item.project_id || item.id.replace(/^project:/,'')))?.parent_id?tr("工作分项"):itemLabels[item.kind] || item.kind}</Badge><time>{date(item.timestamp,true)}</time></div><button className="result-title" onClick={()=>item.thread_id?onSource(item.thread_id,item.message_id):item.kind==='project'?onProject(item.entity_id || item.project_id || item.id.replace(/^project:/,'')):item.project_id?onProject(item.project_id):navigate({view:'topics',topic:item.entity_id || item.id.replace(/^topic:/,'')})}>{item.title || tr("无标题内容")}<ArrowUpRight size={16}/></button><p><Highlighted text={item.text} query={route.q}/></p><SearchProjectContext item={item} projects={projects} onProject={onProject}/>{item.thread_id&&<SourceLink onClick={()=>onSource(item.thread_id!,item.message_id)}>{tr("定位原文")}</SourceLink>}</article>)}</div>:<Empty title={tr("没有找到匹配内容")} icon={Search}>{tr("尝试缩短关键词或放宽筛选。主题和项目筛选只涵盖已经完成分类的内容。")}</Empty>}<Pagination offset={offset} limit={30} total={result.total} onPage={setOffset}/></div>}
  </>;
}
function SearchProjectContext({item,projects,onProject}:{item:SearchItem;projects:Project[];onProject:(id:string)=>void}) {const id=item.kind==='project'?(item.entity_id || item.project_id || item.id.replace(/^project:/,'')):item.project_id;const project=projects.find(p=>p.id===id);if(!project?.parent_id)return null;const parentName=project.parent_name || projects.find(p=>p.id===project.parent_id)?.name || tr("所属主项目");return <div className="search-parent-context"><Layers3 size={12}/><span>{tr("工作分项：")}{project.name}</span><button className="text-button" onClick={()=>onProject(project.parent_id!)}>{tr("主项目：")}{parentName}<ArrowUpRight size={12}/></button></div>;}
function Highlighted({text,query}:{text:string;query:string}) {if(!query||!text)return <>{text}</>;const lower=text.toLowerCase(),needle=query.toLowerCase();let cursor=0;const parts:React.ReactNode[]=[];let count=0;while(cursor<text.length&&count<100){const pos=lower.indexOf(needle,cursor);if(pos<0){parts.push(text.slice(cursor));break;}parts.push(text.slice(cursor,pos));parts.push(<mark key={pos}>{text.slice(pos,pos+query.length)}</mark>);cursor=pos+query.length;count++;}if(count===100)parts.push(text.slice(cursor));return <>{parts}</>;}

function SourceDrawer({demo=false,source,onClose,onOpen,onSource}:{demo?:boolean;source:{id:string;focus?:string};onClose:()=>void;onOpen:(id:string)=>void;onSource:(id:string,focus?:string)=>void}) {
  const [thread,setThread]=useState<ThreadDetail|null>(null);const [error,setError]=useState('');const [offset,setOffset]=useState<number|null>(null);const [loading,setLoading]=useState(true);const bodyRef=useRef<HTMLDivElement>(null);const closeRef=useRef<HTMLButtonElement>(null);
  useEffect(()=>{setOffset(null);setThread(null);},[source.id,source.focus]);
  useEffect(()=>{let live=true;setLoading(true);const p=new URLSearchParams({limit:'60'});if(offset!==null)p.set('offset',String(offset));else if(source.focus)p.set('focus',source.focus);api<ThreadDetail>(`/threads/${encodeURIComponent(source.id)}?${p}`).then(t=>{if(live){setThread(t);setError('');}}).catch(e=>{if(live)setError(errorText(e));}).finally(()=>{if(live)setLoading(false);});return()=>{live=false;};},[source.id,source.focus,offset]);
  useEffect(()=>{if(!thread)return;requestAnimationFrame(()=>{const target=source.focus?bodyRef.current?.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(source.focus)}"]`):null;if(target)target.scrollIntoView({block:'center'});else bodyRef.current?.scrollTo({top:0});});},[thread,source.focus]);
  useModalKeyboard(onClose,closeRef);
  return <div className="overlay drawer-overlay" onMouseDown={e=>{if(e.target===e.currentTarget)onClose();}}><section className="source-drawer" role="dialog" aria-modal="true" aria-labelledby="source-title"><header className="drawer-header"><div><span className="eyebrow">{tr("原始对话 · 可追溯来源")}</span><h2 id="source-title">{thread?.title || tr("读取原对话")}</h2></div><button className="icon-button" ref={closeRef} aria-label={tr("关闭原文")} onClick={onClose}><X size={20}/></button></header><div className="drawer-toolbar"><span>{thread?.archived?tr("已归档"):tr("本地对话")}{thread&&tr(" · {0} 条消息", [thread.total])}</span><button className="button small" title={demo?tr('演示模式不会打开真实聊天'):undefined} disabled={demo || !thread?.available} onClick={()=>onOpen(source.id)}>{tr("在 Codex 中打开")}<ExternalLink size={13}/></button></div>{thread&&!thread.available&&<ErrorBox error={tr("原日志文件已不可用。下方是索引保留的历史内容，无法保证原应用能打开。")}/>}{source.focus&&<div className="source-focus-note"><FileText size={13}/>{!thread?tr("正在定位关联消息…"):thread.messages.some(m=>m.id===source.focus)?tr("已定位关联消息；周边上下文一并保留。"):tr("本页没有关联消息，请返回对应页或查看上下文。")}</div>}<div className="drawer-body" ref={bodyRef}>{error?<ErrorBox error={error}/>:!thread?<Loading/>:<>{thread.cwd&&<div className="source-path"><span>{tr("来源目录（不作为分类依据）")}</span><code>{thread.cwd}</code></div>}{loading&&<Loading label={tr("读取消息页…")}/>}{thread.messages.map((m,i)=><MessageBlock key={`${m.id}-${i}`} message={m} focused={m.id===source.focus}/>)}{thread.related_threads?.length>0&&<div className="related-threads"><h3>{tr("相关技术对话")}</h3>{thread.related_threads.map(t=>{const id=typeof t==='string'?t:t.id;return <button key={id} className="text-button" onClick={()=>onSource(id)}><MessageSquare size={13}/>{typeof t==='string'?t:t.title}<ArrowUpRight size={12}/></button>;})}</div>}</>}</div>{thread&&<div className="drawer-pagination"><Pagination offset={thread.offset} limit={60} total={thread.total} onPage={setOffset}/></div>}</section></div>;
}
function MessageBlock({message:m,focused}:{message:Message;focused:boolean}) {const tool=m.kind==='tool_output'||m.role==='tool';return <article data-message-id={m.id} className={`message-block ${m.role==='user'?'user-message':''} ${focused?'focused-message':''} ${tool?'tool-message':''}`}><header><span className="message-avatar">{m.role==='user'?tr("你"):tool?tr("工"):'AI'}</span><strong>{m.role==='user'?tr("你"):tool?tr("工具证据"):'Codex'}</strong><time>{date(m.timestamp,true)}</time><small>{tr("原文件第 ")}{m.line_no}{tr(" 行")}</small></header>{focused&&<div className="focused-label"><ArrowDown size={12}/>{tr("关联来源")}</div>}{tool?<details open={focused}><summary>{tr("查看工具输出")}</summary><pre>{m.text}</pre></details>:<div className="message-text">{m.text}</div>}</article>;}
function useModalKeyboard(onClose:()=>void,focusRef:React.RefObject<HTMLButtonElement|null>) {useEffect(()=>{const active=document.activeElement as HTMLElement|null;focusRef.current?.focus();const prev=document.body.style.overflow;document.body.style.overflow='hidden';const handler=(e:KeyboardEvent)=>{if(e.key==='Escape')onClose();if(e.key==='Tab'){const dialog=focusRef.current?.closest('[role="dialog"]');const els=dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary');if(!els?.length)return;const first=els[0],last=els[els.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}};document.addEventListener('keydown',handler);return()=>{document.body.style.overflow=prev;document.removeEventListener('keydown',handler);active?.focus();};},[]);}

function CorrectionModal({correction:c,projects,topics,onClose,onDone}:{correction:Correction;projects:Project[];topics:Topic[];onClose:()=>void;onDone:()=>void}) {
  const [name,setName]=useState(c.mode==='edit'?c.entity.name:'');const [goal,setGoal]=useState(c.mode==='edit'&&'goal'in c.entity?c.entity.goal:'');const [summary,setSummary]=useState(c.mode==='edit'&&'summary'in c.entity?c.entity.summary:'');const [stage,setStage]=useState(c.mode==='edit'&&'stage'in c.entity?c.entity.stage:'');const [selected,setSelected]=useState<string[]>([]);const [projectId,setProjectId]=useState(c.mode==='assign'?c.segment.project_id||'':'');const [topicIds,setTopicIds]=useState<string[]>(c.mode==='assign'?c.segment.topic_ids||[]:[]);const [segments,setSegments]=useState<Segment[]>([]);const [loading,setLoading]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState('');const closeRef=useRef<HTMLButtonElement>(null);
  useModalKeyboard(onClose,closeRef);
  useEffect(()=>{if(c.mode!=='split')return;let live=true;setLoading(true);api<Project|Topic>(`/${c.kind}/${encodeURIComponent(c.entity.id)}`).then(entity=>{if(live)setSegments(entity.segments||[]);}).catch(e=>{if(live)setError(errorText(e));}).finally(()=>{if(live)setLoading(false);});return()=>{live=false;};},[c]);
  const noun=c.mode!=='assign'?(c.kind==='projects'?(('parent_id' in c.entity&&c.entity.parent_id)?tr("工作分项"):tr("项目")):tr("主题")):'';
  const mergeCandidates=c.mode==='merge'?(c.kind==='topics'?topics.filter(item=>item.id!==c.entity.id):projects.filter(item=>item.id!==c.entity.id&&!item.child_count&&!item.children?.length&&(item.parent_id||null)===((c.entity as Project).parent_id||null))):[];
  const title=c.mode==='edit'?tr("编辑{0}", [noun]):c.mode==='merge'?tr("合并{0}", [noun]):c.mode==='split'?tr("拆分{0}", [noun]):tr("调整片段归属");
  const toggle=(id:string)=>setSelected(old=>old.includes(id)?old.filter(x=>x!==id):[...old,id]);
  const submit=async(e:React.FormEvent)=>{e.preventDefault();setBusy(true);setError('');try{if(c.mode==='edit'){const changes:Record<string,string>={};if(name.trim()!==c.entity.name)changes.name=name.trim();if(c.kind==='projects'&&'goal' in c.entity){if(goal!==c.entity.goal)changes.goal=goal;if(summary!==c.entity.summary)changes.summary=summary;if(stage!==c.entity.stage)changes.stage=stage;}if(!Object.keys(changes).length){onClose();return;}await patch(`/${c.kind}/${encodeURIComponent(c.entity.id)}`,changes);}else if(c.mode==='merge'){await post(`/${c.kind}/merge`,{source_ids:selected,target_id:c.entity.id});}else if(c.mode==='split'){await post(`/${c.kind}/${encodeURIComponent(c.entity.id)}/split`,{name:name.trim(),segment_ids:selected});}else if(c.mode==='assign'){await patch(`/segments/${encodeURIComponent(c.segment.id)}`,{project_id:projectId||null,topic_ids:topicIds});}onDone();}catch(e){setError(errorText(e));}finally{setBusy(false);}};
  const valid=c.mode==='assign'?(!!projectId||topicIds.length>0):(c.mode==='edit'?!!name.trim():c.mode==='merge'?selected.length>0:!!name.trim()&&selected.length>0);
  return <div className="overlay modal-overlay" onMouseDown={e=>{if(e.target===e.currentTarget&&!busy)onClose();}}><section className="correction-modal" role="dialog" aria-modal="true" aria-labelledby="correction-title"><header><div><span className="eyebrow">{tr("人工修正 · 后续更新保留")}</span><h2 id="correction-title">{title}</h2></div><button ref={closeRef} className="icon-button" aria-label={tr("关闭编辑")} disabled={busy} onClick={onClose}><X size={20}/></button></header><form onSubmit={submit}><div className="modal-body">{error&&<ErrorBox error={error}/>}
    {c.mode==='edit'&&<><label className="form-field">{noun}{tr("名称")}<input autoComplete="off" required value={name} onChange={e=>setName(e.target.value)}/></label>{c.kind==='projects'&&<><label className="form-field">{tr("明确目标")}<textarea rows={2} value={goal} onChange={e=>setGoal(e.target.value)}/></label><label className="form-field">{tr("当前阶段")}<input value={stage} onChange={e=>setStage(e.target.value)}/></label><label className="form-field">{tr("项目摘要")}<textarea rows={5} value={summary} onChange={e=>setSummary(e.target.value)}/></label></>}<p className="form-help">{tr("你修改的字段会保留，自动分析不会覆盖人工填写的内容。")}</p></>}
    {c.mode==='merge'&&<><div className="merge-target"><GitMerge size={20}/><div><small>{tr("合并到当前")}{noun}</small><strong>{c.entity.name}</strong></div></div><p className="form-help">{tr('选择要归入“{0}”的{1}。相关内容会迁移，旧名称保留关联，避免再次自动创建。', [c.entity.name,noun])}{c.kind==='projects'&&tr("仅列出同一层级、同一主项目下且没有下属分项的条目。")}</p><div className="check-list">{mergeCandidates.map(x=><label key={x.id}><input type="checkbox" checked={selected.includes(x.id)} onChange={()=>toggle(x.id)}/><span>{x.name}</span></label>)}</div>{mergeCandidates.length===0&&<p className="muted">{c.kind==='topics'?tr("暂时没有其他可合并的主题。"):tr("暂时没有同一主项目下可合并的工作分项，或其他无分项的独立项目。")}</p>}</>}
    {c.mode==='split'&&<><label className="form-field">{tr("新")}{noun}{tr("名称")}<input required value={name} onChange={e=>setName(e.target.value)} placeholder={tr("从“{0}”中拆出什么？", [c.entity.name])}/></label><p className="form-help">{tr("选中的片段会移到新")}{noun}{tr("中。")}{c.kind==='projects'&&'parent_id' in c.entity&&c.entity.parent_id?tr("新分项仍归属同一主项目。"):''}{tr("选择依据是内容与目标，原始对话保持完整。")}</p>{loading?<Loading/>:<><div className="split-select-all"><span>{tr("已选择 ")}{selected.length} / {segments.length}{tr(" 个片段")}</span><button type="button" className="text-button" onClick={()=>setSelected(selected.length===segments.length?[]:segments.map(s=>s.id))}>{selected.length===segments.length?tr("取消全选"):tr("全选")}</button></div><div className="check-list segment-check-list">{segments.map(s=><label key={s.id}><input type="checkbox" checked={selected.includes(s.id)} onChange={()=>toggle(s.id)}/><span><strong>{s.thread_title}</strong><small>{s.summary}</small></span></label>)}</div>{!segments.length&&<p className="muted">{tr("当前没有可拆分的片段。")}</p>}</>}</>}
    {c.mode==='assign'&&<><blockquote className="assignment-preview">{c.segment.summary}</blockquote><label className="form-field">{tr("所属项目或工作分项")}<select value={projectId} onChange={e=>setProjectId(e.target.value)}><option value="">{tr("仅归主题（无明确项目）")}</option>{projects.filter(p=>!p.parent_id).map(p=><optgroup key={p.id} label={p.name}><option value={p.id}>{tr("主项目：")}{p.name}</option>{childrenFor(p,projects).map(child=><option key={child.id} value={child.id}>{tr("↳ 工作分项：")}{child.name}</option>)}</optgroup>)}</select></label><label className="form-field">{tr("关联主题（可多选）")}</label><div className="check-list">{topics.map(t=><label key={t.id}><input type="checkbox" checked={topicIds.includes(t.id)} onChange={()=>setTopicIds(old=>old.includes(t.id)?old.filter(x=>x!==t.id):[...old,t.id])}/><span>{t.name}</span></label>)}</div><p className="form-help">{tr("至少保留一个项目或主题，便于后续查找和再次调整。该片段的归属会锁定为你的选择，自动更新不会重新分配。")}</p></>}
    </div><footer><button type="button" className="button" disabled={busy} onClick={onClose}>{tr("取消")}</button><button className="button primary" disabled={busy||loading||!valid} type="submit">{busy?<LoaderCircle size={15} className="spin"/>:<Check size={15}/>} {busy?tr("保存中…"):c.mode==='merge'?tr("合并 {0} 个{1}", [selected.length, noun]):c.mode==='split'?tr("创建并移动片段"):tr("保存修改")}</button></footer></form></section></div>;
}


function GroupingModal({project,projects,onClose,onDone}:{project:Project;projects:Project[];onClose:()=>void;onDone:()=>void}) {
  const roots=projects.filter(item=>!item.parent_id&&item.id!==project.id);
  const [mode,setMode]=useState<'existing'|'new'|'remove'>(roots.length?'existing':'new');
  const [parentId,setParentId]=useState(project.parent_id || '');
  const [name,setName]=useState('');const [goal,setGoal]=useState('');
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const closeRef=useRef<HTMLButtonElement>(null);
  useModalKeyboard(onClose,closeRef);
  const valid=mode==='new'?!!name.trim():mode==='existing'?!!parentId:!!project.parent_id;
  const submit=async(event:React.FormEvent)=>{event.preventDefault();if(!valid)return;setBusy(true);setError('');try{
    if(mode==='new')await post('/projects/group',{name:name.trim(),goal:goal.trim(),project_ids:[project.id]});
    else if(mode==='existing'&&parentId===project.parent_id){onClose();return;}
    else await patch(`/projects/${encodeURIComponent(project.id)}/parent`,{parent_id:mode==='remove'?null:parentId});
    onDone();
  }catch(e){setError(errorText(e));}finally{setBusy(false);}};
  return <div className="overlay modal-overlay" onMouseDown={e=>{if(e.target===e.currentTarget&&!busy)onClose();}}><section className="correction-modal" role="dialog" aria-modal="true" aria-labelledby="group-title"><header><div><span className="eyebrow">{tr("主项目 · 工作分项")}</span><h2 id="group-title">{project.parent_id?tr("调整所属主项目"):tr("归入主项目")}</h2></div><button ref={closeRef} className="icon-button" aria-label={tr("关闭归属调整")} disabled={busy} onClick={onClose}><X size={20}/></button></header><form onSubmit={submit}><div className="modal-body">
    <div className="grouping-current"><Layers3 size={20}/><div><strong>{project.name}</strong><span>{project.parent_id?tr("当前是“{0}”的工作分项", [project.parent_name || projects.find(p=>p.id===project.parent_id)?.name || tr("主项目")]):tr("当前在总览中作为独立项目展示")}</span></div></div>
    <div className="group-mode-tabs" role="tablist" aria-label={tr("选择归属方式")}><button type="button" role="tab" aria-selected={mode==='existing'} className={mode==='existing'?'active':''} onClick={()=>setMode('existing')}>{tr("已有主项目")}</button><button type="button" role="tab" aria-selected={mode==='new'} className={mode==='new'?'active':''} onClick={()=>setMode('new')}>{tr("新建主项目")}</button>{project.parent_id&&<button type="button" role="tab" aria-selected={mode==='remove'} className={mode==='remove'?'active':''} onClick={()=>setMode('remove')}>{tr("移出为独立项目")}</button>}</div>
    {error&&<ErrorBox error={error}/>}
    {mode==='existing'&&<><label className="form-field">{tr("选择共同目标所在的主项目")}<select value={parentId} onChange={e=>setParentId(e.target.value)}><option value="">{tr("请选择主项目")}</option>{roots.map(root=><option value={root.id} key={root.id}>{root.name}{root.child_count?tr("（{0} 个分项）", [root.child_count]):''}</option>)}</select></label>{!roots.length&&<p className="form-help">{tr("还没有其他主项目，可以切换到“新建主项目”。")}</p>}<p className="form-help">{tr("保存后，该条目作为工作分项显示在选定主项目中；它的历史、来源和人工修正继续保留。")}</p></>}
    {mode==='new'&&<><label className="form-field">{tr("主项目名称")}<input required value={name} onChange={e=>setName(e.target.value)} placeholder={tr("概括共同目标，例如：社区花园规划")}/></label><label className="form-field">{tr("共同目标")}<textarea rows={3} value={goal} onChange={e=>setGoal(e.target.value)} placeholder={tr("这些工作共同服务于什么目标？")}/></label><p className="form-help">{tr('新建主项目后，“{0}”成为其中的工作分项。其他相关项目也可以通过“归入主项目”加入。', [project.name])}</p></>}
    {mode==='remove'&&<div className="group-remove-note"><FolderKanban size={21}/><p>{tr('“{0}”将重新作为独立项目显示在总览中。原有历史和来源会保留，并从当前主项目的汇总中移出。', [project.name])}</p></div>}
    </div><footer><button type="button" className="button" disabled={busy} onClick={onClose}>{tr("取消")}</button><button className="button primary" type="submit" disabled={busy||!valid}>{busy?<LoaderCircle className="spin" size={15}/>:<Check size={15}/>} {busy?tr("保存中…"):mode==='new'?tr("创建并归入"):mode==='remove'?tr("移出为独立项目"):tr("保存归属")}</button></footer></form></section></div>;
}


type AnalysisSettings = {parallelism: number; model: string | null; reasoning_effort: string | null; catalog_updated_at?: string; models: {id:string; name:string; efforts:string[]; default_effort:string}[]};
const effortLabels: Record<string,string> = {none:'无', minimal:'最低', low:'低', medium:'中等', high:'高', xhigh:'很高', max:'最高', ultra:'Ultra'};
function ModelSettingsModal({demo=false,onClose,onSaved}:{demo?:boolean;onClose:()=>void;onSaved:()=>void}) {
  const [settings,setSettings] = useState<AnalysisSettings | null>(null);
  const [model,setModel] = useState('');
  const [effort,setEffort] = useState('');
  const [parallelism,setParallelism] = useState(3);
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  useModalKeyboard(onClose,closeRef);
  const load = useCallback(async()=>{try {setError(''); const value = await api<AnalysisSettings>('/analysis/settings'); setSettings(value); setModel(value.model || ''); setEffort(value.reasoning_effort || ''); setParallelism(value.parallelism);} catch(e) {setError(errorText(e));}},[]);
  useEffect(()=>{void load();},[load]);
  const selected = settings?.models.find(item=>item.id===model);
  const valid = selected?.efforts.includes(effort);
  const selectModel = (id:string) => {setModel(id); const next=settings?.models.find(item=>item.id===id); if(next&&!next.efforts.includes(effort)) setEffort(next.default_effort);};
  const save = async(e:React.FormEvent) => {e.preventDefault(); if(demo)return; setBusy(true); setError(''); try {await patch('/analysis/settings',{model,reasoning_effort:effort,parallelism}); onSaved();} catch(e) {setError(errorText(e));} finally {setBusy(false);}};
  return <div className="overlay modal-overlay" onMouseDown={e=>{if(e.target===e.currentTarget&&!busy)onClose();}}><section className="correction-modal" role="dialog" aria-modal="true" aria-labelledby="model-settings-title">
    <header><div><span className="eyebrow">{tr("后台整理")}</span><h2 id="model-settings-title">{tr("模型设置")}</h2></div><button className="icon-button" ref={closeRef} aria-label={tr("关闭模型设置")} onClick={onClose}><X size={19}/></button></header>
    <form onSubmit={save}><div className="modal-body">
      {demo&&<p className="mode-notice">{tr('演示模式只显示模型设置，不会保存配置或调用 AI。')}</p>}<p className="form-help">{tr("选择后续批次使用的模型和推理强度。正在运行的一批继续使用原配置，已整理的内容不会重新分析。")}</p>
      {error&&<ErrorBox error={error} retry={load}/>}
      {!settings&&!error&&<Loading label={tr("正在读取模型列表…")}/>}
      {settings&&<><label className="form-field">{tr("模型")}<select value={model} disabled={demo||busy} onChange={e=>selectModel(e.target.value)}><option value="" disabled>{tr("请选择模型")}</option>{model&&!selected&&<option value={model} disabled>{model}{tr("（当前列表中不可用）")}</option>}{settings.models.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label className="form-field">{tr("推理强度")}<select value={effort} disabled={demo||busy||!selected} onChange={e=>setEffort(e.target.value)}><option value="" disabled>{tr("请选择强度")}</option>{effort&&!valid&&<option value={effort} disabled>{effort}{tr("（不支持）")}</option>}{selected?.efforts.map(item=><option key={item} value={item}>{tr(effortLabels[item] || item)} · {item}</option>)}</select></label>
      <label className="form-field">{tr("同时分析的批次数")}<select value={parallelism} disabled={demo||busy} onChange={e=>setParallelism(Number(e.target.value))}>{[1,2,3,4,5,6].map(n=><option key={n} value={n}>{n===1?tr('1 · 串行'):tr('{0} · 并行', [n])}</option>)}</select></label>
      <p className="form-help">{tr("默认 3 路。不同聊天可同时整理，同一聊天依次处理。提高并行数会更快消耗额度；降低时会等待已开始的批次完成。")}</p>
      <p className="form-help">{tr("只保存到本工具，不影响其他 Codex 对话。模型列表来自本机 Codex；推理强度越高，通常需要更多时间与额度。")}</p>
      <p className="form-help">{tr("列表更新时间：")}{date(settings.catalog_updated_at,true)}</p></>}
    </div><footer><button type="button" className="button" disabled={busy} onClick={onClose}>{tr("取消")}</button><button className="button primary" disabled={demo||busy||!valid} type="submit">{busy?<LoaderCircle size={15} className="spin"/>:<Check size={15}/>} {busy?tr("保存中…"):tr("保存，下一批生效")}</button></footer></form>
  </section></div>;
}
