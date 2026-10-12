import type { FunctionDefinition } from "../../shared/types";
import { useState, useEffect } from "react";
import { SchemaFieldsEditor, type FieldSchema } from "./SchemaFieldsEditor";

function stringPaths(node: FieldSchema, prefix: string[] = []): string[] {
  const found: string[] = [];
  if (node.type !== "object" || !node.properties) return found;
  for (const [name, child] of Object.entries(node.properties)) {
    const path = [...prefix, name];
    if (child.type === "string") found.push(path.join("."));
    else if (child.type === "object") found.push(...stringPaths(child, path));
  }
  return found;
}
function markdownPaths(node: FieldSchema, prefix: string[] = []): string[] {
  if (node.type !== "object" || !node.properties) return [];
  return Object.entries(node.properties).flatMap(([name, child]) => {
    const path = [...prefix, name];
    if (name === "markdown" && child.type === "string") return [path.join(".")];
    return child.type === "object" ? markdownPaths(child, path) : [];
  });
}
function legacyResponseMode(definition: FunctionDefinition, schema: FieldSchema): "structured" | "summary" | "content" {
  const presentation = definition.presentation;
  if (presentation.component === "markdown" && markdownPaths(schema).length === 1) return "content";
  if (presentation.component === "table") return "structured";
  const properties = schema.properties ?? {};
  if (definition.execution_kind !== "composite" && Object.values(properties).some(value =>
    value.type === "array" && value.items?.type === "object")) return "structured";
  if (markdownPaths(schema).length === 1) return "content";
  return "summary";
}

