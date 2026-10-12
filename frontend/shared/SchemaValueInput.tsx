

type FieldSchema=Record<string,unknown>&{type?:string|string[];properties?:Record<string,FieldSchema>;required?:string[];items?:FieldSchema};

type ValueInputProps = { field: FieldSchema; value: unknown; name: string; required: boolean; change: (value: unknown) => void; labelPrefix?:string };
function initialValue(field: FieldSchema): unknown { if (Array.isArray(field.examples) && field.examples.length) return field.examples[0]; return field.type === "object" ? {} : field.type === "array" ? [] : field.type === "boolean" ? false : ["number", "integer"].includes(String(field.type)) ? 0 : ""; }
export function ValueInput({ field, value, name, required, change, labelPrefix="测试参数" }: ValueInputProps) {
  const title = String(field.title || field.description || name.split(".").at(-1));
  if (field.type === "object") {
    const data = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
    return <section className="business-mapping-card"><strong>{title}{required ? " *" : ""}</strong><div className="business-mapping-grid">{Object.entries(field.properties ?? {}).map(([key, child]) => <ValueInput key={key} labelPrefix={labelPrefix} field={child} value={data[key]} name={`${name}.${key}`} required={(field.required ?? []).includes(key)} change={next => { const all = { ...data }; if (next === undefined) delete all[key]; else all[key] = next; change(all); }} />)}</div></section>;
  }
  if (field.type === "array") {
    const items = Array.isArray(value) ? value : [], item = field.items ?? { type: "string" };
    return <section className="business-mapping-card"><div className="function-test-result-header"><strong>{title}{required ? " *" : ""}</strong><button type="button" className="text-button" disabled={items.length >= Number(field.maxItems ?? 100)} onClick={() => change([...items, initialValue(item)])}>添加一项</button></div>{items.map((entry, index) => <div className="array-item-editor" key={index}><ValueInput labelPrefix={labelPrefix} field={item} value={entry} name={`${name}.${index}`} required change={next => change(items.map((value, i) => i === index ? next : value))} /><button type="button" className="text-button danger-link" aria-label={`删除测试列表项 ${name}.${index}`} onClick={() => change(items.filter((_, i) => i !== index))}>移除</button></div>)}</section>;
  }
  const text = value === undefined ? "" : String(value);
  const choices: unknown[] | null = Array.isArray(field.enum) ? field.enum : null;
  return <label className="field">{title}{required && <em>*</em>}{choices ? <select required={required} aria-label={`${labelPrefix} ${name}`} value={text} onChange={e => change(e.target.value === "" ? undefined : choices.find(value => String(value) === e.target.value))}><option value="">请选择</option>{choices.map((value, index) => <option key={index} value={String(value)}>{String(value)}</option>)}</select> : field.type === "boolean" ? <select required={required} aria-label={`${labelPrefix} ${name}`} value={text} onChange={e => change(e.target.value === "" ? undefined : e.target.value === "true")}><option value="">未填写</option><option value="true">是</option><option value="false">否</option></select> : <input required={required} aria-label={`${labelPrefix} ${name}`} type={["number", "integer"].includes(String(field.type)) ? "number" : field.format === "date" ? "date" : "text"} step={field.type === "integer" ? 1 : "any"} value={text} placeholder={Array.isArray(field.examples) && field.examples.length ? String(field.examples[0]) : required ? "必填参数" : "可选参数"} onChange={e => change(e.target.value === "" ? undefined : ["number", "integer"].includes(String(field.type)) ? Number(e.target.value) : e.target.value)} />}</label>;
}
