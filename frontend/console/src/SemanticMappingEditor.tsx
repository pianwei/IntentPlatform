import { useEffect, useState } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";
import type { FunctionDefinition, FunctionOptions } from "../../shared/types";
import "./FunctionExperience.css";
import { CatalogItemEditor } from "./BusinessCatalogPage";
import type { CatalogItem, CatalogKind } from "./businessCatalog";

function catalogGroups<T>(items: T[], source: (item: T) => string) {
  const groups = new Map<string, T[]>();
  for (const item of items) { const name = source(item); groups.set(name, [...(groups.get(name) ?? []), item]); }
  return Array.from(groups, ([name, items]) => ({ name, items }));
}
function actionSource(item: FunctionOptions["actions"][number]) {
  if (item.source_name?.trim()) return item.source_name;
  if (item.key.startsWith("ZY_")) return "智驭业务动作";
  if (["READ", "LIST", "COMPARE", "SUMMARIZE", "GENERATE", "CREATE"].includes(item.key)) return "基础业务动作";
  return "其他业务动作";
}

export function SemanticMappingEditor({ api, workspace, definition: d, update, writable=false, onDirtyChange, onBusyChange }: { api: ApiClient; workspace: string; definition: FunctionDefinition; update: (patch: Partial<FunctionDefinition>) => void; writable?:boolean;onDirtyChange?:(value:boolean)=>void;onBusyChange?:(value:boolean)=>void }) {
  const [options, setOptions] = useState<FunctionOptions | null>(null), [error, setError] = useState(""), [loading, setLoading] = useState(true);
  const [creating,setCreating]=useState<CatalogKind|null>(null),[catalogDirty,setCatalogDirty]=useState(false),[catalogBusy,setCatalogBusy]=useState(false),[leaveCatalog,setLeaveCatalog]=useState(false);
  useEffect(()=>{onDirtyChange?.(catalogDirty);},[catalogDirty,onDirtyChange]);
  useEffect(()=>{onBusyChange?.(catalogBusy);},[catalogBusy,onBusyChange]);
  useEffect(() => { let active = true; setLoading(true); setOptions(null); api.get<FunctionOptions>(`${management(workspace)}/function-options`).then(value => { if (active) { setOptions(value); setError(""); } }).catch(cause => { if (active) setError(errorText(cause)); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [api, workspace]);
  const entities = options?.entities ?? [], actions = options?.actions ?? [];
  const entityGroups = catalogGroups(entities, item => item.source_name?.trim() || "业务对象");
  const actionGroups = catalogGroups(actions, actionSource);
  const entityOptions = (disabled?: (item: FunctionOptions["entities"][number]) => boolean, showRecognition = false) => entityGroups.map(group => <optgroup key={group.name} label={group.name}>{group.items.map(item => <option key={item.id} value={item.id} disabled={disabled?.(item)}>{item.label}{showRecognition && !options?.resolvers.some(resolver => resolver.entity_type === item.id) ? "（暂不可自动识别）" : ""}</option>)}</optgroup>);
  const roles = d.participant_roles ?? [];
  const inputs = Object.entries((d.input_schema.properties ?? {}) as Record<string, Record<string, unknown>>);
  const outputFields = (d.output_schema?.properties ?? {}) as Record<string, Record<string, unknown>>;
  const entityName = (type: string) => entities.find(entity => entity.id === type)?.label ?? "未配置对象";
  function entity(type: string) {
    if (!options) return;
    update({ object_type: type, result_type: (options.result_types??[]).find(result => result.id === `${type}Result`)?.id ?? options.default_result_type,
      compiler_version: options.compiler_version, domain_refs: options.domain_refs ?? (options.default_domain_ref ? [options.default_domain_ref] : d.domain_refs),
      authoring_version: "function-v2" });
  }
  function bindParameter(name: string, type: string) {
    const next = { ...d.argument_bindings };
    if (!type) { delete next[name]; update({ argument_bindings: next }); return; }
    const resolver = options?.resolvers.find(item => item.entity_type === type);
    const role = type === d.object_type ? "target" : name.replace(/[^a-z0-9_]/g, "_").replace(/^[^a-z]/, "entity_");
    const current = roles.find(item => item.role === role);
    const required = ((d.input_schema.required ?? []) as string[]).includes(name);
    const participant = { role, type, required, min_count: required ? 1 : 0, max_count: 1 };
    next[name] = { role, resolver_ref: resolver?.reference ?? "" };
    update({ argument_bindings: next, participant_roles: current ? roles.map(item => item.role === role ? participant : item) : [...roles, participant] });
  }
  async function bootstrap() { setLoading(true); try { setOptions(await api.post<FunctionOptions>(`${management(workspace)}/function-options/bootstrap`)); setError(""); } catch (cause) { setError(errorText(cause)); } finally { setLoading(false); } }
  async function receiveCatalog(item:CatalogItem){const next=await api.get<FunctionOptions>(`${management(workspace)}/function-options`);setOptions(next);if(item.kind==="object")update({object_type:item.key,result_type:next.result_types.find(result=>result.id===`${item.key}Result`)?.id??next.default_result_type,compiler_version:next.compiler_version,domain_refs:next.domain_refs??(next.default_domain_ref?[next.default_domain_ref]:d.domain_refs),authoring_version:"function-v2"});else update({action:item.key,effect:item.effect??"READ_ONLY",requires_confirmation:item.effect==="WRITE",authoring_version:"function-v2"});setCreating(null);setCatalogDirty(false);setError("");}
  if(creating)return <section className="form-section catalog-inline-create"><div className="section-caption"><h2>新增{creating==="object"?"业务对象":"动作"}</h2><button type="button" className="text-button" disabled={catalogBusy} onClick={()=>catalogDirty?setLeaveCatalog(true):setCreating(null)}>返回功能配置</button></div><CatalogItemEditor api={api} workspace={workspace} kind={creating} onSaved={receiveCatalog} onClose={()=>{setCreating(null);setCatalogDirty(false);}} onDirtyChange={setCatalogDirty} onBusyChange={setCatalogBusy}/>{leaveCatalog&&<div className="inline-confirm"><p>有未保存的目录修改。</p><button type="button" className="button" onClick={()=>setLeaveCatalog(false)}>继续编辑</button><button type="button" className="button" onClick={()=>{setLeaveCatalog(false);setCatalogDirty(false);setCreating(null);}}>放弃修改并返回</button></div>}</section>;
  return <section className="form-section business-mapping-editor"><div className="section-caption"><h2>业务对象与动作</h2></div>
    {error && <Notice kind="error">业务目录读取失败：{error}。当前配置保留。</Notice>}
    {loading && <p className="muted small">正在读取可用业务类型…</p>}
    {!loading && options && !entities.length && <button type="button" className="button" onClick={() => void bootstrap()}>准备默认业务类型</button>}
    <div className="business-mapping-grid"><div><label className="field">业务对象 <em>*</em><select aria-label="业务对象" value={d.object_type} disabled={loading || !options} onChange={e => entity(e.target.value)}><option value="">选择业务对象</option>{d.object_type && !entities.some(item => item.id === d.object_type) && <option value={d.object_type} disabled>现有对象（不可新增选择）</option>}{entityOptions()}</select></label>{writable&&<button type="button" className="text-button" disabled={loading} onClick={()=>{setCreating("object");setLeaveCatalog(false);}}>＋ 新增业务对象</button>}</div>
      <div><label className="field">动作 <em>*</em><select aria-label="动作" value={d.action} disabled={loading || !options} onChange={e => { const selected = actions.find(item => item.key === e.target.value); if (selected) update({ action: selected.key, effect: selected.effect, requires_confirmation: selected.effect === "WRITE", authoring_version: "function-v2" }); }}><option value="">选择动作</option>{d.action && !actions.some(item => item.key === d.action) && <option value={d.action} disabled>现有动作（不可新增选择）</option>}{actionGroups.map(group => <optgroup key={group.name} label={group.name}>{group.items.map(item => <option key={item.key} value={item.key}>{item.name}{group.items.filter(other => other.name === item.name).length > 1 ? `（${item.description || item.key}）` : ""}</option>)}</optgroup>)}</select></label>{writable&&<button type="button" className="text-button" disabled={loading} onClick={()=>{setCreating("action");setLeaveCatalog(false);}}>＋ 新增动作</button>}</div></div>
    <p className="muted small">{d.object_type && d.action ? `${entityName(d.object_type)} → ${actions.find(item => item.key === d.action)?.name ?? "动作"} → ${d.name || "当前功能"}` : "对象说明操作谁，动作说明做什么。发布后用于意图识别与能力筛选。"}</p>
    <details className="advanced"><summary>参数与业务对象如何关联（选填）</summary>
      {!inputs.length && <p className="muted small">先添加输入参数，再指定需要识别和授权的业务对象。</p>}
      {inputs.map(([name, field]) => {
        const binding = d.argument_bindings?.[name], role = roles.find(item => item.role === binding?.role), semantic = d.parameter_semantics?.[name];
        return <div className="business-mapping-card" key={name}><strong>{String(field.description || name)}</strong><div className="business-mapping-grid">
          {(field.type === "string"||binding) && <label>对应业务对象<select aria-label={`参数对应对象 ${name}`} value={role?.type ?? ""} onChange={e => bindParameter(name, e.target.value)}><option value="">普通参数</option>{entityOptions(item => field.type !== "string" || !options?.resolvers.some(resolver => resolver.entity_type === item.id), true)}</select>{field.type!=="string"&&<span className="field-error">对象编号需要文本类型，请恢复类型或改为普通参数解除绑定。</span>}</label>}
          {["string", "number", "integer"].includes(String(field.type)) && <label>参数含义<select aria-label={`参数含义 ${name}`} value={semantic?.kind ?? ""} onChange={e => { const next = { ...d.parameter_semantics }; if (e.target.value) next[name] = { kind: e.target.value as "quantity" | "date" | "datetime", ...(e.target.value === "date" ? { allow_relative: true } : {}) }; else delete next[name]; update({ parameter_semantics: next }); }}><option value="">普通值</option>{field.type === "string" ? <><option value="date">日期（可识别相对日期）</option><option value="datetime">日期时间</option></> : <option value="quantity">带单位的数量</option>}</select></label>}
          {semantic?.kind === "quantity" && <label>单位<input aria-label={`参数单位 ${name}`} value={semantic.unit ?? ""} placeholder="例如 CNY" onChange={e => update({ parameter_semantics: { ...d.parameter_semantics, [name]: { ...semantic, unit: e.target.value } } })} /></label>}
        </div></div>;
      })}
      {!!roles.length && <section><h3>对象之间的关系</h3>{(d.relations ?? []).map((relation, index) => <div className="business-mapping-grid" key={index}><label>关系<select value={relation.name} onChange={e => update({ relations: (d.relations ?? []).map((item, i) => i === index ? { ...item, name: e.target.value } : item) })}><option value="">选择关系</option>{options?.relations.map(item => <option key={item.id} value={item.id}>{entityName(item.source_type)} → {item.label??item.id.split(":").at(-1)} → {entityName(item.target_type)}</option>)}</select></label>{(["source", "target"] as const).map((key, i) => <label key={key}>{i ? "目标对象" : "来源对象"}<select value={relation[key]} onChange={e => update({ relations: (d.relations ?? []).map((item, at) => at === index ? { ...item, [key]: e.target.value } : item) })}><option value="">选择对象</option>{roles.map(role => <option key={role.role} value={role.role}>{entityName(role.type)}</option>)}</select></label>)}<button type="button" className="text-button danger-link" onClick={() => update({ relations: (d.relations ?? []).filter((_, i) => i !== index) })}>移除关系</button></div>)}<button type="button" className="text-button" onClick={() => update({ relations: [...(d.relations ?? []), { name: "", source: "", target: "" }] })}>添加对象关系</button></section>}
      {Object.entries(outputFields).filter(([, field]) => field.type === "array" && (field.items as Record<string, unknown> | undefined)?.type === "object").map(([name, field]) => {
        const rows = ((field.items as Record<string, unknown>).properties ?? {}) as Record<string, unknown>, current = d.result_bindings?.find(item => item.collection_path === name);
        const setResult = (patch: Partial<NonNullable<FunctionDefinition["result_bindings"]>[number]>) => update({ result_bindings: [...(d.result_bindings ?? []).filter(item => item.collection_path !== name), { name, collection_path: name, entity_type: "", id_field: "", label_field: "", resolver_ref: "", ...current, ...patch }] });
        return <div className="business-mapping-card" key={name}><strong>“{name}”的返回对象</strong><div className="business-mapping-grid"><label>对象类型<select value={current?.entity_type ?? ""} onChange={e => { if (!e.target.value) update({ result_bindings: (d.result_bindings ?? []).filter(item => item.collection_path !== name) }); else setResult({ entity_type: e.target.value, resolver_ref: options?.resolvers.find(item => item.entity_type === e.target.value)?.reference ?? "" }); }}><option value="">普通结果</option>{entityOptions(item => !options?.resolvers.some(resolver => resolver.entity_type === item.id))}</select></label>{current && (["id_field", "label_field"] as const).map((key, i) => <label key={key}>{i ? "名称字段" : "编号字段"}<select value={current[key]} onChange={e => setResult({ [key]: e.target.value })}><option value="">选择字段</option>{Object.keys(rows).map(key => <option key={key}>{key}</option>)}</select></label>)}</div></div>;
      })}
      <p className="muted small">对象参数由服务端识别并校验访问权限；关系用于区分不同对象间的业务含义，返回对象可供后续问题引用。</p>
    </details>
  </section>;
}
