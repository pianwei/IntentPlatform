import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiClient, errorText } from "../../shared/api";
import { Notice } from "../../shared/ui";
import { Modal } from "./ModernUI";
import "./BusinessCatalog.css";

export type HistoryVersion = { id: string | null; version: number; created_at?: string | null; definition?: unknown; caption?: string; current?: boolean };
type Change = { id: string; resource_id: string; created_at: string; action: string; actor_name?: string | null; version_id?: string | null; revision?: number | null; detail?: Record<string, unknown> | null };
const operations: Record<string,string> = { create:"创建",import:"接入",edit:"修改草稿",validate:"测试",test:"测试",publish:"发布",disable:"停用",rollback:"切换发布版本",channels:"调整渠道",access:"调整访问权限",adopt:"采用默认版本",update:"更新目录" };
const labels: Record<string,string> = { name:"名称",title:"智能体名称",description:"说明",welcome_message:"欢迎语",suggested_questions:"推荐问题",primary_color:"主题色",input_placeholder:"输入提示",show_history:"会话历史",icon:"图标",routing:"运行方式",business_skills:"业务规则",effect:"执行性质",enabled:"启用状态",object_type:"业务对象",action:"动作",input_schema:"输入参数",output_schema:"输出字段",output_mode:"输出方式",connector:"执行配置",presentation:"结果展示",composite:"组合规则",runtime_ui:"对话界面",channels:"访问渠道",capabilities:"业务能力",requires_confirmation:"用户确认",domain_refs:"业务目录版本",parameter_semantics:"参数含义",result_bindings:"返回对象绑定",argument_bindings:"参数绑定" };
export function versionChanges(before: unknown, after: unknown) {
  const old = before && typeof before === "object" ? before as Record<string,unknown> : {};
  const next = after && typeof after === "object" ? after as Record<string,unknown> : {};
  return [...new Set([...Object.keys(old),...Object.keys(next)])].filter(key=>JSON.stringify(old[key])!==JSON.stringify(next[key])).map(key=>({key,label:labels[key]??key,before:old[key],after:next[key]}));
}
const value = (item: unknown) => item === undefined ? "未配置" : typeof item === "string" ? item || "空值" : JSON.stringify(item,null,2);
function auditVersion(row:Change){return row.version_id??(typeof row.detail?.version_id==="string"?row.detail.version_id:null);}
function auditRevision(row:Change){return row.revision??(typeof row.detail?.revision==="number"?row.detail.revision:null);}
function auditVersionNumber(row:Change){return typeof row.detail?.version==="number"?row.detail.version:null;}
export function VersionHistoryDialog({ title="版本与变更", api, versions, selectedId, onSelect, onClose, children, footer, busy=false, auditPath, resourceId, auditAllowed=false }: {
  title?: string; api: ApiClient; versions: HistoryVersion[]; selectedId: string | null; onSelect: (id:string|null)=>void;
  onClose:()=>void; children?:ReactNode; footer?:ReactNode; busy?:boolean; auditPath?:string; resourceId?:string; auditAllowed?:boolean;
}) {
  const [tab,setTab]=useState<"versions"|"changes">("versions"),[rows,setRows]=useState<Change[]>([]),[error,setError]=useState(""),[loading,setLoading]=useState(false);
  const sorted=useMemo(()=>[...versions].sort((a,b)=>b.version-a.version),[versions]);
  const selected=sorted.find(v=>v.id===selectedId),previous=selected?sorted.find(v=>v.version<selected.version):undefined;
  useEffect(()=>{if(tab!=="changes"||!auditAllowed||!auditPath)return;let live=true;setLoading(true);setRows([]);setError("");api.get<Change[]>(auditPath).then(items=>{if(live)setRows((Array.isArray(items)?items:[]).filter(item=>!resourceId||item.resource_id===resourceId));}).catch(cause=>live&&setError(errorText(cause))).finally(()=>live&&setLoading(false));return()=>{live=false;};},[api,auditPath,resourceId,auditAllowed,tab]);
  const changes=previous&&selected?.definition!==undefined&&previous.definition!==undefined?versionChanges(previous.definition,selected.definition):[];
  return <Modal title={title} wide busy={busy} onClose={onClose}><div className="modal-body history-dialog"><div className="history-tabs" role="tablist" aria-label="版本与变更视图"><button role="tab" aria-selected={tab==="versions"} onClick={()=>setTab("versions")}>发布版本</button><button role="tab" aria-selected={tab==="changes"} onClick={()=>setTab("changes")}>变更记录</button></div>
    {tab==="versions"?<div className="history-layout"><nav aria-label="发布版本">{sorted.map(row=><button type="button" key={row.id??`version-${row.version}`} disabled={busy} className={selectedId===row.id?"active":""} onClick={()=>onSelect(row.id)}><strong>v{row.version}{row.current&&<span className="history-current">当前</span>}</strong><span>{row.caption??"已发布版本"}</span><time>{row.created_at?new Date(row.created_at).toLocaleString():"时间未记录"}</time></button>)}{!sorted.length&&<p className="muted">尚无发布版本。</p>}</nav><section className="history-detail">{children??(selected?<><h3>v{selected.version}</h3><details><summary>此版本的配置</summary><pre>{value(selected.definition)}</pre></details></>:<p className="muted">请选择版本。</p>)}{selected&&<details className="history-diff"><summary>与上一版本的变化</summary>{!previous?<p>首个发布版本，无上一版本。</p>:selected.definition===undefined||previous.definition===undefined?<p>历史版本内容未记录。</p>:!changes.length?<p>已记录的配置没有变化。</p>:changes.map(change=><section key={change.key}><h4>{change.label}</h4><div className="history-diff-values"><div><span>上一版本</span><pre>{value(change.before)}</pre></div><div><span>此版本</span><pre>{value(change.after)}</pre></div></div></section>)}</details>}</section></div>:<section className="history-changes">{!auditAllowed?<p className="muted">当前账号没有查看操作记录的权限。</p>:!auditPath?<p className="muted">历史操作记录未记录。</p>:<>{error&&<Notice kind="error">{error}</Notice>}{loading?<p role="status">正在读取变更记录…</p>:<>{rows.length?<ol>{rows.map(row=>{const linked=auditVersion(row),revision=auditRevision(row),version=auditVersionNumber(row);return <li key={row.id}><div><strong>{operations[row.action.split(".").at(-1)!]??row.action}</strong><time>{row.created_at?new Date(row.created_at).toLocaleString():"时间未记录"}</time></div><p>{row.actor_name||"操作人未记录"}{linked?` · 发布版本${version?` v${version}`:"已关联"}`:revision?` · 草稿修订 ${revision}`:" · 版本关联未记录"}</p></li>;})}</ol>:<p className="muted">暂无变更记录。</p>}</>}</>}</section>}
  </div>{footer??<footer className="modal-footer"><button className="button" disabled={busy} onClick={onClose}>关闭</button></footer>}</Modal>;
}