export function OutputMappingEditor({ definition, update, onValidityChange }: { definition: FunctionDefinition; update: (patch: Partial<FunctionDefinition>) => void;onValidityChange?:(valid:boolean)=>void }) {
  const [error,setError]=useState("");
  const [confirmEmpty,setConfirmEmpty]=useState(false);
  const noOutput=definition.output_mode==="none";
  useEffect(()=>{if(noOutput)onValidityChange?.(true);},[noOutput,onValidityChange]);
  const schema = (definition.output_schema ?? { type: "object", properties: {}, required: [], additionalProperties: false }) as FieldSchema;
  const availableContentPaths = stringPaths(schema);
  const legacyMode = legacyResponseMode(definition, schema);
  const responseMode = definition.presentation.response_mode ?? legacyMode;
  function change(next: FieldSchema, path?: string[], before?: string, after?: string) {
    setError("");
    const previous = definition.presentation.field_map ?? {};
    const map: Record<string, string> = {};
    for (const name of Object.keys(next.properties ?? {})) {
      const oldName = !path?.length && before && name === after ? before : name;
      const label = Object.entries(previous).find(([, value]) => value === oldName)?.[0] ?? name;
      map[Object.hasOwn(map, label) ? name : label] = name;
    }
    let contentPath = definition.presentation.content_path ?? (legacyMode === "content" ? markdownPaths(schema)[0] ?? null : null);
    if (before && after && contentPath) {
      const prefix = (path ?? []).filter(key => key !== "$items");
      const oldPath = [...prefix, before].join("."), newPath = [...prefix, after].join(".");
      if (contentPath === oldPath || contentPath.startsWith(`${oldPath}.`)) contentPath = `${newPath}${contentPath.slice(oldPath.length)}`;
    }
    const nextContentPaths = stringPaths(next);
    const presentation = { ...definition.presentation, field_map: map, content_path: contentPath };
    if (responseMode === "content" && (!contentPath || !nextContentPaths.includes(contentPath))) {
      presentation.response_mode = "structured";
      presentation.content_path = null;
      presentation.component = "detail";
    }
    const patch: Partial<FunctionDefinition> = { output_schema: next, presentation };
    if (!path?.length && before) {
      if (definition.composite?.final_output_mapping) patch.composite = { ...definition.composite, final_output_mapping: Object.fromEntries(Object.entries(definition.composite.final_output_mapping).flatMap(([name, value]) => name === before ? after ? [[after, value]] : [] : [[name, value]])) };
    }
    if(before&&after&&definition.result_bindings){const source=[...(path??[]).filter(key=>key!=="$items"),before].join("."),target=[...(path??[]).filter(key=>key!=="$items"),after].join(".");const rename=(value:string)=>value===source?target:value.startsWith(`${source}.`)?`${target}${value.slice(source.length)}`:value;patch.result_bindings=definition.result_bindings.map(binding=>({...binding,collection_path:rename(binding.collection_path),id_field:rename(`${binding.collection_path}.${binding.id_field}`).replace(`${rename(binding.collection_path)}.`,""),label_field:rename(`${binding.collection_path}.${binding.label_field}`).replace(`${rename(binding.collection_path)}.`,"")}));}
    update(patch);
  }
  const composite = definition.execution_kind === "composite";
  function chooseResponseMode(mode: "structured" | "summary" | "content") {
    if (mode === "content" && !availableContentPaths.length) {
      setError("当前没有可展示的文本字段；请先添加一个文本输出字段。");
      return;
    }
    setError("");
    const current = definition.presentation.content_path;
    const contentPath = mode === "content"
      ? current && availableContentPaths.includes(current)
        ? current
        : markdownPaths(schema)[0]
        ?? availableContentPaths[0]
      : null;
    const component = mode === "content"
      ? contentPath?.split(".").at(-1) === "markdown" ? "markdown" : "text"
      : "detail";
    update({ presentation: { ...definition.presentation, response_mode: mode, content_path: contentPath, component } });
  }
  function emptyOutput(){
    setConfirmEmpty(false);setError("");
    update({output_mode:"none",output_schema:{type:"object",properties:{},required:[],additionalProperties:false},presentation:{...definition.presentation,component:"detail",field_map:{},files_field:null,response_mode:"structured",content_path:null},result_bindings:[],...(definition.composite?{composite:{...definition.composite,final_output_mapping:{}}}:{} )});
  }
  function chooseMode(mode:"fields"|"none"){
    setError("");
    if(mode==="fields"){update({output_mode:"fields"});return;}
    const references=[definition.result_bindings?.length?"返回对象绑定":"",definition.presentation.files_field?"文件输出":"",Object.keys(definition.composite?.final_output_mapping??{}).length?"组合结果映射":""].filter(Boolean);
    if(references.length){setError(`请先调整${references.join("、")}，再选择无业务数据返回。已有配置已保留。`);return;}
    if(Object.keys(schema.properties??{}).length||Object.keys(definition.presentation.field_map??{}).length){setConfirmEmpty(true);return;}
    emptyOutput();
  }
  function remove(next:FieldSchema,path:string[],name:string){const removed=[...path.filter(key=>key!=="$items"),name].join(".");if(definition.result_bindings?.some(binding=>[binding.collection_path,`${binding.collection_path}.${binding.id_field}`,`${binding.collection_path}.${binding.label_field}`].some(value=>value===removed||value.startsWith(`${removed}.`)))){setError("该输出字段仍被结果对象绑定使用，请先调整业务映射再移除。");return;}change(next,path,name);}
  return <section className="form-section" aria-label="业务输出字段"><div className="section-caption"><h2>返回内容</h2><span className="optional">选填</span></div><label className="field">返回方式<select aria-label="返回方式" value={noOutput?"none":"fields"} onChange={e=>chooseMode(e.target.value as "fields"|"none")}><option value="fields">业务字段或文件</option><option value="none">无业务数据返回 · 仅执行状态</option></select></label>{error&&<p role="alert" className="field-error">{error}</p>}{confirmEmpty&&<div className="inline-confirm"><p>当前返回字段将从草稿中移除。已发布版本不受影响。</p><button type="button" className="button" onClick={()=>setConfirmEmpty(false)}>保留返回字段</button><button type="button" className="button" onClick={emptyOutput}>确认仅返回执行状态</button></div>}{noOutput?<p className="muted small">不向用户或模型提供接口响应内容，仍按接口约定判断执行状态。</p>:<><SchemaFieldsEditor schema={schema} label="输出字段" mode="output" onValidityChange={onValidityChange} onChange={change} onRename={change} onRemove={remove} maxObjectDepth={composite ? 4 : 2} maxArrayDepth={composite ? 3 : 1} />
    {!composite&&<label className="checkbox"><input type="checkbox" checked={definition.presentation.files_field === "files"} onChange={e => update({ presentation: { ...definition.presentation, files_field: e.target.checked ? "files" : null } })} />包含可下载文件</label>}
    <label className="field">用户答复方式<select aria-label="用户答复方式" value={responseMode} onChange={e=>chooseResponseMode(e.target.value as "structured"|"summary"|"content")}><option value="structured">展示业务字段/表格</option><option value="summary">由模型归纳答复</option><option value="content" disabled={!availableContentPaths.length}>展示文本或 Markdown</option></select></label>
    {responseMode === "content" && <label className="field">展示字段<select aria-label="展示字段" value={definition.presentation.content_path ?? (legacyMode === "content" ? markdownPaths(schema)[0] ?? "" : "")} onChange={e=>update({presentation:{...definition.presentation,content_path:e.target.value,component:e.target.value.split(".").at(-1)==="markdown"?"markdown":"text"}})}>{availableContentPaths.map(path=><option key={path} value={path}>{path}</option>)}</select></label>}
    <p className="muted small">结构化展示不重复调用模型总结；模型归纳时只展示答复；文本/Markdown 模式只显示所选字段。结果始终受输出字段契约约束。</p></>}
  </section>;
}
