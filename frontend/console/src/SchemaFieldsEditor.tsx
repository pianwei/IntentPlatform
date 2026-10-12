import { useState, useEffect } from "react";
import { ChevronDown, Plus, Trash2 } from "lucide-react";
import "./FunctionExperience.css";

export type FieldSchema = Record<string, unknown> & {
  type?: string | string[];
  properties?: Record<string, FieldSchema>;
  required?: string[];
  items?: FieldSchema;
};
type Props = {
  schema: FieldSchema;
  label: string;
  mode: "input" | "output";
  onChange: (schema: FieldSchema) => void;
  onRename?: (schema: FieldSchema, path: string[], before: string, after: string) => void;
  onRemove?: (schema: FieldSchema, path: string[], name: string) => void;
  maxObjectDepth?: number;
  maxArrayDepth?: number;
  onValidityChange?: (valid:boolean)=>void;
};
const kinds = [["string", "文本"], ["integer", "整数"], ["number", "数字"], ["boolean", "是/否"], ["object", "对象"], ["array", "列表"]];
const objectSchema = (): FieldSchema => ({ type: "object", properties: {}, required: [], additionalProperties: false });
function newField(type: string, previous: FieldSchema = {}): FieldSchema {
  const description = typeof previous.description === "string" ? previous.description : "";
  if (type === "object") return { ...objectSchema(), description };
  if (type === "array") return { type, description, maxItems: 100, items: { type: "string", maxLength: 2000 } };
  return { type, description, ...(type === "string" ? { maxLength: 2000 } : {}) };
}
function exampleText(field: FieldSchema) {
  const example = Array.isArray(field.examples) ? field.examples[0] : field.example;
  return example === undefined ? "" : typeof example === "string" ? example : JSON.stringify(example);
}
export function SchemaFieldsEditor({ schema, label, mode, onChange, onRename, onRemove, maxObjectDepth = 2, maxArrayDepth = 1, onValidityChange }: Props) {
  const [error, setError] = useState("");
  useEffect(()=>{onValidityChange?.(!error);},[error,onValidityChange]);
  const root = schema.type === "object" && schema.properties && typeof schema.properties === "object" && !Array.isArray(schema.properties) ? schema : objectSchema();
  function change(next: FieldSchema) { onChange(next); }
  function at(node: FieldSchema, path: string[], patch: (field: FieldSchema) => FieldSchema): FieldSchema {
    if (!path.length) return patch(node);
    const [key, ...rest] = path;
    if (key === "$items") return { ...node, items: at(node.items ?? { type: "string", maxLength: 2000 }, rest, patch) };
    return { ...node, properties: { ...node.properties, [key]: at(node.properties?.[key] ?? {}, rest, patch) } };
  }
  function patch(path: string[], value: FieldSchema) { change(at(root, path, () => value)); }
  function add(path: string[], node: FieldSchema) {
    const properties = node.properties ?? {};
    let index = Object.keys(properties).length + 1;
    const prefix = mode === "input" ? "param" : "field";
    while (Object.hasOwn(properties, `${prefix}_${index}`)) index++;
    const name = `${prefix}_${index}`;
    patch(path, { ...node, type: "object", additionalProperties: false, properties: { ...properties, [name]: newField("string") }, required: node.required ?? [] });
  }
  function rename(path: string[], node: FieldSchema, before: string, after: string) {
    if (after !== before && Object.hasOwn(node.properties ?? {}, after)) { setError("字段名称已存在，请使用不同名称。"); return; }
    const value = { ...node, properties: Object.fromEntries(Object.entries(node.properties ?? {}).map(([key, field]) => [key === before ? after : key, field])), required: (node.required ?? []).map(key => key === before ? after : key) };
    const next = at(root, path, () => value); setError("");
    if (onRename) onRename(next, path, before, after); else onChange(next);
  }
  function remove(path: string[], node: FieldSchema, name: string) {
    const next = at(root, path, () => ({ ...node, properties: Object.fromEntries(Object.entries(node.properties ?? {}).filter(([key]) => key !== name)), required: (node.required ?? []).filter(key => key !== name) }));
    if (onRemove) onRemove(next, path, name); else change(next);
  }
  function detail(path: string[], field: FieldSchema, depth: number, arrayDepth: number) {
    const text = path.filter(key => key !== "$items").join(".");
    return <details className="field-options"><summary>约束与示例</summary><div className="field-options-grid">
      {field.type === "string" && <label>最大字符数<input aria-label={`${label}最大字符数 ${text}`} type="number" min="0" max="16000" value={String(field.maxLength ?? 2000)} onChange={e => patch(path, { ...field, maxLength: Number(e.target.value) })} /></label>}
      {field.type === "array" && <label>最大条数<input aria-label={`${label}最大条数 ${text}`} type="number" min="0" max="100" value={String(field.maxItems ?? 100)} onChange={e => patch(path, { ...field, maxItems: Number(e.target.value) })} /></label>}
      {["number", "integer"].includes(String(field.type)) && <><label>最小值<input type="number" value={field.minimum === undefined ? "" : String(field.minimum)} onChange={e => { const next = { ...field }; if (e.target.value === "") delete next.minimum; else next.minimum = Number(e.target.value); patch(path, next); }} /></label><label>最大值<input type="number" value={field.maximum === undefined ? "" : String(field.maximum)} onChange={e => { const next = { ...field }; if (e.target.value === "") delete next.maximum; else next.maximum = Number(e.target.value); patch(path, next); }} /></label></>}
      {<label className="span-2">示例值<input key={`${text}-${field.type}-${exampleText(field)}`} aria-label={`${label}示例 ${text}`} defaultValue={exampleText(field)} placeholder={field.type === "object" ? '{"字段":"示例"}' : field.type === "array" ? '["示例"]' : field.type === "boolean" ? "true 或 false" : "例如：客户名称"} onBlur={e => {
        const raw = e.target.value;
        try {
          const value = ["number", "integer", "boolean", "object", "array"].includes(String(field.type)) && raw ? JSON.parse(raw) : raw;
          const matches = !raw || (field.type === "number" ? typeof value === "number" : field.type === "integer" ? Number.isInteger(value) : field.type === "boolean" ? typeof value === "boolean" : field.type === "object" ? !!value && typeof value === "object" && !Array.isArray(value) : field.type === "array" ? Array.isArray(value) : true);
          if (!matches) throw new Error();
          setError("");patch(path, { ...field, examples: raw ? [value] : [] });
        } catch { setError(`“${text}”的示例需符合字段类型。`); }
      }} /></label>}
      {field.type === "string" && <label>日期格式<select value={String(field.format ?? "")} onChange={e => { const next = { ...field }; if (e.target.value) next.format = e.target.value; else delete next.format; patch(path, next); }}><option value="">普通文本</option><option value="date">日期</option><option value="date-time">日期时间</option></select></label>}
      {["string", "integer", "number", "boolean"].includes(String(field.type)) && <label className="span-2">可选值（选填）<input aria-label={`${label}可选值 ${text}`} key={`${text}-${field.type}-${JSON.stringify(field.enum)}`} defaultValue={Array.isArray(field.enum) ? JSON.stringify(field.enum) : ""} placeholder='例如 ["正常","暂停"]' onBlur={e => { try { const next = { ...field }; if (!e.target.value.trim()) delete next.enum; else { const values: unknown = JSON.parse(e.target.value); if (!Array.isArray(values) || !values.length || values.length > 100 || values.some(value => field.type === "string" ? typeof value !== "string" : field.type === "boolean" ? typeof value !== "boolean" : field.type === "integer" ? !Number.isInteger(value) : typeof value !== "number")) throw new Error(); next.enum = values; } setError("");patch(path, next); } catch { setError(`“${text}”的可选值需为符合字段类型的列表。`); } }} /></label>}
    </div>{field.type === "array" && <div className="array-item-editor"><label>列表项类型<select aria-label={`${label}列表项类型 ${text}`} value={typeof field.items?.type === "string" ? field.items.type : "complex"} onChange={e => patch(path, { ...field, items: newField(e.target.value, field.items) })}>{kinds.map(([kind, title]) => <option key={kind} value={kind} disabled={kind === "object" && depth + 1 > maxObjectDepth || kind === "array" && (depth + 1 > maxArrayDepth || arrayDepth >= maxArrayDepth)}>{title}</option>)}</select></label>{field.items?.type === "object" ? fields([...path, "$items"], field.items, depth + 1, arrayDepth + 1) : field.items?.type === "array" ? detail([...path, "$items"], field.items, depth + 1, arrayDepth + 1) : <><label>列表项说明<input value={String(field.items?.description ?? "")} onChange={e => patch(path, { ...field, items: { ...field.items, description: e.target.value } })} /></label>{detail([...path,"$items"],field.items??{type:"string",maxLength:2000},depth+1,arrayDepth+1)}</>}</div>}</details>;
  }
  function fields(path: string[], node: FieldSchema, depth: number, arrayDepth: number) {
    const entries = Object.entries(node.properties ?? {});
    return <div className={`schema-fields schema-depth-${depth}`}>
      <div className="schema-field-header"><span>{path.length ? "对象字段" : label}</span><button type="button" className="text-button" disabled={entries.length >= 40} onClick={() => add(path, node)}><Plus size={14} />{path.length ? "添加子字段" : mode === "input" ? "添加参数" : "添加输出字段"}</button></div>
      {!entries.length && <p className="muted small">{path.length ? "添加这个对象包含的字段。" : mode === "input" ? "无需参数可直接继续。" : "添加允许返回的业务字段。"}</p>}
      {entries.map(([name, field], index) => {
        const fieldPath = [...path, name], full = fieldPath.filter(key => key !== "$items").join(".");
        const suffix = path.length ? full : String(index + 1);
        const type = typeof field.type === "string" ? field.type : "complex";
        return <div className="schema-field-card" key={index}>
          <div className="schema-field-row"><label>字段名称 <em>*</em><input aria-label={mode === "input" ? `参数名称 ${suffix}` : `输出字段名 ${full}`} value={name} onChange={e => rename(path, node, name, e.target.value)} placeholder="例如 customer_id" /></label>
            <label>说明<input aria-label={`${label}字段说明 ${full}`} value={String(field.description ?? "")} onChange={e => patch(fieldPath, { ...field, description: e.target.value })} placeholder="例如：客户编号" /></label>
            <label>类型<select aria-label={mode === "input" ? `参数类型 ${suffix}` : `数据类型 ${full}`} value={type} onChange={e => patch(fieldPath, newField(e.target.value, field))}><option value="complex" disabled>现有组合类型</option>{kinds.map(([kind, title]) => <option key={kind} value={kind} disabled={kind === "object" && depth + 1 > maxObjectDepth || kind === "array" && (depth + 1 > maxArrayDepth || arrayDepth >= maxArrayDepth)}>{title}</option>)}</select></label>
            <label className="checkbox schema-required"><input aria-label={mode === "input" ? `参数必填 ${suffix}` : `输出必填 ${full}`} type="checkbox" checked={(node.required ?? []).includes(name)} onChange={e => patch(path, { ...node, required: e.target.checked ? [...(node.required ?? []), name] : (node.required ?? []).filter(key => key !== name) })} />必填</label>
            <button type="button" className="icon-button danger-link" aria-label={mode === "input" ? `删除参数 ${suffix}` : `移除输出字段 ${full}`} onClick={() => remove(path, node, name)}><Trash2 size={15} /></button>
          </div>
          {field.type === "object" ? <details className="nested-fields" open><summary><ChevronDown size={14} />配置对象字段</summary>{fields(fieldPath, field, depth + 1, arrayDepth)}{detail(fieldPath,field,depth+1,arrayDepth)}</details> : detail(fieldPath, field, depth + 1, arrayDepth)}
        </div>;
      })}
    </div>;
  }
  return <div className="schema-field-editor">{error && <p className="field-error" role="alert">{error}</p>}{fields([], root, 0, 0)}</div>;
}
