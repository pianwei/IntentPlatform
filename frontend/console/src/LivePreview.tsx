import { useEffect, useMemo, useRef, useState } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import type { Agent, Identity, PublicApp, Release } from "../../shared/types";
import { AgentWorkspace } from "../../agent-web/src/AgentWorkspace";
import { Busy, Notice } from "../../shared/ui";
import { Modal } from "./ModernUI";
export function LivePreview({api,agent,onClose}:{api:ApiClient;agent:Agent;onClose:()=>void}){
 const [identity,setIdentity]=useState<Identity|null>(null),[app,setApp]=useState<PublicApp|null>(null),[error,setError]=useState(''),[tick,setTick]=useState(0);const session=useRef<string>();
 const [version,setVersion]=useState<number|null>(null),[versionLoading,setVersionLoading]=useState(false);
 const runtime=useMemo(()=>new ApiClient('runtime',()=>{setIdentity(null);setTick(t=>t+1);}),[]);
 useEffect(()=>{let live=true;const id=encodeURIComponent(agent.public_id??agent.id);void Promise.all([new ApiClient('runtime').get<PublicApp>(`/api/v2/runtime/apps/${id}`),api.post<Identity>(`/api/v2/runtime/apps/${id}/sessions`,{runtime_session_id:session.current})]).then(([a,i])=>{if(!live){const discard=new ApiClient('runtime');discard.setToken(i.access_token);void discard.post('/api/v2/runtime/sessions/logout').catch(()=>{});return;}session.current=i.runtime_session_id;runtime.setToken(i.access_token);setApp(a);setIdentity(i);setError('');}).catch(e=>live&&setError(errorText(e)));return()=>{live=false;};},[api,agent.public_id,agent.id,runtime,tick]);
 useEffect(()=>{if(!identity?.expires_at)return;const timer=setTimeout(()=>setTick(t=>t+1),Math.max(1000,new Date(identity.expires_at).getTime()-Date.now()-15000));return()=>clearTimeout(timer);},[identity]);
 useEffect(()=>{setVersion(null);if(!app?.release_id)return;let active=true;setVersionLoading(true);void api.get<Release[]>(`${management(agent.workspace_id)}/agents/${encodeURIComponent(agent.id)}/releases`).then(rows=>{if(active)setVersion(rows.find(r=>(r.release_id||r.id)===app.release_id)?.version??null);}).catch(()=>{if(active)setVersion(null);}).finally(()=>{if(active)setVersionLoading(false);});return()=>{active=false;};},[api,agent.workspace_id,agent.id,app?.release_id]);
 useEffect(()=>()=>{void runtime.post('/api/v2/runtime/sessions/logout').catch(()=>{});runtime.clear();},[runtime]);
 return <Modal title={`${agent.name} · ${version===null?"试聊已发布版本":`当前发布 v${version}`}`} wide onClose={onClose}>{app&&version===null&&!versionLoading&&<Notice>发布版本号暂时无法读取。</Notice>}<div className="live-preview">{error?<Notice kind="error">{error}</Notice>:app&&identity?<AgentWorkspace key={identity.runtime_session_id} api={runtime} identity={identity} app={app} storageKey={`intent.preview.${identity.runtime_session_id}`} embedded onLogout={async()=>{await runtime.post('/api/v2/runtime/sessions/logout');onClose();}}/>:<Busy/>}</div></Modal>;
}
