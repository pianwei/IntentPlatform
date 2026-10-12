import type { FunctionDefinition } from "../../shared/types";
import { useState, useEffect } from "react";
import { SchemaFieldsEditor, type FieldSchema } from "./SchemaFieldsEditor";

export function OutputMappingEditor({ definition, update, onValidityChange }: { definition: FunctionDefinition; update: (patch: Partial<FunctionDefinition>) => void;onValidityChange?:(valid:boolean)=>void }) {
  const [error,setError]=useState("");
  const [confirmEmpty,setConfirmEmpty]=useState(false);
  const noOutput=definition.output_mode==="none";
  useEffect(()=>{if(noOutput)onValidityChange?.(true);},[noOutput,onValidityChange]);
  const schema = (definition.output_schema ?? { type: "object", properties: {}, required: [], additionalProperties: false }) as FieldSchema;
  function change(next: FieldSchema, path?: string[], before?: string, after?: string) {
    setError("");
    const previous = definition.presentation.field_map ?? {};
    const map: Record<string, string> = {};
    for (const name of Object.keys(next.properties ?? {})) {
      const oldName = !path?.length && before && name === after ? before : name;
      const label = Object.entries(previous).find(([, value]) => value === oldName)?.[0] ?? name;
      map[Object.hasOwn(map, label) ? name : label] = name;
    }
    const patch: Partial<FunctionDefinition> = { output_schema: next, presentation: { ...definition.presentation, field_map: map } };
    if (!path?.length && before) {
      if (definition.composite?.final_output_mapping) patch.composite = { ...definition.composite, final_output_mapping: Object.fromEntries(Object.entries(definition.composite.final_output_mapping).flatMap(([name, value]) => name === before ? after ? [[after, value]] : [] : [[name, value]])) };
    }
    if(before&&after&&definition.result_bindings){const source=[...(path??[]).filter(key=>key!=="$items"),before].join("."),target=[...(path??[]).filter(key=>key!=="$items"),after].join(".");const rename=(value:string)=>value===source?target:value.startsWith(`${source}.`)?`${target}${value.slice(source.length)}`:value;patch.result_bindings=definition.result_bindings.map(binding=>({...binding,collection_path:rename(binding.collection_path),id_field:rename(`${binding.collection_path}.${binding.id_field}`).replace(`${rename(binding.collection_path)}.`,""),label_field:rename(`${binding.collection_path}.${binding.label_field}`).replace(`${rename(binding.collection_path)}.`,"")}));}
    update(patch);
  }
  const composite = definition.execution_kind === "composite";
  function emptyOutput(){
    setConfirmEmpty(false);setError("");
    update({output_mode:"none",output_schema:{type:"object",properties:{},required:[],additionalProperties:false},presentation:{...definition.presentation,field_map:{},files_field:null},result_bindings:[],...(definition.composite?{composite:{...definition.composite,final_output_mapping:{}}}:{} )});
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
    <p className="muted small">回答使用 Markdown。只使用已配置字段；也可以仅返回文件。</p></>}
  </section>;
}
