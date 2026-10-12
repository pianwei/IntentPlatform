import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowLeft, Check, ChevronDown, Clock3, Download, GitBranch, Layers3, MessageSquare, RefreshCw, Search, UserRound } from "lucide-react";
import { ApiClient, ApiError, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";
import { BusinessResultContent } from "../../shared/BusinessResult";
import type { Agent, RuntimeFile, RuntimeStepSummary, RuntimeTimingSummary } from "../../shared/types";
import { Help, PageTitle, Status, SearchSelect } from "./ModernUI";

type ConversationRow = { id: string; title: string; agent_name: string; agent_id: string; updated_at: string; turn_count: number; status: string };
type ConversationPage = { items: ConversationRow[]; total: number; offset: number; limit: number };
type Turn = { run_id: string; question: string; status: string; created_at: string; timing: RuntimeTimingSummary;
  results: Record<string, unknown>[]; prompts: { kind: string; created_at: string; payload: Record<string, unknown> }[];
  interactions: { kind: string; created_at: string; detail: Record<string, unknown> }[];
  phases: { layer: string; count: number; status: string }[] };
type ConversationDetail = { id: string; title: string; agent_name: string; turn_count: number; turns: Turn[]; offset: number; limit: number };
type TraceEntry = { title: string; display: { label: string; value: unknown }[]; created_at: string };
type TraceDetail = { layers: Record<string, TraceEntry[]>; steps: RuntimeStepSummary[];
  attempts: { id: string; kind: string; status: string; total_ms: number | null; model_ms: number | null; connector_ms: number | null }[] };
const layerNames: Record<string,string> = { entity: "实体层", dynamics: "动力层", planning: "策划层", execution: "执行结果" };
const statusNames: Record<string,string> = { RECEIVED: "已受理", ROUTING: "识别中", WAITING_INPUT: "待补充", WAITING_CONFIRMATION: "待确认", EXECUTING: "执行中", SUCCEEDED: "已完成", FAILED: "失败", UNKNOWN: "待核对", CANCELLED: "已取消", EMPTY: "暂无问答" };
function elapsed(v: number | null | undefined) { return typeof v === "number" && Number.isFinite(v) ? v < 1000 ? `${Math.round(v)} 毫秒` : `${(v/1000).toFixed(2)} 秒` : "未记录"; }
function stamp(v: string) { return new Date(v).toLocaleString("zh-CN", { month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit" }); }
function label(v: string) { return statusNames[v] ?? "已记录"; }
function Fact({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) return <>未记录</>;
  if (typeof value === "boolean") return <>{value ? "是" : "否"}</>;
  if (typeof value !== "object") return <>{String(value)}</>;
  if (depth > 3) return <>内容层级较深</>;
  if (Array.isArray(value)) return value.length ? <ul className="fact-list">{value.map((v,i)=><li key={i}><Fact value={v} depth={depth+1}/></li>)}</ul> : <>无</>;
  return <dl>{Object.entries(value).map(([k,v])=><div key={k}><dt>{k}</dt><dd><Fact value={v} depth={depth+1}/></dd></div>)}</dl>;
}
function Results({ results, download }: { results: Record<string,unknown>[]; download:(file:RuntimeFile)=>Promise<void> }) {
  const [busy,setBusy]=useState(""); const [error,setError]=useState("");
  return <>{!results.length && <p className="muted">本轮尚无已记录的最终答复。</p>}{results.map((r,i)=><div className="log-result" key={i}>
    <BusinessResultContent data={r}/>
    {r.result_unavailable!==true&&Array.isArray(r.components)&&r.components.length>0&&<details open={!["text","markdown"].includes(String(r.component))||typeof r.data!=="string"||!r.data.trim()}><summary>业务明细 · {r.components.length}</summary>{r.components.map((c:Record<string,unknown>,j)=><section className="final-result-component" key={j}><h4>{String(c.title??"业务结果")}</h4><BusinessResultContent data={c}/></section>)}</details>}
    {(Array.isArray(r.files)?r.files as RuntimeFile[]:[]).map(f=><button className="button log-file" key={f.id} disabled={!!busy} onClick={()=>{setBusy(f.id);setError("");void download(f).catch(e=>setError(errorText(e))).finally(()=>setBusy(""));}}><Download size={14}/>{f.filename}{busy===f.id?" · 正在读取…":""}</button>)}
  </div>)}{error&&<Notice kind="error">{error}</Notice>}</>;
}
function InteractionHistory({ turn }: { turn:Turn }) {
  const entries = [...turn.prompts.map(p=>({at:p.created_at,title:p.kind==="QUESTION"?"系统询问":p.kind==="CONFIRMATION"?"请求确认":"异常说明",value:p.payload})),
    ...turn.interactions.map(p=>({at:p.created_at,title:p.kind==="confirmation"?p.detail.confirmed?"用户确认":"用户拒绝":"用户补充",value:p.detail}))].sort((a,b)=>a.at.localeCompare(b.at));
  if (!entries.length) return null;
  return <details className="interaction-history"><summary>补充与确认记录 · {entries.length}</summary>{entries.map((e,i)=><section key={i}><strong>{e.title}</strong><time>{stamp(e.at)}</time>
    {typeof e.value.message==="string"&&<p>{e.value.message}</p>}{e.value.values!==undefined&&<Fact value={e.value.values}/>}{e.value.selection!==undefined&&<Fact value={e.value.selection}/>}
    {Array.isArray(e.value.actions)&&e.value.actions.map((a:Record<string,unknown>,j)=><div key={j}><b>{String(a.name??"业务操作")}</b><BusinessResultContent data={{data:a.arguments}}/></div>)}
    {Array.isArray(e.value.fields)&&e.value.fields.length>0&&<p>待补充：{e.value.fields.map((f:{label?:string;name:string})=>f.label||f.name).join("、")}</p>}
    {Array.isArray(e.value.options)&&e.value.options.length>0&&<p>可选对象：{e.value.options.map((o:{label:string})=>o.label).join("、")}</p>}
  </section>)}{turn.prompts.length>0&&!turn.interactions.length&&<p className="muted">本轮未记录用户交互明细。</p>}</details>;
}
function PhaseDetail({ trace, layer, onFunction }: { trace:TraceDetail; layer:string; onFunction?:(name:string)=>void }) {
  const entries=trace.layers[layer]??[];
  return <>{entries.map((e,i)=><section className="trace-fact-entry" key={i}><div className="trace-entry-heading"><strong>{e.title}</strong><time>{stamp(e.created_at)}</time></div><dl>{e.display.map((f,j)=><div key={j}><dt>{f.label}</dt><dd><Fact value={f.value}/></dd></div>)}</dl></section>)}
    {layer==="execution"&&trace.steps.map((step,i)=><details className="business-step" key={i}><summary>{step.name} · {label(step.status)} · {elapsed(step.elapsed_ms)}</summary>{onFunction&&<button className="text-button" onClick={()=>onFunction(step.name)}>打开功能配置</button>}<h4>输入</h4><BusinessResultContent data={{data:step.input}}/><h4>输出</h4><BusinessResultContent data={{data:step.output}}/></details>)}
    {!entries.length&&!(layer==="execution"&&trace.steps.length)&&<p className="muted">此阶段未记录详情。</p>}
  </>;
}
export function RunTracePage({ api, workspace, admin, agents=[], conversationId, onConversation, agentFilter, onAgent, onFunction }: { api:ApiClient; workspace:string; admin:boolean; agents?:Agent[]; conversationId?:string; onConversation?:(id?:string)=>void; agentFilter?:string; onAgent?:(id:string)=>void; onFunction?:(name:string)=>void }) {
  const [query,setQuery]=useState(""); const [agent,setAgent]=useState(agentFilter??""); const [status,setStatus]=useState(""); const [period,setPeriod]=useState("");
  const [offset,setOffset]=useState(0); const [page,setPage]=useState<ConversationPage>({items:[],total:0,offset:0,limit:25});
  const [detail,setDetail]=useState<ConversationDetail|null>(null); const [turnIndex,setTurnIndex]=useState(0);
  const [selected,setSelected]=useState(""); const [mobileDetail,setMobileDetail]=useState(false); const [traceOnly,setTraceOnly]=useState(false);
  const [error,setError]=useState(""); const [detailError,setDetailError]=useState(""); const [traceError,setTraceError]=useState("");
  const [loading,setLoading]=useState(false); const [detailLoading,setDetailLoading]=useState(false); const [traceLoading,setTraceLoading]=useState(false);
  const [trace,setTrace]=useState<TraceDetail|null>(null); const [expanded,setExpanded]=useState<string[]>([]); const [refresh,setRefresh]=useState(0);
  const listEpoch=useRef(0),detailEpoch=useRef(0),traceEpoch=useRef(0); const selectionRef=useRef("");
  const scrollNode=useRef<HTMLDivElement>(null),scrollPositions=useRef<Record<string,number>>({}),phaseMemory=useRef<Record<string,string[]>>({});
  const phaseNodes=useRef<Record<string,HTMLDetailsElement|null>>({});
  const conversationRef=useRef(conversationId);conversationRef.current=conversationId;
  const base=`${management(workspace)}/conversations`;
  const turn=detail?.turns[turnIndex];
  useEffect(()=>{setSelected("");selectionRef.current="";setDetail(null);setOffset(0);scrollPositions.current={};phaseMemory.current={};return()=>{listEpoch.current++;detailEpoch.current++;traceEpoch.current++;};},[workspace]);
  useEffect(()=>{setAgent(agentFilter??"");},[agentFilter,workspace]);
  useLayoutEffect(()=>{if(turn&&scrollNode.current)scrollNode.current.scrollTop=scrollPositions.current[`${turn.run_id}:${traceOnly}`]??0;},[turn?.run_id,traceOnly,detail,trace]);
  useEffect(()=>{
    if (!admin) return;
    const epoch=++listEpoch.current; const controller=new AbortController();setLoading(true);setError("");
    const timer=setTimeout(()=>{const params=new URLSearchParams({limit:"25",offset:String(offset)});
      if(query.trim())params.set("query",query.trim()); if(agent)params.set("agent_id",agent); if(status)params.set("status",status);
      if(period){const date=new Date();date.setDate(date.getDate()-Number(period));params.set("created_after",date.toISOString());}
      void api.get<ConversationPage>(`${base}?${params}`,controller.signal).then(v=>{if(epoch!==listEpoch.current)return;setPage(v);
        if(!conversationRef.current&&selectionRef.current&&!v.items.some(c=>c.id===selectionRef.current)){detailEpoch.current++;setDetail(null);setSelected("");selectionRef.current="";setMobileDetail(false);}
      }).catch(e=>{if(epoch===listEpoch.current&&!controller.signal.aborted)setError(errorText(e));}).finally(()=>{if(epoch===listEpoch.current)setLoading(false);});
    },query?220:0);
    return()=>{clearTimeout(timer);controller.abort();};
  },[api,base,admin,query,agent,status,period,offset,refresh]);
  useEffect(()=>{setTrace(null);setTraceError("");setTraceLoading(false);setExpanded(turn?(phaseMemory.current[turn.run_id]??turn.phases.filter(p=>p.status==="FAILED").map(p=>p.layer)):[]);return()=>{traceEpoch.current++;};},[turn?.run_id]);
  useEffect(()=>{
    if(!turn||!expanded.length||trace||traceLoading||traceError)return;
    const epoch=++traceEpoch.current;setTraceLoading(true);
    void api.get<TraceDetail>(`${management(workspace)}/runs/${encodeURIComponent(turn.run_id)}/trace`).then(v=>{if(epoch===traceEpoch.current)setTrace(v);}).catch(e=>{if(epoch===traceEpoch.current)setTraceError(errorText(e));}).finally(()=>{if(epoch===traceEpoch.current)setTraceLoading(false);});
  },[api,workspace,turn?.run_id,expanded,trace,traceLoading,traceError]);
  async function open(id:string,turnOffset=0,preserve=false){
    if(!admin)return;
    const chosen=preserve?turn?.run_id:undefined;
    const distance=preserve&&detail?detail.offset+detail.turns.length-1-turnIndex:0;
    const oldCount=detail?.turn_count??0;
    const epoch=++detailEpoch.current;traceEpoch.current++;setTraceLoading(false);selectionRef.current=id;setSelected(id);setDetailLoading(true);setDetailError("");setMobileDetail(true);
    if(!preserve){setDetail(null);setTraceOnly(false);setTrace(null);}
    try{
      let v=await api.get<ConversationDetail>(`${base}/${encodeURIComponent(id)}?limit=25&offset=${turnOffset}`);
      if(epoch!==detailEpoch.current)return;
      // New turns can move the selected turn across a server page boundary.
      if(chosen&&!v.turns.some(t=>t.run_id===chosen)&&v.turn_count>=oldCount){
        const adjusted=Math.floor((distance+v.turn_count-oldCount)/25)*25;
        if(adjusted!==v.offset&&adjusted<v.turn_count)v=await api.get<ConversationDetail>(`${base}/${encodeURIComponent(id)}?limit=25&offset=${adjusted}`);
      }
      if(epoch!==detailEpoch.current)return;
      const index=chosen?v.turns.findIndex(t=>t.run_id===chosen):-1;
      setDetail(v);setTurnIndex(index>=0?index:Math.max(0,v.turns.length-1));
      if(preserve&&chosen&&index>=0&&expanded.length){
        const traceVersion=++traceEpoch.current;setTraceLoading(true);setTraceError("");
        try{const value=await api.get<TraceDetail>(`${management(workspace)}/runs/${encodeURIComponent(chosen)}/trace`);if(epoch===detailEpoch.current&&traceVersion===traceEpoch.current)setTrace(value);}
        catch(e){if(epoch===detailEpoch.current&&traceVersion===traceEpoch.current)setTraceError(errorText(e));}
        finally{if(traceVersion===traceEpoch.current)setTraceLoading(false);}
      }
    }
    catch(e){if(epoch===detailEpoch.current){setDetailError(errorText(e));if(e instanceof ApiError&&[401,403,404].includes(e.status)){setDetail(null);setTrace(null);}}}
    finally{if(epoch===detailEpoch.current)setDetailLoading(false);}
  }
  useEffect(()=>{if(!admin){detailEpoch.current++;traceEpoch.current++;setDetail(null);setTrace(null);setSelected("");selectionRef.current="";return;}if(conversationId){void open(conversationId);}else if(onConversation){setSelected("");selectionRef.current="";setDetail(null);setMobileDetail(false);}},[conversationId,admin,workspace]);
  function filter(setter:(v:string)=>void,v:string){onConversation?.();setter(v);setOffset(0);detailEpoch.current++;setDetail(null);selectionRef.current="";setSelected("");setMobileDetail(false);}
  function reload(){setRefresh(v=>v+1);if(selected)void open(selected,detail?.offset??0,true);}
  function expand(layer:string,isOpen:boolean){if(!turn)return;setExpanded(old=>{const next=isOpen?(old.includes(layer)?old:[...old,layer]):old.filter(x=>x!==layer);phaseMemory.current[turn.run_id]=next;return next;});}
  function locateFailure(layer:string){setTraceOnly(true);expand(layer,true);requestAnimationFrame(()=>{const node=phaseNodes.current[layer];node?.scrollIntoView({block:"start",behavior:"auto"});node?.querySelector("summary")?.focus();});}
  async function download(file:RuntimeFile){const blob=await api.blob(`${base}/${encodeURIComponent(selected)}/files/${encodeURIComponent(file.id)}`);const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download=file.filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  if(!admin)return <Notice>需要当前空间的管理员权限。</Notice>;
  return <section className="log-page">
    <PageTitle title="运行日志" help="一次会话包含多轮问答。默认查看最终答复，需要时展开各阶段的处理记录。"><button className="button" disabled={loading||detailLoading} onClick={reload}><RefreshCw size={15}/>刷新</button></PageTitle>
    <div className="log-filters"><label className="search-box"><Search size={16}/><input aria-label="搜索会话" placeholder="搜索会话标题…" value={query} onChange={e=>filter(setQuery,e.target.value)}/></label>
      <SearchSelect label="筛选智能体" value={agent} onChange={v=>filter(setAgent,v)} options={[{value:'',label:'全部智能体'},...agents.map(a=>({value:a.id,label:a.name}))]}/>
      <SearchSelect label="筛选状态" value={status} onChange={v=>filter(setStatus,v)} options={[{value:'',label:'全部状态'},...Object.entries(statusNames).filter(([k])=>k!=='EMPTY').map(([value,label])=>({value,label}))]}/>
      <SearchSelect label="筛选时间" value={period} onChange={v=>filter(setPeriod,v)} options={[{value:'',label:'全部时间'},{value:'1',label:'最近一天'},{value:'7',label:'最近七天'},{value:'30',label:'最近三十天'}]}/><span className="result-count">{page.total} 个会话</span>
    </div>{error&&<Notice kind="error">{error}</Notice>}
    <div className={`log-workbench ${mobileDetail?"show-detail":""}`}>
      <aside className="conversation-list" aria-label="会话列表" aria-busy={loading}><div className="list-heading">会话<span>最近活动</span></div>
        {!page.items.length&&<div className="empty-state"><MessageSquare size={26}/><p>{loading?"正在读取…":"没有匹配的会话"}</p></div>}
        {page.items.map(c=><button key={c.id} className={`conversation-item ${selected===c.id?"selected":""}`} aria-current={selected===c.id?"true":undefined} onClick={()=>onConversation?onConversation(c.id):void open(c.id)}><div className="conversation-item-top"><MessageSquare size={15}/><strong>{c.title}</strong></div><p>{stamp(c.updated_at)}</p><div className="conversation-item-bottom"><span>{c.agent_name} · {c.turn_count} 轮</span><Status value={label(c.status)}/></div></button>)}
        <div className="list-footer log-pagination"><button className="text-button" disabled={offset===0||loading} onClick={()=>setOffset(Math.max(0,offset-25))}>上一页</button><span>{page.total?`${offset+1}–${Math.min(offset+25,page.total)}`:"0"} / {page.total}</span><button className="text-button" disabled={offset+25>=page.total||loading} onClick={()=>setOffset(offset+25)}>下一页</button></div>
      </aside>
      {selected?<article className="conversation-detail" aria-label="会话详情" aria-busy={detailLoading}><header className="detail-header"><button className="icon-button mobile-only" aria-label="返回会话列表" onClick={()=>onConversation?onConversation():setMobileDetail(false)}><ArrowLeft size={18}/></button><div><h2>{detail?.title??(detailLoading?"正在读取会话…":"会话详情")}</h2>{detail&&<div className="detail-meta"><button className="text-button" onClick={()=>{const a=agents.find(a=>a.name===detail.agent_name);if(a)onAgent?.(a.id);}}><Layers3 size={13}/>{detail.agent_name}</button><span>{detail.turn_count} 轮问答</span></div>}</div></header>
        {detailError&&<Notice kind="error">{detailError}</Notice>}{detail&&<><div className="detail-tabs-row"><div className="turn-tabs" role="tablist" aria-label="问答轮次">
          {detail.offset+detail.turns.length<detail.turn_count&&<button disabled={detailLoading} onClick={()=>void open(selected,detail.offset+25)}>更早问答</button>}
          {detail.turns.map((t,i)=><button disabled={detailLoading} key={t.run_id} role="tab" aria-selected={turnIndex===i} className={turnIndex===i?"active":""} onClick={()=>{setTurnIndex(i);setTraceOnly(false);}}>第 {detail.turn_count-detail.offset-detail.turns.length+i+1} 轮</button>)}
          {detail.offset>0&&<button disabled={detailLoading} onClick={()=>void open(selected,Math.max(0,detail.offset-25))}>较新问答</button>}</div>{turn&&<button className="trace-shortcut" aria-pressed={traceOnly} onClick={()=>setTraceOnly(!traceOnly)}>{traceOnly?<MessageSquare size={13}/>:<GitBranch size={13}/>} {traceOnly?"返回问答":"查看处理过程"}</button>}</div>
          {turn?<div className="detail-scroll" ref={scrollNode} onScroll={e=>{scrollPositions.current[`${turn.run_id}:${traceOnly}`]=e.currentTarget.scrollTop;}} key={`${turn.run_id}-${traceOnly}`}><div className="turn-status"><Status value={label(turn.status)}/><span><Clock3 size={14}/>处理 {elapsed(turn.timing.processing_ms)}</span><span>等待用户 {elapsed(turn.timing.user_wait_ms)}</span><span>整轮 {elapsed(turn.timing.total_ms)}</span></div>
            {turn.prompts.filter(p=>p.kind==="ERROR").map((p,i)=><Notice key={i} kind="error">{String(p.payload.message??"本轮记录了异常，请查看处理过程。")}</Notice>)}
            {turn.phases.some(p=>p.status==="FAILED")&&<div className="button-row">{turn.phases.filter(p=>p.status==="FAILED").map(p=><button key={p.layer} className="text-button danger-text" onClick={()=>locateFailure(p.layer)}>查看{layerNames[p.layer]??"处理阶段"}异常</button>)}</div>}
            {!traceOnly&&<><div className="message-block"><div className="message-label"><UserRound size={15}/>用户输入</div><p className="question-text">{turn.question}</p></div><div className="message-block answer-block"><div className="message-label"><Layers3 size={15}/>最终答复</div><Results key={turn.run_id} results={turn.results} download={download}/></div><InteractionHistory turn={turn}/></>}
            <div className="trace-heading"><h3><GitBranch size={16}/>处理过程</h3><Help text="只展示实际记录的决定和执行事实；没有采集的阶段耗时显示未记录。"/></div>
            <div className="phase-list">{turn.phases.map(p=><details className={`phase ${p.status==="FAILED"?"failed":""}`} key={p.layer} ref={node=>{phaseNodes.current[p.layer]=node;}} open={expanded.includes(p.layer)} onToggle={e=>expand(p.layer,e.currentTarget.open)}>
              <summary><span className="phase-number">{p.status==="FAILED"?"!":<Check size={12}/>}</span><span><strong>{layerNames[p.layer]??"处理阶段"}</strong><small>{p.count?`${p.count} 项处理记录${p.status==="FAILED"?" · 有异常":""}`:"未记录"}</small></span><ChevronDown size={16}/></summary>
              <div className="phase-facts">{trace?<PhaseDetail trace={trace} layer={p.layer} onFunction={onFunction}/>:traceError?<><Notice kind="error">{traceError}</Notice><button className="text-button" onClick={()=>setTraceError("")}>重试</button></>:<p className="muted">正在读取阶段详情…</p>}</div>
            </details>)}</div>
          </div>:<div className="empty-detail"><p>此会话尚无问答。</p></div>}</>}
      </article>:<div className="empty-detail"><MessageSquare size={34}/><p>选择会话查看问答与处理过程</p></div>}
    </div>
  </section>;
}
