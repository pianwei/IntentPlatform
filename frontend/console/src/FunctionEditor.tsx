import { ArrowLeft, ArrowRight, History, Save } from "lucide-react";
import { SchemaTestForm } from "./SchemaTestForm";
import { InputParameters } from "./InputParameters";
import { PageTitle, Modal, Help } from "./ModernUI";
import { CompositeRuleEditor } from "./CompositeRuleEditor";
import { SemanticMappingEditor } from "./SemanticMappingEditor";
import { useState, useEffect, useRef, useCallback, type FormEvent } from "react";
import { Code2 } from "lucide-react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";
import type { Capability, FunctionDefinition, Validation } from "../../shared/types";
import type { CompositeCommit } from "./CompositeRuleEditor";
import type { ModelProfile } from "./ModelSettingsPage";
import { BusinessResultContent, businessLabel, valueText } from "../../shared/BusinessResult";
import "./FunctionExperience.css";
import { OpenAPIWizard } from "./OpenAPIWizard";
import { HttpMappingEditor } from "./HttpMappingEditor";
import { SourceReviewPanel } from "./SourceReviewPanel";
import type { SourceReview } from "../../shared/sources";
import { httpEditorError } from "../../shared/httpMapping";
import { jsonEqual } from "../../shared/jsonEquality";
import { CredentialSelector } from "./CredentialSelector";
import { OutputMappingEditor } from "./OutputMappingEditor";
import { VersionHistoryDialog, type HistoryVersion } from "./VersionHistoryDialog";
export const blankDefinition: FunctionDefinition = {
  authoring_version: "function-v2",
  output_examples: [],
  execution_kind: "direct",
  composite: null,
  name: "",
  description: "",
  object_type: "",
  action: "",
  result_type: "",
  aliases: [],
  input_schema: {
    type: "object",
    properties: {},
    required: [],
    additionalProperties: false,
  },
  output_schema: { type: "object", properties: {}, required: [], additionalProperties: false },
  connector: {
    kind: "http",
    version: "2",
    http_mapping: { path: {}, query: {}, headers: {}, body: {} },
    endpoint: null,
    method: "GET",
    credential_ref: null,
    timeout_seconds: 10,
    idempotency_header: null,
    status_endpoint: null,
  },
  effect: "READ_ONLY",
  requires_confirmation: false,
  allowed_users: [],
  preconditions: [],
  presentation: { component: "detail", title: "查询结果", field_map: {}, files_field: null },
  positive_examples: [],
  negative_examples: [],
};
export function parseFunctionDraft(raw: string): FunctionDefinition {
  const value = JSON.parse(raw);
  const object = (v: unknown) => !!v && typeof v === "object" && !Array.isArray(v);
  if (!object(value) || !object(value.connector) || !object(value.input_schema) || !object(value.presentation)) throw new Error("定义、connector、input_schema 和 presentation 必须是对象");
  if (value.output_schema !== undefined && value.output_schema !== null && !object(value.output_schema)) throw new Error("output_schema 必须是对象或 null");
  if (!object(value.presentation.field_map)) throw new Error("presentation.field_map 必须是字段映射对象");
  for (const key of ["name", "description", "object_type", "action", "result_type", "effect"]) if (typeof value[key] !== "string") throw new Error(`${key} 必须是字符串`);
  if (typeof value.connector.kind !== "string" || typeof value.connector.method !== "string") throw new Error("connector.kind 与 method 必须是字符串");
  if (value.execution_kind && !["direct", "composite"].includes(value.execution_kind)) throw new Error("执行类型必须为直接调用或组合功能");
  if (value.execution_kind === "composite" && value.composite !== null && value.composite !== undefined && (!object(value.composite) || !Array.isArray(value.composite.dependencies) || !object(value.composite.final_output_mapping))) throw new Error("组合功能需要固定规则版本、依赖和最终输出映射");
  const httpError = value.execution_kind === "composite" ? null : httpEditorError(value.connector);
  if (httpError) throw new Error(httpError);
  for (const key of ["domain_refs", "participant_roles", "relations", "result_bindings"]) if (value[key] !== undefined && (!Array.isArray(value[key]) || value[key].some((row: unknown) => !object(row)))) throw new Error(`${key} 必须是对象数组`);
  for (const key of ["argument_bindings", "parameter_semantics"]) if (value[key] !== undefined && (!object(value[key]) || Object.values(value[key]).some(row => !object(row)))) throw new Error(`${key} 必须是映射对象`);
  return value as FunctionDefinition;
}
export function FunctionEditor({
  api,
  workspace,
  item: initialItem,
  functions = [],
  admin = false,
  writable,
  publishable = writable,
  onClose,
  onSaved: notifySaved,
  onDirtyChange,
  onBusyChange,
  onStepChange,
  initialStep,
  compact = false,
  returnLabel = "功能库",
}: {
  api: ApiClient;
  workspace: string;
  item: Capability | null;
  functions?: Capability[];
  admin?: boolean;
  writable: boolean;
  publishable?: boolean;
  onClose: () => void;
  onDirtyChange?: (value: boolean) => void;
  onBusyChange?: (value: boolean) => void;
  onStepChange?: (step:number) => void;
  initialStep?:number;
  compact?:boolean;
  returnLabel?:string;
  onSaved: (item: Capability) => Promise<void>;
}) {
  const [item,setItem] = useState(initialItem);
  const [step,setStep] = useState(()=>Math.max(0,Math.min(2,initialStep??(Number(new URLSearchParams(location.search).get("step"))||0))));
  useEffect(()=>{if(initialStep!==undefined)setStep(Math.max(0,Math.min(2,initialStep)));},[initialStep]);
  async function onSaved(saved:Capability){setItem(saved);try{await notifySaved(saved);}catch(cause){setError(`操作已完成；列表刷新失败：${errorText(cause)}`);}}
  const [mappingDirty,setMappingDirty]=useState(false),[mappingBusy,setMappingBusy]=useState(false);
  useEffect(()=>{const content=document.querySelector<HTMLElement>(".task-dialog .task-content")??document.querySelector<HTMLElement>(".console-modern .page-content");if(content)content.scrollTop=0;},[step]);
  const [stepError,setStepError] = useState("");
  const [showDisable,setShowDisable] = useState(false);
  const [json, setJson] = useState(
    JSON.stringify(item?.definition ?? blankDefinition, null, 2),
  );
  const [advanced, setAdvanced] = useState(() => {
    try { parseFunctionDraft(JSON.stringify(item?.definition ?? blankDefinition)); return false; }
    catch { return true; }
  });
  const [importMode, setImportMode] = useState(false),[importStarted,setImportStarted]=useState(false);
  const [importBusy,setImportBusy]=useState(false),[importDirty,setImportDirty]=useState(false);
  const [compositePending, setCompositePending] = useState(false);
  const compositeCommit = useRef<CompositeCommit | null>(null);
  const registerComposite = useCallback((value:CompositeCommit|null)=>{compositeCommit.current=value;},[]);
  const [sourceReview, setSourceReview] = useState<SourceReview | null>(null);
  const [sourceDirty, setSourceDirty] = useState(false);
  const [sourceBusy, setSourceBusy] = useState(false);
  const [failureArgs, setFailureArgs] = useState("{}");
  const [testArgs, setTestArgs] = useState("{}");
  const [allowEffects, setAllowEffects] = useState(false);
  const [validation, setValidation] = useState<Validation | null>(()=>initialItem?.validation?.revision===initialItem?.revision?initialItem?.validation??null:null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [models,setModels]=useState<ModelProfile[]>([]),[testModel,setTestModel]=useState("");
  const [testGoal,setTestGoal]=useState("");
  const [historyOpen,setHistoryOpen]=useState(false),[historyVersions,setHistoryVersions]=useState<HistoryVersion[]>([]),[historySelection,setHistorySelection]=useState<string|null>(null),[historyLoading,setHistoryLoading]=useState(false),[historyError,setHistoryError]=useState("");
  const [testValid,setTestValid]=useState(true);const [inputValid,setInputValid]=useState(true),[outputValid,setOutputValid]=useState(true);
  let definition: FunctionDefinition = blankDefinition;
  let draftError = "";
  try { definition = parseFunctionDraft(json); } catch (e) { draftError = errorText(e); }
  const isComposite = definition.execution_kind === "composite";
  useEffect(()=>{let active=true;setSourceReview(null);if(!item?.id)return;api.get<SourceReview>(`${management(workspace)}/functions/${item.id}/source-review`).then(review=>{if(active)setSourceReview(review);}).catch(()=>{if(active)setSourceReview(null);});return()=>{active=false;};},[api,workspace,item?.id,item?.revision]);
  useEffect(()=>{if(!isComposite)return;let active=true;api.get<ModelProfile[]>(`${management(workspace)}/model-profiles`).then(value=>{if(active){const usable=Array.isArray(value)?value.filter(item=>!item.disabled&&item.latest_version>0):[];setModels(usable);setTestModel(current=>current||usable[0]?.id||"");}}).catch(cause=>{if(active)setError(`测试模型读取失败：${errorText(cause)}`);});return()=>{active=false;};},[api,workspace,isComposite]);
  const dirty = compositePending || !!draftError || !jsonEqual(definition, item?.definition ?? blankDefinition);
  const pendingStates=["RUNNING","RECEIVED","ROUTING","EXECUTING","WAITING_CONFIRMATION","UNKNOWN"];
  const testingFailure=!!validation?.failure_execution_test&&!validation.failure_execution_test.passed&&pendingStates.includes(validation.failure_execution_test.status);
  const activeTest=testingFailure?validation?.failure_execution_test:validation?.execution_test;
  const executionPending=!!activeTest&&pendingStates.slice(0,-1).includes(activeTest.status);
  const executionUnknown=activeTest?.status==="UNKNOWN";
  function applyExecutionResult(result:NonNullable<Validation["execution_test"]>){if(result.revision!==item?.revision)return;setValidation(previous=>{if(!previous)return previous;const next={...previous,[testingFailure?"failure_execution_test":"execution_test"]:result};const ready=!!next.execution_test?.passed&&(!sourceReview?.api_source||!!next.failure_execution_test?.passed);return{...next,valid:ready,errors:ready?[]:result.error?[result.error]:previous.errors};});}
  useEffect(()=>{
    if(!item||!executionPending||validation?.revision!==item.revision)return;
    let active=true;const timer=setTimeout(()=>{void api.get<NonNullable<Validation["execution_test"]>>(`${management(workspace)}/functions/${item.id}/execution-test${testingFailure?"?failure=true":""}`).then(result=>{if(active)applyExecutionResult(result);}).catch(cause=>{if(active)setError(`测试状态读取失败：${errorText(cause)}。可刷新状态，不要重复发起写入测试。`);});},2000);
    return()=>{active=false;clearTimeout(timer);};
  },[api,workspace,item?.id,item?.revision,validation,executionPending]);
  useEffect(() => { onDirtyChange?.(dirty || sourceDirty || mappingDirty || importDirty); }, [dirty, sourceDirty, mappingDirty, importDirty, onDirtyChange]);
  useEffect(() => { const leave = (event: BeforeUnloadEvent) => { if (dirty || sourceDirty || mappingDirty || importDirty) {event.preventDefault();event.returnValue="";} }; window.addEventListener("beforeunload",leave);return()=>window.removeEventListener("beforeunload",leave); }, [dirty,sourceDirty,mappingDirty,importDirty]);
  useEffect(()=>{onBusyChange?.(busy||sourceBusy||mappingBusy||importBusy);},[busy,sourceBusy,mappingBusy,importBusy,onBusyChange]);
  function next(target: number) { setStepError(""); if (!item && target > step && (!definition.name.trim() || !definition.description.trim())) {setStep(0);setStepError("请先填写功能名称和业务描述");document.querySelector<HTMLInputElement>(!definition.name.trim()?'[aria-label="功能名称"]':'[aria-label="业务描述"]')?.focus();return;} if(target===2 && Object.keys((definition.input_schema.properties||{}) as object).some(k=>!k.trim())){setStep(1);setStepError("请补全输入参数名称");return;}setStep(target);onStepChange?.(target); }
  function update(patch: Partial<FunctionDefinition>) {
    setJson(current => {
      try { return JSON.stringify({ ...parseFunctionDraft(current), ...patch, authoring_version:"function-v2" }, null, 2); }
      catch (e) { setError(errorText(e)); return current; }
    });
    setValidation(null);
  }
  async function currentDefinition(){const value=parseFunctionDraft(json);const rule=value.execution_kind==="composite"&&compositeCommit.current?await compositeCommit.current():{};const next={...value,...rule,authoring_version:"function-v2" as const};setJson(JSON.stringify(next,null,2));return next;}
  async function perform(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    if (busy || !writable || sourceDirty || sourceBusy) return;
    await perform(async () => {
      const value = await currentDefinition();
      if (!value.name.trim() || !value.description.trim()) {setStep(0);setStepError("请填写功能名称和业务描述");document.querySelector<HTMLInputElement>('[aria-label="功能名称"]')?.focus();throw new Error("保存草稿前请填写功能名称和业务描述");}
      const saved = item
        ? await api.patch<Capability>(
            `${management(workspace)}/functions/${item.id}/draft`,
            { revision: item.revision, definition: value },
          )
        : await api.post<Capability>(
            `${management(workspace)}/functions/import`,
            { definition: value },
          );
      setJson(JSON.stringify(saved.definition, null, 2));
      setValidation(null);
      await onSaved(saved);
    });
  }
  async function saveAndValidate(){
    if(busy||!writable||sourceDirty||sourceBusy||!testValid||!inputValid||!outputValid||executionPending||executionUnknown)return;
    await perform(async()=>{
      const value=await currentDefinition();
      if(!value.name.trim()||!value.description.trim()){setStep(0);throw new Error("请填写功能名称和业务描述");}
      const saved=item&&jsonEqual(value,item.definition)?item:item?await api.patch<Capability>(`${management(workspace)}/functions/${item.id}/draft`,{revision:item.revision,definition:value}):await api.post<Capability>(`${management(workspace)}/functions/import`,{definition:value});
      setJson(JSON.stringify(saved.definition,null,2));setItem(saved);setStep(2);onStepChange?.(2);
      let result:Validation|null=null;
      try{
        const review=await api.get<SourceReview>(`${management(workspace)}/functions/${saved.id}/source-review`);setSourceReview(review);
        if(!review.ready)throw new Error("草稿已保存；请先处理接口资料中的待确认内容。");
        if(value.effect==="WRITE"&&!allowEffects)throw new Error("草稿已保存；请明确确认本次测试的写入效果。");
        if(value.execution_kind==="composite"&&!testModel)throw new Error("草稿已保存；请选择用于执行组合测试的模型。");
        result=await api.post<Validation>(`${management(workspace)}/functions/${saved.id}/validate`,{revision:saved.revision,arguments:JSON.parse(testArgs),...(review.api_source?{failure_arguments:JSON.parse(failureArgs)}:{}),probe:true,allow_effects:allowEffects,...(value.execution_kind==="composite"?{model_profile_id:testModel,test_goal:testGoal.trim()||value.name}:{})});
        setValidation(result);
      }finally{await onSaved({...saved,...(result?.valid?{status:"validated"}:{}),validation:result});}
    });
  }
  async function publishCurrent(){if(!item)return;await perform(async()=>onSaved(await api.post<Capability>(`${management(workspace)}/functions/${item.id}/publish`,{revision:item.revision})));}
  async function openHistory(){if(!item||historyLoading)return;setHistoryOpen(true);setHistoryLoading(true);setHistoryError("");try{const rows=await api.get<HistoryVersion[]>(`${management(workspace)}/functions/${item.id}/versions`);setHistoryVersions(rows);setHistorySelection(rows[0]?.id??null);}catch(cause){setHistoryError(errorText(cause));}finally{setHistoryLoading(false);}}
  async function confirmExecution(confirmed:boolean){const confirmation=activeTest?.confirmation;if(!item||!confirmation||activeTest?.status!=="WAITING_CONFIRMATION"||busy)return;await perform(async()=>applyExecutionResult(await api.post<NonNullable<Validation["execution_test"]>>(`${management(workspace)}/functions/${item.id}/execution-test/confirmations`,{run_id:confirmation.run_id,revision:confirmation.revision,confirmed})));}
  const validCurrent=!!item&&!dirty&&!sourceDirty&&inputValid&&outputValid&&!!validation?.valid&&validation.revision===item.revision&&validation.execution_test?.passed===true&&validation.execution_test.revision===item.revision&&(!sourceReview?.api_source||!!validation.failure_execution_test?.passed);
  async function saveOutputExample(){if(!item)return;await perform(async()=>{const saved=await api.post<Capability>(`${management(workspace)}/functions/${item.id}/output-examples`,{revision:item.revision});setJson(JSON.stringify(saved.definition,null,2));await onSaved(saved);});}
  function requiredError(field:"name"|"description"){return !!stepError&&!definition[field].trim();}
  return <section className={`editor-page functional-editor ${compact?"compact-editor":""}`}>
    <button className="back-link" aria-label="关闭功能编辑" onClick={onClose}><ArrowLeft size={15}/>{returnLabel}</button>
    {!compact&&<PageTitle title={item?`编辑功能 · ${item.name}`:"新建功能"} help="名称和业务描述用于保存草稿；发布前还需完成业务映射、参数契约和验证。"/>}
    <header className="function-action-header"><div><h2>{item?"功能配置":"新建功能"}</h2><span className="function-action-status">{busy?'正在处理…':dirty||sourceDirty?'有未保存的修改':validCurrent?'已验证，可发布':item?'草稿已保存':'尚未保存'}</span></div><div className="button-row">{item&&<button className="button" type="button" disabled={busy||historyLoading} onClick={()=>void openHistory()}><History size={14}/>版本与变更</button>}<button className="button" form="function-draft-form" type="submit" disabled={busy||importBusy||importMode||!writable||!!draftError||sourceDirty||sourceBusy||executionPending||!inputValid||!outputValid}><Save size={14}/>保存草稿</button><button className="button" disabled={busy||importBusy||importMode||!writable||!!draftError||sourceDirty||sourceBusy||executionPending||executionUnknown||!inputValid||!outputValid||(step===2&&!testValid)} onClick={()=>{if(step!==2){setStep(2);onStepChange?.(2);}else void saveAndValidate();}}>{step===2?'执行测试':'测试功能'}</button><button className="button primary" disabled={busy||!publishable||sourceDirty||sourceBusy||!validCurrent||!sourceReview?.ready||sourceReview.revision!==item?.revision} onClick={()=>void publishCurrent()}>发布功能版本</button></div></header>
    <div className="editor-stepper" aria-label="配置步骤">{["基本信息与执行","输入输出与映射","验证与发布"].map((label,index)=><button key={label} type="button" className={step===index?'current':index<step?'complete':''} aria-current={step===index?'step':undefined} disabled={busy||importBusy||importMode} onClick={()=>next(index)}><span>{index+1}</span><strong>{label}</strong></button>)}</div>
    {error&&<Notice kind="error">{error}</Notice>}{stepError&&<Notice kind="error">{stepError}</Notice>}
    <div className="editor-body"><div className="editor-main">
      <div hidden={step!==0}>{!item&&<button className="text-button" disabled={busy||importBusy} onClick={()=>{setImportStarted(true);setImportMode(!importMode);}}>{importMode?"返回手动填写":"从 OpenAPI 接口资料导入"}</button>}{!item&&importStarted&&<div hidden={!importMode}><OpenAPIWizard api={api} workspace={workspace} writable={writable} onBusyChange={setImportBusy} onDirtyChange={setImportDirty} onCreated={async saved=>{setJson(JSON.stringify(saved.definition,null,2));setValidation(saved.validation?.revision===saved.revision?saved.validation:null);setImportMode(false);setImportDirty(false);setImportBusy(false);await onSaved(saved);}}/></div>}</div>
      <form hidden={importMode} id="function-draft-form" onSubmit={e=>void save(e)} noValidate>
      <fieldset disabled={!writable||busy||executionPending||sourceDirty||sourceBusy||importBusy}>
      {draftError&&<Notice kind="error">完整定义存在错误：{draftError}。输入已保留，可在下方修正。</Notice>}
      <fieldset disabled={!!draftError}>
      <div hidden={step!==0}>
        <section className="form-section"><div className="section-caption"><h2>基本信息</h2><span><em>*</em> 保存草稿必填</span></div>
        <label className="field">功能名称 <em>*</em><input aria-label="功能名称" aria-invalid={requiredError("name")} required value={definition.name} maxLength={160} onChange={e=>update({name:e.target.value})} placeholder="例如：查询账户余额"/></label>
        <label className="field">业务描述 <em>*</em><textarea aria-label="业务描述" aria-invalid={requiredError("description")} required rows={3} value={definition.description} onChange={e=>update({description:e.target.value})} placeholder="说明解决什么问题、适用条件及返回内容"/></label>
        <label className="field">执行方式<select value={definition.execution_kind??'direct'} onChange={e=>{const kind=e.target.value as 'direct'|'composite';update({execution_kind:kind,composite:kind==='composite'?definition.composite??{mode:'document',skill_version_id:'',dependencies:[],final_output_mapping:{}}:null});setCompositePending(false);}}><option value="direct">直接调用</option><option value="composite">组合功能</option></select></label></section>
        {!draftError&&<SemanticMappingEditor api={api} workspace={workspace} definition={definition} update={update} writable={writable&&publishable} onDirtyChange={setMappingDirty} onBusyChange={setMappingBusy}/>}
        {!isComposite?<section className="form-section"><h2>连接配置</h2><div className="field-row"><label className="field">接入方式<select value={definition.connector.kind} onChange={e=>update({connector:{...definition.connector,kind:e.target.value as FunctionDefinition['connector']['kind']}})}><option value="http">HTTP / OpenAPI</option><option value="dify">Dify 工作流</option><option value="hiagent">HiAgent 工作流</option>{definition.connector.kind==='fixture'&&<option value="fixture">模拟处理器</option>}</select></label><label className="field">执行效果<select value={definition.effect} onChange={e=>update({effect:e.target.value as FunctionDefinition['effect'],requires_confirmation:e.target.value==='WRITE'})}><option value="READ_ONLY">只读查询</option><option value="WRITE">写操作 · 必须确认</option></select></label></div>
        {definition.connector.kind!=='fixture'&&<><label className="field">接口地址 <span className="optional">发布必填</span><input value={definition.connector.endpoint??''} onChange={e=>update({connector:{...definition.connector,endpoint:e.target.value||null}})} placeholder="https://example.com/api/accounts/{account_id}/balance"/></label><div className="field-row"><label className="field">HTTP 方法<select value={definition.connector.method} onChange={e=>update({connector:{...definition.connector,method:e.target.value}})}>{['GET','POST',...(!['GET','POST'].includes(definition.connector.method)?[definition.connector.method]:[])].map(m=><option key={m}>{m}</option>)}</select></label><CredentialSelector api={api} workspace={workspace} kind={definition.connector.kind} value={definition.connector.credential_ref} onChange={credential_ref=>update({connector:{...definition.connector,credential_ref}})}/></div></>}
        {definition.effect==='WRITE'&&<Notice>写入前将展示具体对象与参数，并要求用户确认。</Notice>}
        </section>:!draftError&&<CompositeRuleEditor api={api} workspace={workspace} definition={definition} functions={functions} writable={writable} publishable={publishable} update={update} onPending={setCompositePending} onCommit={registerComposite}/>}
      </div>
      <div hidden={step!==1}>
        <InputParameters definition={definition} update={update} onValidityChange={setInputValid}/>
        {!draftError&&!isComposite&&<details className="advanced"><summary>接口参数如何传递</summary><HttpMappingEditor definition={definition} update={update}/></details>}
        {!draftError&&<OutputMappingEditor definition={definition} update={update} onValidityChange={setOutputValid}/>}
      </div></fieldset>
      <details className="advanced" open={advanced} onToggle={e=>setAdvanced(e.currentTarget.open)}><summary><Code2 size={14}/>完整 JSON 与低频配置</summary><label>FunctionDefinition<textarea className="code-input" rows={18} value={json} onChange={e=>{setJson(e.target.value);setValidation(null);}} spellCheck={false}/></label></details>
      </fieldset></form>
      <div hidden={step!==2}>
        <p className="muted small">填写测试输入后，使用顶部“执行测试”。通过同一份配置的实际执行测试后才能发布。</p>
        {sourceDirty&&<Notice>接口资料引用有未保存修改，请先保存或还原。</Notice>}
        {item&&sourceReview?.required&&<details open={!sourceReview.ready}><summary>资料核对{!sourceReview.ready?' · 需要处理':''}</summary><SourceReviewPanel api={api} workspace={workspace} item={item} writable={writable} dirty={dirty} busy={busy} onReview={review=>{if(review)setSourceReview(review);}} onDirtyChange={setSourceDirty} onBusyChange={setSourceBusy} onChanged={async saved=>{setValidation(null);await onSaved(saved);}}/></details>}
          <SchemaTestForm schema={definition.input_schema} value={testArgs} onChange={setTestArgs} onValidityChange={setTestValid}/>
          {isComposite&&<div className="business-mapping-grid"><label className="field">测试模型<select aria-label="组合测试模型" value={testModel} onChange={e=>setTestModel(e.target.value)}><option value="">选择已配置模型</option>{models.map(model=><option key={model.id} value={model.id}>{model.name}</option>)}</select></label><label className="field">测试问题<input aria-label="组合测试问题" value={testGoal} onChange={e=>setTestGoal(e.target.value)} placeholder={definition.name||'例如：为客户准备拜访材料'}/></label></div>}
          {definition.effect==='WRITE'&&<label className="checkbox"><input type="checkbox" checked={allowEffects} onChange={e=>setAllowEffects(e.target.checked)}/>允许在测试环境办理写入（实际对象与参数仍须确认）</label>}
          {sourceReview?.api_source&&<label className="field">预期失败样例参数（JSON）<textarea rows={3} className="code-input" value={failureArgs} onChange={e=>setFailureArgs(e.target.value)}/></label>}
          {item && (
            <section className="validation-panel">
              <h3>测试结果</h3>
              {dirty&&<p className="muted small">执行测试会先保存当前修改；上次结果不会用于发布。</p>}
              {validation && (
                <div className="validation-result">
                  <Notice kind={validation.valid&&validation.execution_test?.passed ? "success" : executionPending?"info":"error"}>
                    {validation.valid&&validation.execution_test?.passed
                      ? "当前配置已通过执行测试"
                      : executionPending ? "测试已受理，正在等待执行完成" : validation.valid ? "配置检查通过，尚无成功执行测试记录" : "测试未通过，请处理下列问题"}
                  </Notice>
                  {validation.errors.map((message) => (
                    <p key={message}>{message}</p>
                  ))}
                  {executionPending&&<p role="status">测试正在执行，页面只查询已有任务，不重复提交。</p>}{executionUnknown&&<Notice kind="error">远端结果未知，请核对原业务结果；不能直接重试本次执行。</Notice>}{(executionUnknown||executionPending)&&<button type="button" className="text-button" onClick={()=>void api.get<NonNullable<Validation["execution_test"]>>(`${management(workspace)}/functions/${item.id}/execution-test${testingFailure?"?failure=true":""}`).then(applyExecutionResult).catch(cause=>setError(errorText(cause)))}>刷新测试状态</button>}{activeTest?.status==="WAITING_CONFIRMATION"&&activeTest.confirmation&&<section className="function-test-confirmation"><h4>确认本次测试操作</h4><strong>{activeTest.confirmation.name}</strong><dl>{Object.entries(activeTest.confirmation.arguments).map(([name,value])=><div key={name}><dt>{businessLabel(name)}</dt><dd>{valueText(value)}</dd></div>)}</dl><p className="muted small">以上为本次计划解析出的实际参数。确认后才执行业务写入。</p><div className="button-row"><button type="button" className="button primary" disabled={busy} onClick={()=>void confirmExecution(true)}>确认本次测试写入</button><button type="button" className="button" disabled={busy} onClick={()=>void confirmExecution(false)}>拒绝本次测试写入</button></div></section>}{activeTest?.error&&<p className="field-error">{activeTest.error}</p>}{validation.failure_execution_test&&<p role="status">失败场景测试：{validation.failure_execution_test.passed?"已验证预期业务失败":validation.failure_execution_test.status==="WAITING_CONFIRMATION"?"待确认本次测试操作":"未通过或未完成"}</p>}
                  {validation.execution_test?.passed&&<div className="function-test-result"><div className="function-test-result-header"><strong>返回结果</strong><button type="button" className="button" disabled={busy||!validCurrent} onClick={()=>void saveOutputExample()}>保存为输出案例</button><Help text="保存后，当前空间的功能维护者可查看这个案例。请只使用可共享的安全测试数据。"/></div>{validation.execution_test.data!==undefined?<BusinessResultContent data={{component:'detail',data:validation.execution_test.data}}/>:<button type="button" className="text-button" disabled={busy} onClick={()=>void api.get<NonNullable<Validation["execution_test"]>>(`${management(workspace)}/functions/${item.id}/execution-test`).then(applyExecutionResult).catch(cause=>setError(errorText(cause)))}>查看本次测试结果</button>}</div>}
                  <details><summary>查看检查详情</summary><pre>
                    {JSON.stringify(
                      { checks: validation.checks, execution_status:validation.execution_test?.status??'未执行' },
                      null,
                      2,
                    )}
                  </pre></details>
                </div>
              )}
            </section>
          )}

        {!!definition.output_examples?.length&&<details className="function-output-example"><summary>输出案例 · {definition.output_examples.length}</summary>{definition.output_examples.map((example,index)=><pre key={index}>{JSON.stringify(example,null,2)}</pre>)}</details>}
      </div></div>
      <aside className="editor-guide"><h3>{['从业务问题开始','把输入输出说清楚','先验证，再发布'][step]}</h3><p>{['用业务名称说明功能用途。连接、规则和映射可保存为草稿后继续完善。','标出必填参数、类型和示例；让实体、动作与结果字段对应业务含义。','验证已保存的修订。草稿修改不会影响正在使用的发布版本。'][step]}</p><div className="example-block"><span>配置进度</span><strong>{step+1} / 3</strong></div></aside>
    </div>
    <footer className="editor-footer"><span>第 {step+1} 步，共 3 步</span><div>{step>0&&<button className="button" disabled={busy||importBusy||importMode} onClick={()=>next(step-1)}>上一步</button>}{step<2&&<button className="button primary" disabled={busy||importBusy||importMode} onClick={()=>next(step+1)}>下一步<ArrowRight size={14}/></button>}{item&&<button type="button" className="danger-link" disabled={!writable||busy||item.status==='disabled'} onClick={()=>setShowDisable(true)}>停用功能</button>}</div></footer>
    {historyOpen&&item&&<VersionHistoryDialog api={api} versions={historyVersions} selectedId={historySelection} onSelect={setHistorySelection} onClose={()=>setHistoryOpen(false)} busy={historyLoading} auditAllowed={admin} resourceId={item.id} auditPath={`${management(workspace)}/audit?resource_id=${encodeURIComponent(item.id)}`}>{historyError?<Notice kind="error">{historyError}</Notice>:undefined}</VersionHistoryDialog>}
    {showDisable&&item&&<Modal title="停用功能？" onClose={()=>setShowDisable(false)}><div className="modal-body">停用后，所有智能体中的此功能立即不可调用。历史版本与记录保留。</div><footer className="modal-footer"><button className="button" onClick={()=>setShowDisable(false)}>取消</button><button className="button primary" disabled={busy} onClick={()=>void perform(async()=>{await onSaved(await api.post<Capability>(`${management(workspace)}/functions/${item.id}/disable`));setShowDisable(false);})}>确认停用</button></footer></Modal>}
  </section>;
}
