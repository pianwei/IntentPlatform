import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { ApiClient, errorText, management } from '../../shared/api';
import { Notice } from '../../shared/ui';
import type { Agent, Capability } from '../../shared/types';
import { TaskSurface, SearchSelect } from './ModernUI';
import { CapabilityPicker } from './CapabilityPicker';
import { FunctionEditor } from './FunctionEditor';
import { ModelEditor, type ModelProfile, type ModelTest } from './ModelSettingsPage';
export type CreationKind='agent'|'function'|'model';
export type CreationSession={key:number;kind:CreationKind;origin:string;model?:ModelProfile|null;expanded:boolean};
type Props={api:ApiClient;workspace:string;session:CreationSession;functions:Capability[];admin:boolean;publishable:boolean;exitRequested?:boolean;onStay:()=>void;onClose:(selection?:{newFunctionId?:string;newModelId?:string})=>void;onAgent:(a:Agent)=>void;onDirtyChange:(value:boolean)=>void;onBusyChange:(value:boolean)=>void;onExpand:()=>void;onRoute:(kind:CreationKind,id?:string,step?:number)=>void;onSaved:()=>Promise<void>;onModelTest?:(value:ModelTest)=>void};
export function CreationFlow(props:Props){
 const {api,workspace,session,functions,admin,publishable}=props;
 const stepRef=useRef(0);
 const [kind,setKind]=useState(session.kind),[step,setStep]=useState(0),[functionId,setFunctionId]=useState<string>(),[model,setModel]=useState<ModelProfile|null>(session.model??null),[confirm,setConfirm]=useState<'exit'|'back'|null>(null);
 const [dirty,setDirty]=useState<Partial<Record<CreationKind,boolean>>>({}),[busy,setBusy]=useState(false),[selection,setSelection]=useState<{newFunctionId?:string;newModelId?:string}>({});
 const allDirty=Object.values(dirty).some(Boolean);const active=useRef(true);useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
 useEffect(()=>props.onDirtyChange(allDirty),[allDirty,props.onDirtyChange]);useEffect(()=>props.onBusyChange(busy),[busy,props.onBusyChange]);
 useEffect(()=>{if(props.exitRequested&&!busy)setConfirm('exit');},[props.exitRequested,busy]);
 const agentDirty=useCallback((v:boolean)=>setDirty(s=>({...s,agent:v})),[]),functionDirty=useCallback((v:boolean)=>setDirty(s=>({...s,function:v})),[]),modelDirty=useCallback((v:boolean)=>setDirty(s=>({...s,model:v})),[]);
 function switchTo(next:CreationKind){setFunctionId(undefined);setStep(0);stepRef.current=0;setKind(next);setModel(null);props.onRoute(next,undefined,next==='function'?0:undefined);}
 function requestClose(){if(busy)return;if(allDirty)setConfirm('exit');else props.onClose();}
 function requestBack(){if(busy)return;if(dirty[kind])setConfirm('back');else back();}
 function back(){setBusy(false);setDirty(v=>({...v,[kind]:false}));setKind(session.kind);setConfirm(null);props.onRoute(session.kind);}
 function useModel(value:ModelProfile){setBusy(false);setDirty(v=>({...v,model:false}));if(session.kind==='agent'){setSelection({newModelId:value.id});setKind('agent');props.onRoute('agent');}else props.onClose({newModelId:value.id});}
 const title=kind==='agent'?'新建智能体':kind==='function'?'新建功能':session.model?'编辑模型配置':'新建模型配置';
 return <TaskSurface title={title} expanded={session.expanded} onExpand={props.onExpand} onClose={requestClose} wide={kind==='function'} busy={busy}>
 {confirm&&<div className="task-confirm"><h3>保留当前修改？</h3><p>当前输入尚未保存，离开前可以继续编辑。</p><div className="button-row"><button className="button primary" onClick={()=>{setConfirm(null);props.onStay();}}>继续编辑</button><button className="button" onClick={()=>{if(confirm==='back')back();else props.onClose();}}>放弃修改并离开</button></div></div>}
 <div hidden={!!confirm}>
 {kind!==session.kind&&<button className="back-link" disabled={busy} onClick={requestBack}><ArrowLeft size={15}/>返回新建智能体</button>}
 {session.kind==='agent'&&<div hidden={kind!=='agent'}><AgentCreator api={api} workspace={workspace} functions={functions} admin={admin} selection={selection} onDirtyChange={agentDirty} onBusyChange={setBusy} onCreateFunction={()=>switchTo('function')} onCreateModel={()=>switchTo('model')} onCreated={props.onAgent}/></div>}
 {kind==='function'&&<FunctionEditor api={api} workspace={workspace} item={null} functions={functions} admin={admin} writable publishable={publishable} compact onClose={session.kind==='agent'?requestBack:requestClose} returnLabel={session.origin.includes('/agents/')?'返回智能体':'返回功能库'} onDirtyChange={functionDirty} onBusyChange={setBusy} initialStep={step} onStepChange={value=>{setStep(value);stepRef.current=value;props.onRoute('function',functionId,value);}} onSaved={async saved=>{if(!active.current)return;setFunctionId(saved.id);props.onRoute('function',saved.id,stepRef.current);await props.onSaved();if(!active.current)return;if(saved.status==='published'){setBusy(false);setDirty(v=>({...v,function:false}));if(session.kind==='agent'){setSelection({newFunctionId:saved.id});setKind('agent');props.onRoute('agent');}else props.onClose({newFunctionId:saved.id});}}}/>}
 {kind==='model'&&<ModelEditor onTest={props.onModelTest} api={api} workspace={workspace} item={model} onDirtyChange={modelDirty} onBusyChange={setBusy} onSaved={value=>{setModel(value);props.onRoute('model',value.id);void props.onSaved();}} onUse={useModel}/>}
 </div></TaskSurface>;
}
function AgentCreator({api,workspace,functions,admin,selection,onDirtyChange,onBusyChange,onCreateModel,onCreateFunction,onCreated}:{api:ApiClient;workspace:string;functions:Capability[];admin:boolean;selection:{newFunctionId?:string;newModelId?:string};onDirtyChange:(v:boolean)=>void;onBusyChange:(v:boolean)=>void;onCreateModel:()=>void;onCreateFunction:()=>void;onCreated:(a:Agent)=>void}){
 const [name,setName]=useState(''),[modelId,setModelId]=useState(''),[selected,setSelected]=useState<string[]>([]),[models,setModels]=useState<ModelProfile[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState('');const pending=useRef(false),alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{let live=true;api.get<ModelProfile[]>(`${management(workspace)}/model-profiles`).then(items=>{if(live)setModels(items);}).catch(()=>{});return()=>{live=false;};},[api,workspace,selection.newModelId]);
 useEffect(()=>{if(selection.newModelId)setModelId(selection.newModelId);if(selection.newFunctionId)setSelected(v=>[...new Set([...v,selection.newFunctionId!])]);},[selection]);
 useEffect(()=>onDirtyChange(!!name||!!modelId||!!selected.length),[name,modelId,selected,onDirtyChange]);useEffect(()=>onBusyChange(busy),[busy,onBusyChange]);
 async function create(){if(pending.current)return;pending.current=true;setBusy(true);setError('');try{const result=await api.post<Agent>(`${management(workspace)}/agents`,{name:name.trim(),capability_ids:selected,model_profile_id:modelId||null,routing_strategy:'three-layer-v1',planning_enabled:true});if(alive.current)onCreated(result);}catch(e){if(alive.current)setError(errorText(e));}finally{pending.current=false;if(alive.current)setBusy(false);}}
 return <form className="agent-create" onSubmit={e=>{e.preventDefault();void create();}}><fieldset disabled={busy}>{error&&<Notice kind="error">{error}</Notice>}<label className="field">智能体名称 <em>*</em><input aria-label="智能体名称" autoFocus required maxLength={160} placeholder="例如：客户服务助手" value={name} onChange={e=>setName(e.target.value)}/></label><label className="field">模型配置<SearchSelect label="模型配置" value={modelId} options={models.filter(m=>!m.disabled).map(m=>({value:m.id,label:m.name,detail:`${m.latest?.config.model_version??''} · v${m.latest_version}`}))} onChange={setModelId} placeholder="选择模型" footer={admin&&<button type="button" className="text-button" onClick={onCreateModel}>创建模型配置</button>}/>{!modelId&&<small>未配置模型时，仅可使用规则工程预览。</small>}</label><CapabilityPicker api={api} disabled={busy} functions={functions} selected={selected} onChange={setSelected} onCreate={onCreateFunction}/><footer className="editor-footer"><span>{selected.length?`已选 ${selected.length} 项功能`:'至少选择一项已发布功能'}</span><button className="button primary" disabled={!name.trim()||!selected.length||busy}>{busy?'正在创建…':'创建智能体'}</button></footer></fieldset></form>;
}
