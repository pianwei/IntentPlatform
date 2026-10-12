import type { FunctionDefinition } from "../../shared/types";
import { useState, useEffect } from "react";
import { SchemaFieldsEditor, type FieldSchema } from "./SchemaFieldsEditor";

function editExampleField(value: unknown, path: string[], before: string, after?: string): unknown {
  if (path[0] === "$items") return Array.isArray(value) ? value.map(item => editExampleField(item, path.slice(1), before, after)) : value;
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const fields = value as Record<string, unknown>;
  if (path.length) {
    const [key, ...rest] = path;
    return Object.hasOwn(fields, key) ? { ...fields, [key]: editExampleField(fields[key], rest, before, after) } : value;
  }
  return Object.fromEntries(Object.entries(fields).flatMap(([key, item]) => key !== before ? [[key, item]] : after === undefined ? [] : [[after, item]]));
}

function editExamples(examples: Record<string, unknown>[], path: string[], before: string, after?: string) {
  return examples.map(example => Object.hasOwn(example, "arguments") ? { ...example, arguments: editExampleField(example.arguments, path, before, after) } : example);
}

export function InputParameters({ definition, update, onValidityChange }: { definition: FunctionDefinition; update: (patch: Partial<FunctionDefinition>) => void;onValidityChange?:(valid:boolean)=>void }) {
  const [error,setError]=useState("");
  const [noInput,setNoInput]=useState(()=>!Object.keys(definition.input_schema.properties??{}).length);
  useEffect(()=>{if(Object.keys(definition.input_schema.properties??{}).length)setNoInput(false);},[definition.input_schema]);
  const [pendingRemoval,setPendingRemoval]=useState<{schema:FieldSchema;path:string[];name:string}|null>(null);
  function rename(schema: FieldSchema, path: string[], before: string, after: string) {
    setError("");
    const source = [...path.filter(key => key !== "$items"), before].join("."), target = [...path.filter(key => key !== "$items"), after].join(".");
    const patch: Partial<FunctionDefinition> = {
      input_schema: schema,
      positive_examples: editExamples(definition.positive_examples, path, before, after),
      negative_examples: editExamples(definition.negative_examples, path, before, after),
      preconditions: definition.preconditions.map(condition => condition && typeof condition === "object" && !Array.isArray(condition) && typeof (condition as { field?: unknown }).field === "string" ? {
        ...condition, field: (condition as { field: string }).field === source ? target : (condition as { field: string }).field.startsWith(`${source}.`) ? `${target}${(condition as { field: string }).field.slice(source.length)}` : (condition as { field: string }).field,
      } : condition),
    };
    if (path.length) { update(patch); return; }
    const names = <T,>(value: Record<string, T> | undefined) => value && Object.fromEntries(Object.entries(value).map(([name, item]) => [name === before ? after : name, item]));
    const mapping = definition.connector.http_mapping;
    update({ ...patch,
      argument_bindings: names(definition.argument_bindings), parameter_semantics: names(definition.parameter_semantics),
      connector: { ...definition.connector, ...(mapping ? { http_mapping: Object.fromEntries(Object.entries(mapping).map(([location, fields]) => [location, Object.fromEntries(Object.entries(fields).map(([wire, argument]) => [wire, argument === before ? after : argument]))])) as NonNullable<FunctionDefinition["connector"]["http_mapping"]> } : {}) },
    });
  }
  function remove(schema: FieldSchema, path: string[], name: string) {
    const source = [...path.filter(key => key !== "$items"), name].join(".");
    if(definition.preconditions.some(condition=>condition&&typeof condition==="object"&&typeof (condition as {field?:unknown}).field==="string"&&((condition as {field:string}).field===source||(condition as {field:string}).field.startsWith(`${source}.`)))){setError("该参数仍被前置条件使用，请先调整前置条件再移除。");return;}
    if(!path.length&&(definition.argument_bindings?.[name]||definition.parameter_semantics?.[name]||Object.values(definition.connector.http_mapping??{}).some(fields=>Object.values(fields).includes(name)))){setPendingRemoval({schema,path,name});return;}
    applyRemoval(schema,path,name);
  }
  function applyRemoval(schema:FieldSchema,path:string[],name:string){
    setError("");setPendingRemoval(null);
    const patch: Partial<FunctionDefinition> = { input_schema: schema,
      positive_examples: editExamples(definition.positive_examples, path, name),
      negative_examples: editExamples(definition.negative_examples, path, name) };
    if (path.length) { update(patch); return; }
    const without = <T,>(value: Record<string, T> | undefined) => value && Object.fromEntries(Object.entries(value).filter(([key]) => key !== name));
    const mapping = definition.connector.http_mapping;
    update({ ...patch, argument_bindings: without(definition.argument_bindings), parameter_semantics: without(definition.parameter_semantics),
      connector: { ...definition.connector, ...(mapping ? { http_mapping: Object.fromEntries(Object.entries(mapping).map(([location, fields]) => [location, Object.fromEntries(Object.entries(fields).filter(([, argument]) => argument !== name))])) as NonNullable<FunctionDefinition["connector"]["http_mapping"]> } : {}) } });
  }
  return <section className="form-section"><div className="section-caption"><h2>输入参数</h2><span className="optional">选填</span></div><label className="checkbox"><input type="checkbox" aria-label="无需输入参数" checked={noInput} onChange={e=>{if(e.target.checked&&Object.keys(definition.input_schema.properties??{}).length){setError("请先逐项移除现有参数及其关联配置，再选择无需输入参数。");return;}setError("");setNoInput(e.target.checked);if(e.target.checked)update({input_schema:{type:"object",properties:{},required:[],additionalProperties:false}});}}/>无需输入参数</label>{noInput&&<p className="muted small">测试将使用空参数。身份与对象权限仍由服务端校验。</p>}{error&&<p role="alert" className="field-error">{error}</p>}{pendingRemoval&&<div className="inline-confirm"><p>移除「{pendingRemoval.name}」会一并移除它的对象绑定、单位规则和接口参数映射。</p><button type="button" className="button" onClick={()=>setPendingRemoval(null)}>保留参数</button><button type="button" className="button" onClick={()=>applyRemoval(pendingRemoval.schema,pendingRemoval.path,pendingRemoval.name)}>确认移除参数与关联配置</button></div>}<div hidden={noInput}><SchemaFieldsEditor schema={definition.input_schema as FieldSchema} label="输入参数" mode="input" onValidityChange={onValidityChange} onChange={input_schema => update({ input_schema })} onRename={rename} onRemove={remove} maxObjectDepth={2} maxArrayDepth={1} /></div></section>;
}
